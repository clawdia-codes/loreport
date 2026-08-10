#!/usr/bin/env python3
"""Tests for the merge's three liveness states (P5).

The bug these exist to prevent is not a crash — it is a misreading. The merge
used to exit 1 for two unrelated outcomes: "nothing merged, main was rolled
back" and "everything merged, some items are parked for you". On 2026-08-10 two
readers saw the second and reported the first, telling the owner the brain had
been stuck for three days when it had merged and published every night.

So these tests assert the distinction directly:

  * a quarantine event must NOT stop the merge or the publish (requirement 1),
  * a completed merge must stamp hub/merge-state.json, and a fail-closed abort
    must NOT — that stamp is the only thing asserting the pipeline is alive,
  * the two nonzero exits must be different numbers.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.join(os.path.dirname(HERE), "hub")
sys.path.insert(0, HUB)

import brain_merge  # noqa: E402

ITEM = """---
name: {name}
description: {name} for the liveness tests
type: project
visibility: shared
source: openclaw
captured: 2026-08-10
---

{body}
"""

HUMAN_BODY = "intro\n<!-- human:start -->the owner's own words<!-- human:end -->\noutro"


class MergeLivenessHarness:
    """A real brain-shaped git repo. Not a TestCase, so subclasses do not
    silently re-run each other's assertions."""

    def setUp(self):
        self.brain = tempfile.mkdtemp(prefix="loreport-liveness-")
        self.addCleanup(shutil.rmtree, self.brain, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.brain], check=True)
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        os.makedirs(os.path.join(self.brain, "memories"))
        os.makedirs(os.path.join(self.brain, "prompts"))
        # Mirror the brain template's ignores. Without them a provider branch's
        # `git add -A` would pick up the local report artifacts and carry them
        # through the merge, which no real brain does.
        self.write(".gitignore", "hub/quarantine/*\nhub/digest-*.md\nhub/merge-state.json\n")
        self.write("prompts/bootstrap.md", "# bootstrap\n")
        self.write("PROFILE.md", "# profile\n")
        self.write("memories/protected.md", ITEM.format(name="protected", body=HUMAN_BODY))
        index, _, _, _ = brain_merge.build_index_bytes(self.brain)
        with open(os.path.join(self.brain, "INDEX.md"), "wb") as fh:
            fh.write(index)
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", self.brain, *args],
                              capture_output=True, text=True, check=check)

    def write(self, rel, text):
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, rel):
        with open(os.path.join(self.brain, rel), encoding="utf-8") as fh:
            return fh.read()

    def merge_state(self):
        path = os.path.join(self.brain, brain_merge.MERGE_STATE_FILE)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def seed_stale_merge_state(self, age_hours):
        """Fake an old last-success timestamp — the hook the freshness budget is
        tested through, here used to prove an abort cannot refresh it."""
        path = os.path.join(self.brain, brain_merge.MERGE_STATE_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = int(time.time()) - age_hours * 3600
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"last_success_epoch": stamp}, fh)
        return stamp

    def force_quarantine(self):
        """Trigger the REAL quarantine path: a provider branch that strips the
        human region from a protected file. Hand-placing a file under
        hub/quarantine/ would only exercise the counter, not the guard."""
        self.git("checkout", "-qb", "provider/openclaw")
        self.write("memories/protected.md",
                   ITEM.format(name="protected", body="agent rewrote the whole body"))
        self.git("add", "-A")
        self.git("commit", "-qm", "provider edit\n\nTrust: local")
        self.git("checkout", "-q", "main")


class QuarantineDoesNotGateLiveness(MergeLivenessHarness, unittest.TestCase):

    def test_quarantine_lets_merge_and_publish_complete(self):
        """Requirement 1, end to end: force a quarantine, then prove main
        advanced, the packet was published from it, and liveness was stamped."""
        self.force_quarantine()
        head_before = self.git("rev-parse", "main").stdout.strip()

        with self.assertRaises(SystemExit) as ctx:
            brain_merge.do_merge(self.brain, dry_run=False)

        # It is NEEDS REVIEW, not BROKEN. Same nonzero-ness, opposite meaning.
        self.assertEqual(ctx.exception.code, brain_merge.EXIT_NEEDS_REVIEW)
        self.assertNotEqual(brain_merge.EXIT_NEEDS_REVIEW, brain_merge.EXIT_BROKEN)

        # The quarantine actually happened.
        qroot = os.path.join(self.brain, "hub", "quarantine")
        parked = [f for _r, _d, files in os.walk(qroot) for f in files
                  if f not in brain_merge.QUARANTINE_NON_ITEMS]
        self.assertTrue(parked, "the human-region guard did not park anything")
        self.assertIn("the owner's own words", self.read("memories/protected.md"))

        # ...and the merge still completed: main moved, the tree is clean on main.
        self.assertNotEqual(self.git("rev-parse", "main").stdout.strip(), head_before)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").stdout.strip(), "main")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no").stdout.strip(), "")

        # ...and publish runs off that main and produces a packet.
        pub = subprocess.run(
            [sys.executable, os.path.join(HUB, "snapshot_publish.py"), "--brain-dir", self.brain],
            capture_output=True, text=True,
        )
        self.assertEqual(pub.returncode, 0, f"publish failed after a quarantine: {pub.stderr}")
        self.assertTrue(os.path.isfile(os.path.join(self.brain, "hub", "published", "packet.md")))

        # ...and the run counts as alive. A quarantine count never falls without
        # a human, so if this did not stamp, one parked block would make the
        # brain look permanently dead.
        state = self.merge_state()
        self.assertIsNotNone(state, "a completed merge did not stamp hub/merge-state.json")
        self.assertLess(abs(int(time.time()) - state["last_success_epoch"]), 300)
        self.assertTrue(state["needs_review"])

    def test_quiet_successful_merge_stamps_liveness(self):
        """A no-op night is a live night: nothing to merge is not the same as
        nothing running, and the freshness budget must not confuse them."""
        report = brain_merge.do_merge(self.brain, dry_run=False)
        self.assertTrue(report.get("noop"))
        state = self.merge_state()
        self.assertIsNotNone(state, "a no-op merge left no liveness stamp")
        self.assertLess(abs(int(time.time()) - state["last_success_epoch"]), 300)
        self.assertFalse(state["needs_review"])

    def test_dry_run_does_not_stamp_liveness(self):
        """--dry-run plans and prints; claiming a successful merge from it would
        let a human hide a dead pipeline by running the planner."""
        brain_merge.do_merge(self.brain, dry_run=True)
        self.assertIsNone(self.merge_state())


class BrokenMergeDoesNotLookAlive(MergeLivenessHarness, unittest.TestCase):

    def test_fail_closed_abort_does_not_refresh_liveness(self):
        """The whole point of the stamp: a merge that aborts must leave the old
        timestamp alone so the freshness budget starts counting against it."""
        stale = self.seed_stale_merge_state(age_hours=100)

        self.git("checkout", "-qb", "provider/openclaw")
        self.write("memories/leaky.md", ITEM.format(
            name="leaky", body='api_key: "sk-abcdefghijklmnopqrstuvwx1234567890"'))
        self.git("add", "-A")
        self.git("commit", "-qm", "capture\n\nTrust: local")
        self.git("checkout", "-q", "main")

        with self.assertRaises(SystemExit) as ctx:
            brain_merge.do_merge(self.brain, dry_run=False)

        self.assertEqual(ctx.exception.code, brain_merge.EXIT_BROKEN)
        self.assertEqual(self.merge_state()["last_success_epoch"], stale,
                         "an aborted merge refreshed the liveness stamp")


class QuarantineCounting(MergeLivenessHarness, unittest.TestCase):

    def test_gitkeep_marker_is_not_a_pending_item(self):
        """.gitkeep is the tracked directory marker the brain's .gitignore keeps.
        Counting it reports one pending item forever, and a count that can never
        reach zero pins the health check into NEEDS REVIEW permanently."""
        qdir = os.path.join(self.brain, "hub", "quarantine")
        os.makedirs(qdir, exist_ok=True)
        open(os.path.join(qdir, ".gitkeep"), "w").close()
        self.assertEqual(brain_merge.count_quarantine_items(self.brain), 0)

    def test_a_real_parked_block_is_counted(self):
        qdir = os.path.join(self.brain, "hub", "quarantine", "provider-openclaw")
        os.makedirs(qdir, exist_ok=True)
        open(os.path.join(qdir, "parked-block.md"), "w").close()
        self.assertEqual(brain_merge.count_quarantine_items(self.brain), 1)


class SyncKeepsGoingWhenItemsNeedReview(unittest.TestCase):
    """The other half of requirement 1. The merge-side test proves brain_merge
    exits 3 and a packet can still be built; this proves the DRIVER acts on
    that — that `bin/loreport-sync` publishes, projects and pushes after a
    needs-review merge instead of halting, and does not push a failure-shaped
    line to the owner's phone every night while an item sits un-triaged.

    The engine is stubbed rather than run for real: what is under test is the
    script's branching on the merge's exit code, and a stub is the only way to
    hold that code fixed."""

    SYNC = os.path.join(os.path.dirname(HERE), "bin", "loreport-sync")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loreport-sync-liveness-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.brain = os.path.join(self.tmp, "brain")
        self.engine = os.path.join(self.tmp, "engine")
        self.marker = os.path.join(self.tmp, "published-marker")
        self.notify_log = os.path.join(self.tmp, "notifications")
        os.makedirs(os.path.join(self.engine, "hub"))

        remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.brain], check=True)
        for k, v in (("user.email", "test@example.invalid"), ("user.name", "test")):
            subprocess.run(["git", "-C", self.brain, "config", k, v], check=True)
        subprocess.run(["git", "-C", self.brain, "remote", "add", "origin", remote], check=True)
        with open(os.path.join(self.brain, "PROFILE.md"), "w", encoding="utf-8") as fh:
            fh.write("# profile\n")
        subprocess.run(["git", "-C", self.brain, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.brain, "commit", "-qm", "seed"], check=True)

        self._py("hub/report_build.py", "raise SystemExit(0)")
        self._py("hub/project.py", "raise SystemExit(0)")
        self._py("hub/snapshot_publish.py",
                 f"open({self.marker!r}, 'w').close()\nraise SystemExit(0)")

        self.notifier = os.path.join(self.tmp, "notify")
        with open(self.notifier, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.notify_log}"\n')
        os.chmod(self.notifier, 0o755)

        self.config = os.path.join(self.tmp, "loreport.conf")
        with open(self.config, "w", encoding="utf-8") as fh:
            fh.write(f'LOREPORT_BRAIN="{self.brain}"\n'
                     f'LOREPORT_ENGINE="{self.engine}"\n'
                     f'LOREPORT_NOTIFY="{self.notifier}"\n')

    def _py(self, rel, body):
        with open(os.path.join(self.engine, rel), "w", encoding="utf-8") as fh:
            fh.write(body + "\n")

    def run_sync(self, merge_exit):
        self._py("hub/brain_merge.py", f"raise SystemExit({merge_exit})")
        return subprocess.run(["bash", self.SYNC, "--config", self.config],
                              capture_output=True, text=True)

    def notifications(self):
        if not os.path.isfile(self.notify_log):
            return []
        with open(self.notify_log, encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]

    def test_needs_review_still_publishes_and_stays_quiet(self):
        result = self.run_sync(brain_merge.EXIT_NEEDS_REVIEW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(self.marker),
                        "a needs-review merge stopped the publish")
        self.assertEqual(self.notifications(), [],
                         "a healthy pipeline paged the owner over a pending review item")

    def test_a_broken_merge_still_publishes_but_says_so(self):
        """Exit 1 keeps its existing behaviour — the tree is clean, so the run
        continues, but the owner is told. The split must not mute this."""
        result = self.run_sync(brain_merge.EXIT_BROKEN)
        self.assertEqual(result.returncode, 0, result.stderr)
        notes = self.notifications()
        self.assertTrue([n for n in notes if "FAILED" in n or "FAIL" in n], notes)


if __name__ == "__main__":
    unittest.main()
