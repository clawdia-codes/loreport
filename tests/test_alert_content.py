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
from datetime import date, timedelta

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
        self.assertIn("show: cat ", text)
        self.assertIn("accept", text)
        self.assertIn("discard", text)

    def test_a_capture_block_accepts_by_re_running_the_gate(self):
        """Mutation: change `--trust local` to `--trust cloud` in
        quarantine_report._commands.

        The trust tier is not cosmetic. The operator pasting this line is on
        their own machine, which is what the local tier means; under `cloud` the
        ownership check refuses every visibility:local item, so the command in
        the alert would re-quarantine exactly the blocks most likely to be
        parked and the reader would conclude the alert was wrong.

        That reasoning holds only for a block parked by a gate `--trust local`
        still runs. It does NOT hold when the ownership check itself did the
        refusing — see OwnershipRefusalGetsNoOneLiner below, the class this
        test was originally written too broadly to distinguish."""
        text = self.render()
        self.assertIn(os.path.join(self.engine, "hub", "inbox_ingest.py"), text)
        self.assertIn("openclaw", text)
        self.assertIn(f"--brain-dir {self.brain}", text)
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

    def test_an_empty_capture_is_not_called_a_rejected_merge_update(self):
        """Mutation: delete the `elif item["kind"] == "capture":` branch in
        quarantine_report._commands so an unclassifiable file falls back to the
        merge-update wording.

        An `empty-block` (0-byte) quarantine IS a capture — it just carries no
        provider to re-run it with. Falling through told the reader it was "a
        rejected merge update, not a capture block": the wrong subsystem, with a
        remedy that does not apply. Two separate agents reported a healthy brain
        as three days dead this week off the back of one mislabelled subsystem,
        which is why this is worth a test rather than a shrug."""
        self._park("claude/2026-08-10-capture-empty00.txt", "")
        text = self.render()
        block = text.split("empty00", 1)[1]
        head = block.split("discard", 1)[0]
        self.assertNotIn("rejected merge update", head)
        self.assertIn("could not be classified", head)
        self.assertIn("0-byte", head)

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

    def test_summary_names_cap_each_name_not_only_how_many(self):
        """Mutation: `NOTIFY_MAX_NAME_CHARS = 10000` in quarantine_report.

        Capping the COUNT is not enough. `name:` comes out of a block that
        failed the schema gate, so it is unvalidated caller text — one enormous
        name is exactly the shape that gets parked, and it spends the whole
        notification budget on its own. Three names of 600 chars each is the
        same defect as ten names."""
        self._park("openclaw/2026-08-10-huge.txt",
                   CAPTURE_BLOCK.replace("backup-schedule", "x" * 600))
        names = quarantine_report.summary_names(
            quarantine_report.list_pending(self.brain))
        for label in names.split(" (+")[0].split(", "):
            # A LITERAL bound, not NOTIFY_MAX_NAME_CHARS. Asserting against the
            # constant the production code reads makes the test agree with any
            # value of it, including 10000 — a test that cannot fail.
            self.assertLessEqual(
                len(label), 80,
                f"an uncapped name reached the phone payload: {label[:80]!r}")

    def test_the_non_items_rule_matches_the_merge_writer(self):
        """Mutation: `QUARANTINE_NON_ITEMS = ("digest.md",)` in either module.

        brain_merge counts the parked blocks for the digest line; this module
        lists them for the alert. If the two disagree about what is not a block,
        the count and the list describe different sets of files and the alert
        contradicts itself."""
        self.assertEqual(brain_merge.QUARANTINE_NON_ITEMS,
                         quarantine_report.QUARANTINE_NON_ITEMS)


OWNERSHIP_DIGEST_LOG = """# Quarantine digest

## 2026-08-10T01:02:03 — QUARANTINE (chatgpt)
- file: hub/quarantine/chatgpt/2026-08-10-capture-own1.txt
- reason: ownership-denied
- detail: cloud-trust caller (provider=chatgpt) may not update/delete 'memories/health-notes.md': its main version is visibility: local

"""

OWNERSHIP_BLOCK = """<MEMORY file="memories/health-notes.md" action="update">
---
name: health-notes
type: reference
visibility: shared
description: Rewritten by a second provider
---
Replacement body from the other provider.
</MEMORY>
"""


class OwnershipRefusalGetsNoOneLiner(unittest.TestCase):
    """A block refused BY the ownership check must not be handed a command that
    switches that check off.

    The alert's accept line is `inbox_ingest.py ... --trust local`, and
    check_ownership() returns None on its first line under local trust. So for
    reason `ownership-denied` the label "re-runs the capture gate" is false: the
    gate is not re-run, it is disabled — and the commit it produces carries
    `Trust: local`, which collect_provenance_violations() skips, so the
    merge-side backstop built for exactly this cross-provider takeover never
    fires either. Two defences waived by one line the alert itself printed.

    No mutation could have caught this: the original
    test_a_capture_block_accepts_by_re_running_the_gate ASSERTS `--trust local`
    unconditionally, so the mutation harness pinned the defect in place.
    """

    def setUp(self):
        import shutil
        import tempfile
        self.brain = tempfile.mkdtemp(prefix="loreport-p4-own-")
        self.addCleanup(shutil.rmtree, self.brain, ignore_errors=True)
        self.engine = os.path.join(self.brain, "engine")
        self._write("hub/quarantine/chatgpt/2026-08-10-capture-own1.txt",
                    OWNERSHIP_BLOCK)
        self._write("hub/quarantine/digest.md", OWNERSHIP_DIGEST_LOG)

    def _write(self, rel, text):
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def render(self):
        return quarantine_report.render(self.brain, self.engine)

    def test_no_paste_and_run_accept_is_printed_for_an_ownership_refusal(self):
        """Mutation: `OWNERSHIP_WAIVED_REASONS = ()` in quarantine_report.

        (Equivalently: drop the `item["reason"] in OWNERSHIP_WAIVED_REASONS`
        arm from _commands, which is the pre-fix code.)"""
        text = self.render()
        self.assertIn("health-notes", text)
        self.assertIn("ownership-denied", text)
        accept_region = text.split("accept", 1)[1].split("discard", 1)[0]
        # No RUNNABLE line. The prose is allowed to name `--trust local` — it
        # has to, to explain the refusal — so assert on what makes a line
        # pasteable instead.
        self.assertNotIn("inbox_ingest.py", accept_region)
        self.assertNotIn("python3 ", accept_region)

    def test_the_accept_label_never_claims_a_gate_it_does_not_run(self):
        """Mutation: `OWNERSHIP_WAIVED_REASONS = ()` in quarantine_report.

        The label, not just the command. "re-runs the capture gate" over a line
        that disables the gate is the one-word-two-conditions failure this repo
        keeps paying for."""
        text = self.render()
        self.assertNotIn("re-runs the capture gate", text)

    def test_it_says_why_and_what_to_do_instead(self):
        """Mutation: replace the ownership-denied accept text with the bare
        string "not automatic".

        Withholding the command is only half the job — a reader left with
        "not automatic" and no reason pastes the other block's command."""
        text = self.render()
        self.assertIn("not automatic", text)
        self.assertIn("ownership", text)
        self.assertIn("Trust: local", text)
        self.assertIn("visibility", text)

    def test_an_ordinary_capture_refusal_still_gets_its_command(self):
        """Mutation: `OWNERSHIP_WAIVED_REASONS = ("ownership-denied",
        "schema-invalid")` in quarantine_report.

        The withholding must be scoped to the reason the ownership check
        produced. Widening it would strip the useful one-liner off every parked
        capture and undo P4."""
        self._write("hub/quarantine/openclaw/2026-08-10-capture-abc123.txt",
                    CAPTURE_BLOCK)
        self._write("hub/quarantine/digest.md",
                    OWNERSHIP_DIGEST_LOG + DIGEST_LOG.split("\n", 1)[1])
        text = self.render()
        schema_block = text.split("backup-schedule", 1)[1]
        self.assertIn("inbox_ingest.py", schema_block)
        self.assertIn("--trust local", schema_block)


class PrintedCommandsAreShellSafe(unittest.TestCase):
    """These lines exist to be pasted into a shell, so a parked filename is
    command text, not a display string. `'{path}'` breaks out on a quote."""

    def setUp(self):
        import shutil
        import tempfile
        self.brain = tempfile.mkdtemp(prefix="loreport-p4-sh-")
        self.addCleanup(shutil.rmtree, self.brain, ignore_errors=True)
        self.engine = os.path.join(self.brain, "engine")

    def test_a_quote_in_a_parked_filename_cannot_break_out_of_the_command(self):
        """Mutation: `f"cat '{path}'"` in place of the shlex.quote call in
        quarantine_report._commands."""
        import shlex as _shlex
        name = "2026-08-11-memories__x'$(touch PWNED)'.md"
        rel = os.path.join("hub", "quarantine", "openclaw", name)
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(MERGE_UPDATE_BLOCK)
        for verb, cmd in quarantine_report._commands(
                quarantine_report.list_pending(self.brain)[0],
                self.brain, self.engine):
            if not cmd.startswith(("cat ", "rm ", "python3 ")):
                continue
            argv = _shlex.split(cmd)
            self.assertIn(path, argv,
                          f"{verb!r} did not survive shell parsing: {cmd!r}")
            self.assertNotIn("PWNED", " ".join(argv[2:]),
                             f"{verb!r} leaked a substitution: {cmd!r}")


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

    def review_region(self):
        """Only the NEEDS REVIEW half of the banner.

        The negatives below must be scoped: the banner's own failure header
        reads `FAILING — these are broken or stale:`, so asserting "broken" is
        absent from the whole file would go red the first time a real failure
        co-occurred with a pending item — on production text that is correct."""
        banner = self.banner_text()
        self.assertIsNotNone(banner, "a pending review item left no banner at all")
        self.assertIn("NEEDS REVIEW", banner)
        return banner.split("NEEDS REVIEW", 1)[1]

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
        region = self.review_region()
        # what happened
        self.assertIn("the merge and publish completed normally", region)
        # which item
        self.assertIn("backup-schedule", region)
        self.assertIn("schema-invalid", region)
        self.assertIn("Nightly restic backup", region)
        # what to do — one command per outcome
        self.assertIn("show: cat ", region)
        self.assertIn("inbox_ingest.py", region)
        self.assertIn("discard", region)
        # and nothing that reads as a different failure
        for word in self.MISLEADING:
            self.assertNotIn(word, region,
                             f"the review section contains {word!r}, which is "
                             f"how the original alert was misdiagnosed")

    def test_review_wording_survives_a_co_occurring_failure(self):
        """Mutation: delete the `printf '%s\\n' "${review_details[$i]:-}"` line
        from the banner writer in scripts/loreport-health.

        A real failure and a pending item can hold at once. The failure must not
        borrow the review's wording, and — the direction that caused the
        incident — the review must not borrow the failure's: it still says the
        merge and publish completed, and still carries its own commands."""
        self.set_merge_age(hours=100)  # merge liveness goes red
        result = self.run_health()
        self.assertEqual(result.returncode, 1, "the failure was not graded as one")
        banner = self.banner_text()
        self.assertIn("FAILING", banner)
        region = self.review_region()
        self.assertIn("the merge and publish completed normally", region)
        self.assertIn("backup-schedule", region)
        # Assert on text only the DETAIL block can supply: the item name and the
        # word "discard" both appear in the summary line and the `next:` hint,
        # so asserting those alone would stay green with the detail block gone.
        self.assertIn("show: cat ", region)
        self.assertIn("schema-invalid", region)
        for word in self.MISLEADING:
            self.assertNotIn(word, region)

    def test_notification_alone_names_the_item_and_clears_the_pipeline(self):
        """Mutation: delete `[ -n "${q_names:-}" ] && q_msg="${q_msg}: ${q_names}"`
        from section 6b of scripts/loreport-health.

        This is the payload that was actually misread, on a phone."""
        self.run_health()
        msg = self.review_note()
        self.assertIn("merge and publish are running", msg)
        self.assertIn("backup-schedule", msg)
        self.assertIn("1 item(s)", msg)
        self.assertIn("what to do — reply to Clawdia", msg)
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

    def test_a_clamped_message_keeps_the_actionable_pointer(self):
        """Mutation: append the pointer BEFORE the clamp in the
        `changed:review` arm of scripts/loreport-health — i.e. restore
        `msg="${msg} · what to do: cat ${BANNER}"` above the `if [ "${#msg}"
        -gt ... ]` block and clamp the whole thing.

        The clamp cuts from the END, so appending first put the only actionable
        element of the phone payload last in line to be evicted. Staying under
        400 chars is not enough — the cap must spend its budget on caller text,
        never on the fixed suffix.

        The cap is lowered through LOREPORT_NOTIFY_MAX_CHARS rather than by
        building a naturally-400-char fixture: this must redden on the ORDER,
        and a fixture tuned to overflow by a few characters would instead be
        asserting on its own arithmetic. The first assertion proves the clamp
        actually fired, so the test cannot pass vacuously."""
        self.write_digest(_quarantine_digest(10))
        for n in range(9):
            self.write(
                f"hub/quarantine/openclaw/2026-08-10-extra{n}.txt",
                CAPTURE_BLOCK.replace("backup-schedule",
                                      f"a-parked-memory-with-a-realistically-"
                                      f"long-hyphenated-name-{n}"))
        self.run_health(extra_env={"LOREPORT_NOTIFY_MAX_CHARS": "220"})
        msg = self.review_note()
        self.assertIn("…", msg, "the clamp did not fire; this test proves "
                                "nothing unless the message is truncated")
        self.assertLessEqual(len(msg), 220, len(msg))
        self.assertIn("what to do — reply to Clawdia", msg)
        self.assertIn("merge and publish are running", msg)

    def test_a_missing_report_helper_says_so_instead_of_an_empty_detail(self):
        """Mutation: `return 0` in place of the printf in the
        `[ ! -f "$QUARANTINE_REPORT" ]` arm of quarantine_detail in
        scripts/loreport-health.

        With an older engine checkout on LOREPORT_ENGINE the helper is absent.
        Returning empty leaves the review line's "each block below carries a
        show/accept/discard command" standing over nothing, and the
        notification pointing the reader at that empty banner — a skipped step
        reporting success."""
        # A SHADOW engine tree, symlinked entry by entry, minus the one file.
        # Never rename anything inside the real checkout: a crashed test would
        # leave the working tree mutilated.
        import test_health_liveness as thl
        old = os.path.join(self.tmp, "old-engine")
        os.makedirs(os.path.join(old, "hub"))
        for entry in os.listdir(thl.ENGINE):
            if entry == "hub":
                continue
            os.symlink(os.path.join(thl.ENGINE, entry), os.path.join(old, entry))
        for entry in os.listdir(os.path.join(thl.ENGINE, "hub")):
            if entry == "quarantine_report.py":
                continue
            os.symlink(os.path.join(thl.ENGINE, "hub", entry),
                       os.path.join(old, "hub", entry))
        conf = os.path.join(self.tmp, "old-engine.conf")
        with open(conf, "w", encoding="utf-8") as fh:
            fh.write(f'LOREPORT_BRAIN="{self.brain}"\n'
                     f'LOREPORT_ENGINE="{old}"\n'
                     f'LOREPORT_BANNER="{self.banner}"\n'
                     f'LOREPORT_NOTIFY="{self.notifier}"\n')
        self.config = conf
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        region = self.review_region()
        self.assertIn("no per-block detail", region)
        self.assertIn("quarantine_report.py", region)
        for word in self.MISLEADING:
            self.assertNotIn(word, region)

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




class TwoReviewSourcesInOneRun(HealthHarness, unittest.TestCase):
    """MERGE GUARD. Three feature branches edited scripts/loreport-health, and
    no single branch could test the state where two of them speak at once.

    Section 6b (a parked capture block) came from one branch and gained an
    optional third `note_review` argument, the per-item detail block. Section 9
    (a synthesis proposal awaiting disposition) came from another and still
    calls `note_review` with two. `${3:-}` is what makes that safe — and nothing
    exercised it. This is also the shape the whole 1.14.0 naming work exists
    for: two independent things owe a decision, the pipeline is healthy, and the
    reader must be able to tell that from the alert alone.
    """

    def setUp(self):
        super().setUp()
        self.write_digest(_quarantine_digest(1))
        self.write("hub/quarantine/openclaw/2026-08-10-capture-abc123.txt",
                   CAPTURE_BLOCK)
        self.write("hub/quarantine/digest.md", DIGEST_LOG)
        self.write_nightly(ledger=self.pending_ledger(
            (date.today() - timedelta(days=2)).isoformat()))

    def test_both_review_items_are_reported_and_neither_is_a_failure(self):
        """Mutation: change `review_details+=("${3:-}")` to
        `review_details+=("$3")` in scripts/loreport-health — section 9's
        two-argument note_review call then aborts the script under `set -u`."""
        result = self.run_health()
        self.assertEqual(result.returncode, 0,
                         f"two pending decisions were graded a failure: "
                         f"{result.stderr}")
        banner = self.banner_text() or ""
        self.assertIn("NEEDS REVIEW", banner)
        # 6b: the parked block, named, with its per-item commands.
        self.assertIn("backup-schedule", banner)
        self.assertIn("show: cat ", banner)
        # 9: the proposal awaiting a disposition, with its own command.
        self.assertIn("awaiting your disposition", banner)
        self.assertIn("--dispose", banner)
        # And the detail block belongs to 6b alone — section 9 passes no third
        # argument, so its entry must render without one rather than borrowing.
        self.assertEqual(banner.count("parked blocks under"), 1, banner)

    def test_one_notification_covers_both_and_still_says_the_pipeline_is_fine(self):
        """Mutation: as above. The phone payload is the one that got misread;
        two simultaneous review items must not make it read as a failure."""
        self.run_health()
        notes = [n for n in self.notifications() if "awaiting your review" in n]
        self.assertEqual(1, len(notes), self.notifications())
        msg = notes[0].replace(" --silent", "")
        self.assertIn("2 item(s)", msg)
        self.assertIn("merge and publish are running", msg)
        self.assertIn("what to do — reply to Clawdia", msg)
        self.assertLessEqual(len(msg), 400, len(msg))
        for word in ("FAIL", "failed", "stuck", "dead", "broken"):
            self.assertNotIn(word, msg)


if __name__ == "__main__":
    unittest.main()
