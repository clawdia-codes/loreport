#!/usr/bin/env python3
"""P4 — the alert must say what happened and what to do about it.

The whole alert used to be `Loreport health FAIL: merge digest needs review`.
The owner could not act on it, asked an agent, and the agent reported a healthy
pipeline as three days dead; a second agent repeated the misreading an hour
later. 1.14.0 fixed the state NAMES. These tests pin the CONTENT.

The acceptance criterion is behavioural: a reader given ONLY the alert text must
be able to state the right cause. Two payloads carry that text and both are
asserted here, with symmetric negatives —

  * the push notification, read on a phone, which is the one that got misread;
  * the banner file, which is what a session (and an agent) reads at start-up.

Each test names the single-line production mutation it reddens in its docstring;
every one of those mutations was applied and run.
"""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ENGINE, "hub"))

import brain_merge  # noqa: E402
import quarantine_report  # noqa: E402

from test_health_liveness import CLEAN_DIGEST, HealthHarness  # noqa: E402

# A capture block exactly as inbox_ingest.py parks it: the provider's bytes,
# verbatim, <MEMORY> wrapper and all.
CAPTURE_BLOCK = """<MEMORY file="memories/backup-schedule.md" action="new">
---
name: backup-schedule
type: project
visibility: local
description: Nightly restic backup of the home directory to object storage,
---
The timer fires at 03:00 and the repository password lives in the keyring.
</MEMORY>
INDEX: - [[backup-schedule]] — Nightly restic backup  (project)
"""

# What quarantine_merge_update parks: the incoming item text, no wrapper.
MERGE_UPDATE_BLOCK = """---
name: editor-setup
type: reference
visibility: shared
description: Editor configuration that a second provider tried to rewrite
---
Body that lost the human-region check.
"""

DIGEST_LOG = """# Quarantine digest

## 2026-08-10T01:02:03 — QUARANTINE (openclaw)
- file: hub/quarantine/openclaw/2026-08-10-capture-abc123.txt
- reason: schema-invalid
- detail: description: is missing its closing value

"""


def _quarantine_digest(count):
    return CLEAN_DIGEST.replace("Quarantine items pending review: 0",
                                f"Quarantine items pending review: {count}")


class RenderedDetailNamesTheItem(unittest.TestCase):
    """The module in isolation, so a failure points at the renderer rather than
    at the eight checks the health script runs around it."""

    def setUp(self):
        import shutil
        import tempfile
        self.brain = tempfile.mkdtemp(prefix="loreport-p4-")
        self.addCleanup(shutil.rmtree, self.brain, ignore_errors=True)
        self.engine = os.path.join(self.brain, "engine")
        self._park("openclaw/2026-08-10-capture-abc123.txt", CAPTURE_BLOCK)
        self._write("hub/quarantine/digest.md", DIGEST_LOG)

    def _write(self, rel, text):
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _park(self, rel, text):
        self._write(os.path.join("hub", "quarantine", rel), text)

    def render(self, digest_count=None):
        return quarantine_report.render(self.brain, self.engine, digest_count)

    def test_names_the_item_and_its_type_and_visibility(self):
        """Mutation: delete `visibility: {item['visibility'] or 'unset'} · `
        from the header line in quarantine_report.render."""
        text = self.render()
        self.assertIn("backup-schedule", text)
        self.assertIn("type: project", text)
        self.assertIn("visibility: local", text)

    def test_carries_the_items_own_description(self):
        """Mutation: delete the `description:` line from quarantine_report.render.

        Without it the alert says a block is parked but never what it is about,
        which is the state that forced a second investigation."""
        self.assertIn("Nightly restic backup of the home directory",
                      self.render())

    def test_a_long_description_is_truncated_not_dropped(self):
        """Mutation: `return text` in place of the slice in
        quarantine_report._truncate."""
        long_desc = "word " * 200
        self._park("openclaw/2026-08-10-long.txt",
                   CAPTURE_BLOCK.replace(
                       "description: Nightly restic backup of the home directory to object storage,",
                       "description: " + long_desc.strip()))
        text = self.render()
        self.assertIn("…", text)
        for line in text.splitlines():
            if "description:" in line:
                self.assertLessEqual(
                    len(line.split("description:", 1)[1].strip()),
                    quarantine_report.DESCRIPTION_MAX_CHARS + 1,
                    "an untruncated description went into the alert")

    def test_says_why_the_block_was_parked(self):
        """Mutation: drop the `reason` interpolation from the
        `parked because:` line in quarantine_report.render."""
        text = self.render()
        self.assertIn("schema-invalid", text)

    def test_every_block_gets_show_accept_and_discard(self):
        """Mutation: delete the `out.append(("discard ..."))` call in
        quarantine_report._commands.

        "Open a file and resolve it" is not an outcome. Each block must offer a
        command per decision."""
        text = self.render()
        self.assertIn("show: cat '", text)
        self.assertIn("accept", text)
        self.assertIn("discard", text)

    def test_a_capture_block_accepts_by_re_running_the_gate(self):
        """Mutation: change `--trust local` to `--trust cloud` in
        quarantine_report._commands.

        The trust tier is not cosmetic. The operator pasting this line is on
        their own machine, which is what the local tier means; under `cloud` the
        ownership check refuses every visibility:local item, so the command in
        the alert would re-quarantine exactly the blocks most likely to be
        parked and the reader would conclude the alert was wrong."""
        text = self.render()
        self.assertIn(os.path.join(self.engine, "hub", "inbox_ingest.py"), text)
        self.assertIn("openclaw", text)
        self.assertIn(f"--brain-dir '{self.brain}'", text)
        self.assertIn("--trust local", text)

    def test_a_rejected_merge_update_is_not_offered_a_paste_and_run_accept(self):
        """Mutation: make quarantine_report._commands emit the inbox_ingest
        command for every kind, not only `capture`.

        A rejected merge update copied into place by hand bypasses the secret
        scrub, the visibility classification and the provenance revert. The
        alert must refuse to hand out that one-liner and say why."""
        self._park("openclaw/2026-08-10-memories__editor-setup.md",
                   MERGE_UPDATE_BLOCK)
        text = self.render()
        block = text.split("editor-setup", 1)[1]
        self.assertIn("not automatic", block)
        self.assertIn("secret scrub", block)
        self.assertNotIn("inbox_ingest.py", block.split("discard", 1)[0])

    def test_a_digest_disk_count_mismatch_is_stated_outright(self):
        """Mutation: delete the `if digest_count is not None and digest_count
        != len(items)` branch in quarantine_report.render.

        The digest's count is committed; hub/quarantine/ is gitignored. When
        they disagree, an empty list under an "N pending" header is the
        vacuously-true shape that has shipped in this repo twice."""
        text = self.render(digest_count=4)
        self.assertIn("count mismatch", text)
        self.assertIn("4", text)

    def test_no_parked_files_at_all_says_so_rather_than_rendering_nothing(self):
        """Mutation: `return ""` when `items` is empty in
        quarantine_report.render."""
        os.remove(os.path.join(self.brain, "hub", "quarantine", "openclaw",
                               "2026-08-10-capture-abc123.txt"))
        text = self.render(digest_count=2)
        self.assertIn("no parked files found", text)
        self.assertTrue(text.strip(), "an empty detail block says nothing")

    def test_summary_names_cap_at_three_and_count_the_rest(self):
        """Mutation: `NOTIFY_MAX_NAMES = 999` in quarantine_report."""
        for n in range(9):
            self._park(f"openclaw/2026-08-10-extra{n}.txt",
                       CAPTURE_BLOCK.replace("backup-schedule", f"item-{n}"))
        names = quarantine_report.summary_names(
            quarantine_report.list_pending(self.brain))
        self.assertIn("+7 more", names)
        named = names.split(" (+")[0].split(", ")
        self.assertEqual(quarantine_report.NOTIFY_MAX_NAMES, len(named), names)

    def test_the_non_items_rule_matches_the_merge_writer(self):
        """Mutation: `QUARANTINE_NON_ITEMS = ("digest.md",)` in either module.

        brain_merge counts the parked blocks for the digest line; this module
        lists them for the alert. If the two disagree about what is not a block,
        the count and the list describe different sets of files and the alert
        contradicts itself."""
        self.assertEqual(brain_merge.QUARANTINE_NON_ITEMS,
                         quarantine_report.QUARANTINE_NON_ITEMS)


class AlertTextIsUnambiguous(HealthHarness, unittest.TestCase):
    """The acceptance test. A reader who has ONLY this text must state the right
    cause: the pipeline is healthy, N named blocks are parked, here is how to
    deal with each. Both payloads, with symmetric negatives."""

    MISLEADING = ("FAIL", "failed", "stuck", "dead", "broken")

    def review_note(self):
        """The review push notification, with the notifier's own flags stripped
        — the harness logs `$*`, so ` --silent` would otherwise count against
        the length budget the phone actually sees."""
        notes = [n for n in self.notifications() if "awaiting your review" in n]
        self.assertEqual(1, len(notes), self.notifications())
        return notes[0].replace(" --silent", "")

    def setUp(self):
        super().setUp()
        self.write_digest(_quarantine_digest(1))
        self.write("hub/quarantine/openclaw/2026-08-10-capture-abc123.txt",
                   CAPTURE_BLOCK)
        self.write("hub/quarantine/digest.md", DIGEST_LOG)

    def test_banner_alone_states_the_cause_and_the_next_command(self):
        """Mutation: delete the `printf '%s\\n' "${review_details[$i]:-}"` line
        from the banner writer in scripts/loreport-health."""
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        banner = self.banner_text()
        self.assertIsNotNone(banner)
        # what happened
        self.assertIn("the merge and publish completed normally", banner)
        self.assertIn("NEEDS REVIEW", banner)
        # which item
        self.assertIn("backup-schedule", banner)
        self.assertIn("schema-invalid", banner)
        self.assertIn("Nightly restic backup", banner)
        # what to do — one command per outcome
        self.assertIn("show: cat '", banner)
        self.assertIn("inbox_ingest.py", banner)
        self.assertIn("discard", banner)
        # and nothing that reads as a different failure
        for word in self.MISLEADING:
            self.assertNotIn(word, banner,
                             f"the banner contains {word!r}, which is how the "
                             f"original alert was misdiagnosed")

    def test_notification_alone_names_the_item_and_clears_the_pipeline(self):
        """Mutation: delete `[ -n "${q_names:-}" ] && q_msg="${q_msg}: ${q_names}"`
        from section 6b of scripts/loreport-health.

        This is the payload that was actually misread, on a phone."""
        self.run_health()
        msg = self.review_note()
        self.assertIn("merge and publish are running", msg)
        self.assertIn("backup-schedule", msg)
        self.assertIn("1 item(s)", msg)
        self.assertIn("what to do: cat ", msg)
        for word in self.MISLEADING:
            self.assertNotIn(word, msg)

    def test_notification_stays_short_enough_to_read_on_a_phone(self):
        """Mutation: `NOTIFY_MAX_NAMES = 999` in hub/quarantine_report.py.

        Ten parked blocks must not push the clause that says the pipeline is
        healthy out of a notification preview."""
        self.write_digest(_quarantine_digest(10))
        for n in range(9):
            self.write(
                f"hub/quarantine/openclaw/2026-08-10-extra{n}.txt",
                CAPTURE_BLOCK.replace("backup-schedule",
                                      f"a-fairly-long-memory-name-number-{n}"))
        self.run_health()
        msg = self.review_note()
        self.assertIn("+7 more", msg)
        self.assertLessEqual(len(msg), 400, msg)

    def test_a_single_enormous_name_cannot_blow_the_notification_cap(self):
        """Mutation: delete the `if [ "${#msg}" -gt "$NOTIFY_MAX_CHARS" ]`
        clamp from the `changed:review` arm in scripts/loreport-health."""
        self.write("hub/quarantine/openclaw/2026-08-10-capture-abc123.txt",
                   CAPTURE_BLOCK.replace("backup-schedule", "x" * 600))
        self.run_health()
        msg = self.review_note()
        self.assertLessEqual(len(msg), 400, len(msg))

    def test_a_quarantine_dir_that_lost_its_files_is_reported_not_hidden(self):
        """Mutation: delete the count-mismatch branch in
        quarantine_report.render.

        The digest says two are pending, the gitignored directory has none. The
        alert must not render a confident empty list."""
        os.remove(os.path.join(self.brain, "hub", "quarantine", "openclaw",
                               "2026-08-10-capture-abc123.txt"))
        self.write_digest(_quarantine_digest(2))
        self.run_health()
        banner = self.banner_text()
        self.assertIn("count mismatch", banner)

    def test_the_detail_block_does_not_re_page_when_only_wording_changes(self):
        """Mutation: append `${review_details[@]}` to the argument list of the
        state-change signature python block in scripts/loreport-health.

        The signature exists so a NEW problem speaks up while an old one is
        unresolved. Absolute paths and a truncated description are not new
        problems; folding them in would resurrect the alert fatigue the gate was
        built to stop."""
        self.run_health()
        first = len(self.notifications())
        self.assertGreater(first, 0)
        # Same item, same reason, reworded description -> the detail block
        # changes, the message does not.
        self.write("hub/quarantine/openclaw/2026-08-10-capture-abc123.txt",
                   CAPTURE_BLOCK.replace(
                       "Nightly restic backup of the home directory to object storage,",
                       "A completely different sentence about the same block"))
        self.run_health()
        self.assertEqual(first, len(self.notifications()),
                         "a reworded description re-sent the notification")


class ExistingReviewPathsStillCarryDetail(HealthHarness, unittest.TestCase):
    """The merge's own record (6c) points at the same directory, so it gets the
    same treatment — otherwise the actionable wording depends on which of two
    detectors noticed first."""

    def test_human_region_review_from_merge_state_lists_the_blocks(self):
        """Mutation: delete the `"$(quarantine_detail)"` third argument from the
        human_region_violations arm of section 6c in scripts/loreport-health."""
        self.write("hub/merge-state.json", json.dumps({
            "last_success_epoch": int(__import__("time").time()) - 3600,
            "needs_review_kinds": ["human_region_violations"],
        }))
        self.write("hub/quarantine/openclaw/2026-08-10-capture-abc123.txt",
                   CAPTURE_BLOCK)
        self.write("hub/quarantine/digest.md", DIGEST_LOG)
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        banner = self.banner_text()
        self.assertIn("backup-schedule", banner)
        self.assertIn("discard", banner)


if __name__ == "__main__":
    unittest.main()
