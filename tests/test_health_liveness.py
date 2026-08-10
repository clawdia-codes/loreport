#!/usr/bin/env python3
"""Tests for the three liveness states in scripts/loreport-health (P5).

The health check used to emit one failure line — `Loreport health FAIL: merge
digest needs review` — for three unrelated conditions: a secret-scrub abort that
merged nothing, a quarantine block waiting on a human, and a PROFILE conflict.
On 2026-08-10 two readers took that line to mean the merge had failed and told
the owner the brain had been stuck for three days. It had merged and published
every night.

These tests drive the real script as a subprocess against a synthetic brain, and
assert on the two things a human actually sees: the banner file and the notifier
payload. Everything the script touches is redirected into the temp tree — in
particular LOREPORT_BANNER, because the healthy path runs `rm -f "$BANNER"` and
the default location is the operator's own live banner.
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
ENGINE = os.path.dirname(HERE)
HEALTH = os.path.join(ENGINE, "scripts", "loreport-health")

CLEAN_DIGEST = """# Hub digest — 2026-08-10

- Backup tag: pre-merge/20260810-000000
- Merged: provider/openclaw -> main
- PROFILE conflicts (human review required): none
- Secret-scrub: PASS
- Quarantine items pending review: 0
- INDEX rebuilt: 1 items (1 memories, 0 knowledge, 0 skills)
"""


class HealthHarness:
    """A synthetic brain that is green on every check, so each test can turn
    exactly one condition red and attribute the result to it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loreport-health-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.brain = os.path.join(self.tmp, "brain")
        self.home = os.path.join(self.tmp, "home")
        self.banner = os.path.join(self.tmp, "banner")
        self.notify_log = os.path.join(self.tmp, "notifications")
        os.makedirs(os.path.join(self.brain, "hub"))
        os.makedirs(os.path.join(self.brain, "memories"))
        os.makedirs(self.home)

        subprocess.run(["git", "init", "-q", "-b", "main", self.brain], check=True)
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "test")
        self.write("INDEX.md", "# Index\n\n- [[seed]] — seed\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "seed")

        # A manifest with no targets keeps checks 3-4 green without pretending
        # a projection ran.
        self.write("hub/projection-manifest.json",
                   json.dumps({"targets": [], "projected_at": "2026-08-10"}))
        self.write_digest(CLEAN_DIGEST)
        self.set_merge_age(hours=2)

        # Stubs, prepended to PATH rather than replacing it: the script still
        # needs the real git, python3 and date.
        self.stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(self.stubs)
        self._script(os.path.join(self.stubs, "systemctl"), "echo success\n")
        self.notifier = os.path.join(self.tmp, "notify")
        self._script(self.notifier, f'printf "%s\\n" "$*" >> "{self.notify_log}"\n')

        self.config = os.path.join(self.tmp, "loreport.conf")
        with open(self.config, "w", encoding="utf-8") as fh:
            fh.write(
                f'LOREPORT_BRAIN="{self.brain}"\n'
                f'LOREPORT_ENGINE="{ENGINE}"\n'
                f'LOREPORT_BANNER="{self.banner}"\n'
                f'LOREPORT_NOTIFY="{self.notifier}"\n'
            )

    # --- helpers ---------------------------------------------------------

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.brain, *args],
                              capture_output=True, text=True, check=True)

    def _script(self, path, body):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

    def write(self, rel, text):
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def write_digest(self, text):
        self.write("hub/digest-2026-08-10.md", text)

    def set_merge_age(self, hours):
        """Fake the last-success timestamp — the hook the freshness budget is
        specified to be testable through."""
        self.write("hub/merge-state.json", json.dumps(
            {"last_success_epoch": int(time.time()) - int(hours * 3600)}))

    def drop_merge_state(self):
        os.remove(os.path.join(self.brain, "hub", "merge-state.json"))

    def run_health(self, extra_env=None):
        env = dict(os.environ)
        env["PATH"] = self.stubs + os.pathsep + env["PATH"]
        env["HOME"] = self.home
        if extra_env:
            env.update(extra_env)
        return subprocess.run(["bash", HEALTH, "--config", self.config],
                              capture_output=True, text=True, env=env)

    def banner_text(self):
        if not os.path.isfile(self.banner):
            return None
        with open(self.banner, encoding="utf-8") as fh:
            return fh.read()

    def notifications(self):
        if not os.path.isfile(self.notify_log):
            return []
        with open(self.notify_log, encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]


class BaselineIsGreen(HealthHarness, unittest.TestCase):

    def test_clean_brain_exits_zero_and_removes_the_banner(self):
        # Seed a banner from an imagined earlier bad run: with no banner on
        # disk, "removes the banner" is unfalsifiable — the assertion passes
        # whether or not the script ever deletes anything.
        with open(self.banner, "w", encoding="utf-8") as fh:
            fh.write("=== Loreport health (stale) ===\n✗ something old\n")
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.banner_text(),
                          "a healthy run left a stale failure banner in place")


class ChecksDoNotCrashSilently(HealthHarness, unittest.TestCase):

    def test_empty_index_does_not_crash_the_capture_check(self):
        """`grep -c` prints 0 and exits 1 on no match, so the old fallback
        produced "0\\n0". The script runs without -e, so the resulting Python
        ValueError was invisible: the check died and the run still reported
        healthy. A liveness check that can crash without saying so is the exact
        failure mode this sprint exists to remove."""
        self.write("INDEX.md", "# Index\n\nno items yet\n")
        result = self.run_health()
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)


class QuarantineIsReviewNotFailure(HealthHarness, unittest.TestCase):
    """Requirement 3: pending items must not read as, or be graded as, a
    failure."""

    def setUp(self):
        super().setUp()
        self.write_digest(CLEAN_DIGEST.replace(
            "Quarantine items pending review: 0",
            "Quarantine items pending review: 2"))

    def test_pending_quarantine_does_not_fail_the_check(self):
        result = self.run_health()
        self.assertEqual(result.returncode, 0,
                         f"a pending quarantine was graded as a failure: {result.stderr}")

    def test_banner_files_it_under_review_not_under_failing(self):
        self.run_health()
        banner = self.banner_text()
        self.assertIsNotNone(banner, "a pending review item left no banner at all")
        self.assertIn("NEEDS REVIEW", banner)
        self.assertNotIn("FAILING", banner)
        self.assertNotIn("✗", banner)

    def test_message_names_the_subsystem_and_says_the_merge_completed(self):
        self.run_health()
        banner = self.banner_text()
        self.assertIn("merge quarantine", banner)
        self.assertIn("2 block(s)", banner)
        self.assertIn("completed normally", banner)
        # The exact string that caused the false diagnosis must be gone.
        self.assertNotIn("merge digest needs review", banner)

    def test_notification_does_not_say_FAIL(self):
        self.run_health()
        notes = self.notifications()
        self.assertFalse([n for n in notes if "FAIL" in n],
                         f"a pending review item was announced as a failure: {notes}")
        review = [n for n in notes if "awaiting your review" in n]
        self.assertEqual(len(review), 1, notes)
        self.assertIn("merge and publish are running", review[0])

    def test_heartbeat_still_fires_while_an_item_is_pending(self):
        """A quarantine count never falls without a human. If review suppressed
        the weekly heartbeat, one un-triaged block would silence the brain's
        only proof-of-life indefinitely."""
        self.run_health()
        self.assertTrue(any("brain OK" in n for n in self.notifications()),
                        f"no heartbeat while an item was pending review: {self.notifications()}")


class FreshnessBudget(HealthHarness, unittest.TestCase):
    """Requirement 2: nothing asserted merge freshness before this."""

    def test_merge_older_than_the_budget_is_a_failure(self):
        self.set_merge_age(hours=48)
        result = self.run_health()
        self.assertEqual(result.returncode, 1)
        banner = self.banner_text()
        self.assertIn("merge liveness", banner)
        self.assertIn("48h ago", banner)
        self.assertIn("budget 36h", banner)

    def test_merge_inside_the_budget_is_green(self):
        self.set_merge_age(hours=30)
        self.assertEqual(self.run_health().returncode, 0)

    def test_budget_is_configurable(self):
        self.set_merge_age(hours=10)
        result = self.run_health(extra_env={"LOREPORT_MERGE_MAX_AGE_HOURS": "8"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("budget 8h", self.banner_text())

    def test_never_merged_is_a_different_message_from_gone_stale(self):
        """Two conditions, two fixes: 'it has never run here' is not 'it stopped
        running', and telling them apart is the difference between a first-run
        note and an outage."""
        self.drop_merge_state()
        result = self.run_health()
        self.assertEqual(result.returncode, 1)
        banner = self.banner_text()
        self.assertIn("no completed merge on record", banner)
        self.assertNotIn("ago, budget", banner)


class BrokenIsStillBroken(HealthHarness, unittest.TestCase):
    """The split must not soften the one state that really is a failure."""

    def test_secret_scrub_abort_fails_and_names_the_subsystem(self):
        self.write_digest(CLEAN_DIGEST.replace(
            "Secret-scrub: PASS",
            "Secret-scrub: ABORT: memories/leaky.md: sk-abc… blocked"))
        result = self.run_health()
        self.assertEqual(result.returncode, 1)
        banner = self.banner_text()
        self.assertIn("secret-scrub", banner)
        self.assertIn("nothing merged", banner)
        self.assertNotIn("merge digest needs review", banner)
        self.assertIn("FAIL", self.notifications()[0])

    def test_a_failure_and_a_review_item_are_reported_separately(self):
        self.write_digest(CLEAN_DIGEST
                          .replace("Secret-scrub: PASS", "Secret-scrub: FAIL")
                          .replace("Quarantine items pending review: 0",
                                   "Quarantine items pending review: 1"))
        result = self.run_health()
        self.assertEqual(result.returncode, 1)
        banner = self.banner_text()
        self.assertIn("FAILING", banner)
        self.assertIn("NEEDS REVIEW", banner)
        self.assertIn("secret-scrub", banner)
        self.assertIn("merge quarantine", banner)


class EveryDigestConditionIsReported(HealthHarness, unittest.TestCase):

    def test_quarantine_and_profile_conflict_both_surface(self):
        """These used to be an elif chain, so the second condition was invisible
        while the first held — and the first is the one that never clears on its
        own."""
        self.write_digest(CLEAN_DIGEST
                          .replace("PROFILE conflicts (human review required): none",
                                   "PROFILE conflicts (human review required): ['PROFILE.md']")
                          .replace("Quarantine items pending review: 0",
                                   "Quarantine items pending review: 3"))
        self.run_health()
        banner = self.banner_text()
        self.assertIn("merge quarantine", banner)
        self.assertIn("PROFILE conflict", banner)


class AlertOnStateChangeOnly(HealthHarness, unittest.TestCase):
    """Requirement 4: no spam. A weekly line that repeats forever is a line
    nobody reads."""

    def test_identical_state_notifies_once(self):
        self.set_merge_age(hours=48)
        self.run_health()
        self.run_health()
        self.run_health()
        fails = [n for n in self.notifications() if "FAIL" in n]
        self.assertEqual(len(fails), 1, self.notifications())

    def test_a_new_condition_breaks_through_the_silence(self):
        self.set_merge_age(hours=48)
        self.run_health()
        self.write_digest(CLEAN_DIGEST.replace("Secret-scrub: PASS", "Secret-scrub: FAIL"))
        self.run_health()
        fails = [n for n in self.notifications() if "FAIL" in n]
        self.assertEqual(len(fails), 2, self.notifications())

    def test_recovery_is_announced(self):
        self.set_merge_age(hours=48)
        self.run_health()
        self.set_merge_age(hours=1)
        self.run_health()
        self.assertTrue(any("recovered" in n for n in self.notifications()),
                        f"the brain came back and nobody was told: {self.notifications()}")

    def test_a_first_run_on_a_healthy_brain_does_not_claim_a_recovery(self):
        """There is no recorded prior state on a new brain, so there is nothing
        to have recovered from. Announcing one makes the word meaningless the
        first time it is ever used."""
        self.run_health()
        self.assertFalse([n for n in self.notifications() if "recovered" in n],
                         f"a brand-new healthy brain announced a recovery: {self.notifications()}")


class PendingItemsStayVisible(HealthHarness, unittest.TestCase):
    """A review item that only appears on some runs is worse than one that never
    appears: the runs in between report a state the brain is not in."""

    def setUp(self):
        super().setUp()
        surface = "<!-- loreport:begin -->\ncurrent content\n<!-- loreport:end -->\n"
        self.write("hub/surface-chatgpt.md", surface)
        self.write("hub/health-state.json", json.dumps(
            {"chatgpt_pasted": {"region_hash": "sha256:deliberately-not-the-current-hash",
                                "recorded_epoch": int(time.time()) - 90 * 86400}}))

    def test_an_unpasted_surface_is_reported_on_every_run(self):
        first = self.run_health()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("ChatGPT surface", self.banner_text() or "")

        second = self.run_health()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("ChatGPT surface", self.banner_text() or "",
                      "the pending paste vanished from the banner on the second run")

    def test_a_still_pending_item_never_reports_a_recovery(self):
        self.run_health()
        self.run_health()
        self.run_health()
        self.assertFalse([n for n in self.notifications() if "recovered" in n],
                         f"reported all-green while an item was still pending: {self.notifications()}")


class EveryNeedsReviewReasonReachesTheOwner(HealthHarness, unittest.TestCase):
    """`brain_merge.needs_review_reasons()` has FIVE members. Only two of them
    put anything in a line section 6b greps: `profile_conflicts` (the digest's
    "PROFILE conflicts … none") and `human_region_violations` (they write files
    under hub/quarantine/, so the count line catches them).

    The other three — a renamed add/add twin, a secret-scrub warning on a
    `visibility: local` item, a reverted provenance violation — write no
    quarantine file and appear in no line 6b reads. Measured on the branch as
    delivered: the merge exited 3, and this script then computed state=ok,
    DELETED the banner and pushed `🧠 brain OK — 2 memories`. An unremediated
    secret sitting in the brain announced itself as healthy.

    hub/merge-state.json already carried `needs_review`; nothing read it.
    """

    def set_kinds(self, *kinds, **extra):
        state = {"last_success_epoch": int(time.time()) - 2 * 3600,
                 "needs_review": bool(kinds),
                 "needs_review_kinds": list(kinds)}
        state.update(extra)
        self.write("hub/merge-state.json", json.dumps(state))

    def test_a_scrub_warning_on_a_local_item_reaches_the_banner(self):
        """The worst of the three: a secret is sitting in the brain.

        MUTATION: delete the `while IFS= read -r kind` loop in section 6c
        (leave the `merge_review_kinds` capture in place).
        Observed: 5 failed — this test, `..._a_rename_...`,
        `..._a_provenance_violation_...`,
        `..._an_unknown_future_kind_is_still_reported` and
        `..._a_pre_upgrade_merge_state_still_says_something`.
        """
        self.set_kinds("scrub_warnings")
        result = self.run_health()
        banner = self.banner_text()
        self.assertIsNotNone(banner, "a scrub warning left no banner at all")
        self.assertIn("NEEDS REVIEW", banner)
        self.assertIn("secret-scrub", banner)
        self.assertIn("still sitting in the brain", banner)
        # It is review, not failure: nothing egressed and the merge published.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("FAILING", banner)
        # And it reaches the phone, not just the banner file. (The weekly
        # heartbeat still fires alongside it — deliberately; see
        # `test_heartbeat_still_fires_while_an_item_is_pending`.)
        review = [n for n in self.notifications() if "awaiting your review" in n]
        self.assertEqual(len(review), 1, self.notifications())
        self.assertIn("secret-scrub", review[0])

    def test_a_rename_reaches_the_banner(self):
        """Same mutation as above."""
        self.set_kinds("renamed")
        self.run_health()
        banner = self.banner_text()
        self.assertIsNotNone(banner, "an add/add rename left no banner at all")
        self.assertIn("add/add rename", banner)
        self.assertIn("completed normally", banner)

    def test_a_provenance_violation_reaches_the_banner(self):
        """The cross-provider tamper gate. It reverts silently, so if the revert
        itself is never announced nobody learns a provider is misbehaving.
        Same mutation as above."""
        self.set_kinds("provenance_violations")
        self.run_health()
        banner = self.banner_text()
        self.assertIsNotNone(banner, "a provenance revert left no banner at all")
        self.assertIn("provenance", banner)
        self.assertIn("was reverted", banner)

    def test_an_unknown_future_kind_is_still_reported(self):
        """6c iterates whatever kinds are present instead of testing a fixed
        list, so a sixth reason added to brain_merge surfaces the day it lands.
        Same mutation as above."""
        self.set_kinds("some_future_subsystem")
        self.run_health()
        banner = self.banner_text()
        self.assertIsNotNone(banner)
        self.assertIn("some_future_subsystem", banner)

    def test_a_kind_the_digest_already_named_is_not_reported_twice(self):
        """6b says quarantine and PROFILE in the digest's own words. 6c must not
        repeat them, or the two most common conditions double every banner.

        MUTATION: delete the
        `case " ${digest_reported_kinds:-} " in *" $kind "*) continue ;; esac`
        line in 6c.
        Observed: 1 failed — this test (2 != 1).
        """
        self.write_digest(CLEAN_DIGEST.replace(
            "Quarantine items pending review: 0",
            "Quarantine items pending review: 2"))
        self.set_kinds("human_region_violations")
        self.run_health()
        banner = self.banner_text()
        entries = [ln for ln in banner.splitlines() if ln.startswith("⧗")]
        self.assertEqual(len(entries), 1, banner)
        self.assertIn("merge quarantine", entries[0])

    def test_no_kinds_means_no_review_line(self):
        """The positive control. Without it every assertion above could pass on
        a script that files everything under NEEDS REVIEW unconditionally."""
        self.set_kinds()
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.banner_text())
        self.assertTrue(any("brain OK" in n for n in self.notifications()),
                        self.notifications())

    def test_a_pre_upgrade_merge_state_still_says_something(self):
        """A brain whose last merge ran under an engine older than the kinds
        field has `needs_review: true` and no list. Saying nothing there would
        reintroduce the exact hole, so it reports that it cannot name the
        subsystem — and says why.

        MUTATION: in 6c's python block, delete the
        `elif data.get("needs_review"):` branch.
        Observed: 1 failed — this test.
        """
        self.write("hub/merge-state.json", json.dumps(
            {"last_success_epoch": int(time.time()) - 2 * 3600,
             "needs_review": True}))
        self.run_health()
        banner = self.banner_text()
        self.assertIsNotNone(banner, "a pre-upgrade needs_review left no banner")
        self.assertIn("could not name the subsystem", banner)

    def test_an_old_merge_state_with_no_needs_review_field_is_silent(self):
        """The other half of the upgrade window: merge-state files written
        before either field existed must not start reporting a phantom review
        item on every run."""
        self.write("hub/merge-state.json", json.dumps(
            {"last_success_epoch": int(time.time()) - 2 * 3600}))
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.banner_text())


class MergeStateRecordsWhichSubsystemIsWaiting(unittest.TestCase):
    """The writer half. `write_merge_state` must stamp the subsystem KEYS, and
    only the keys — `scrub_warnings`' payload carries the matched secret
    fragment, and merge-state.json is untracked-but-not-gitignored in every
    brain created before this version."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ENGINE, "hub"))
        import brain_merge
        self.brain_merge = brain_merge
        self.tmp = tempfile.mkdtemp(prefix="loreport-mergestate-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.makedirs(os.path.join(self.tmp, "hub"))

    def state(self, report):
        path = self.brain_merge.write_merge_state(self.tmp, report)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_scrub_warning_is_recorded_by_name(self):
        """MUTATION: in `write_merge_state`, hardcode
        `"needs_review_kinds": []`.
        Observed: 2 failed — this test and
        `test_several_waiting_subsystems_are_all_recorded`.
        """
        s = self.state({"scrub_warnings": ["memories/x.md: sk-abc…"]})
        self.assertEqual(s["needs_review_kinds"], ["scrub_warnings"])
        self.assertTrue(s["needs_review"])

    def test_the_payload_is_never_written_only_the_key(self):
        """The reason it is keys and not reasons: the payload is a secret."""
        s = self.state({"scrub_warnings": ["memories/x.md: sk-abcdefghij0123456789"]})
        self.assertNotIn("sk-abcdefghij", json.dumps(s))

    def test_several_waiting_subsystems_are_all_recorded(self):
        """Reporting only the first is how three of five went missing to begin
        with. Same mutation as above."""
        s = self.state({"renamed": ["a -> b"], "provenance_violations": ["c"]})
        self.assertEqual(sorted(s["needs_review_kinds"]),
                         ["provenance_violations", "renamed"])

    def test_a_quiet_merge_records_no_kinds(self):
        s = self.state({})
        self.assertEqual(s["needs_review_kinds"], [])
        self.assertFalse(s["needs_review"])


if __name__ == "__main__":
    unittest.main()
