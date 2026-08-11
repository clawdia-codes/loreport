#!/usr/bin/env python3
"""Tests for hub/nightly_review.py — the synthesis detector's consumer.

Every test here names the single-line production change it reddens. That is not
ceremony: this project once shipped a 56-line feature with zero tests, and an
independent review found a fail-open hole in it by deleting the whole feature and
watching the suite stay green. `DeletingTheFeatureRedensTheSuite` at the bottom
is the standing guard against a repeat.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import nightly_review as nr  # noqa: E402

TODAY = "2026-08-11"


def cluster(topic, members, signal="mutual-link"):
    return {"topic": topic, "members": list(members), "signal": signal,
            "shared_type": None, "evidence": {"member_count": len(members)}}


def report(*clusters):
    return {"clusters": list(clusters), "warnings": [], "item_count": 86}


class BrainTmp:
    def setUp(self):
        self.brain = tempfile.mkdtemp(prefix="nightly-review-")
        self.addCleanup(shutil.rmtree, self.brain, ignore_errors=True)
        os.makedirs(os.path.join(self.brain, "hub"))

    def write(self, rel, text):
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read_json(self, rel):
        with open(os.path.join(self.brain, rel), encoding="utf-8") as fh:
            return json.load(fh)


class ProposalIdentityIsStable(BrainTmp, unittest.TestCase):
    """Reddens: sorting in `proposal_id` (`members = sorted(...)` -> `list(...)`).

    Without the sort, the detector emitting the same cluster in a different order
    mints a new id every night, which resets first_seen and means the overdue
    check can never fire — the assertion that forces dispositions, silently dead.
    """

    def test_member_order_does_not_change_the_id(self):
        a = nr.proposal_id(cluster("coach", ["c", "a", "b"]))
        b = nr.proposal_id(cluster("coach", ["a", "b", "c"]))
        self.assertEqual(a, b)

    def test_a_different_signal_is_a_different_proposal(self):
        a = nr.proposal_id(cluster("coach", ["a", "b", "c"], "mutual-link"))
        b = nr.proposal_id(cluster("coach", ["a", "b", "c"], "common-missing-link"))
        self.assertNotEqual(a, b)


class RejectionSurvivesMembershipChurn(BrainTmp, unittest.TestCase):
    """Reddens: the `suppressed_by(...)` call in `refresh` (delete the two lines
    that consult it and always add the proposal).

    The id IS the member set, so a rejected cluster that gains one member comes
    back as a brand-new pending proposal. Without suppression the owner re-decides
    the same rejected cluster every time the brain grows — a decision queue that
    refills itself is a queue people learn to ignore.
    """

    def _rejected_ledger(self, members):
        ledger = {"schema": 1, "proposals": {}}
        nr.refresh(ledger, report(cluster("coach", members)), TODAY)
        pid = next(iter(ledger["proposals"]))
        nr.dispose(ledger, pid, "reject", "not a real topic", TODAY)
        return ledger, pid

    def test_rejected_cluster_plus_one_member_does_not_come_back(self):
        ledger, pid = self._rejected_ledger(["a", "b", "c"])
        out = nr.refresh(ledger, report(cluster("coach", ["a", "b", "c", "d"])), TODAY)
        self.assertEqual(out["added"], [], "a rejected cluster returned as pending")
        self.assertFalse(out["changed"])
        self.assertEqual(out["suppressed"][0]["already_rejected_as"], pid)
        self.assertEqual(nr.pending(ledger, TODAY), [])

    def test_a_narrowed_subset_of_a_rejected_cluster_does_not_come_back(self):
        ledger, _ = self._rejected_ledger(["a", "b", "c", "d"])
        out = nr.refresh(ledger, report(cluster("coach", ["a", "b", "c"])), TODAY)
        self.assertEqual(out["added"], [])

    def test_two_newcomers_is_a_genuinely_new_proposal_and_does_come_back(self):
        """The suppression must not swallow everything: a materially different
        cluster still earns a fresh decision."""
        ledger, _ = self._rejected_ledger(["a", "b", "c"])
        out = nr.refresh(ledger, report(cluster("coach", ["a", "b", "c", "d", "e"])), TODAY)
        self.assertEqual(len(out["added"]), 1)
        self.assertEqual(len(nr.pending(ledger, TODAY)), 1)


class DispositionsMustBeExplained(BrainTmp, unittest.TestCase):
    """Reddens: the `if not isinstance(reason, str) or not reason.strip(): raise`
    guard in `dispose` (delete it).

    A disposition with no reason is a cleared queue, not a decision: six months
    later nothing distinguishes "considered and dismissed" from "clicked away".
    """

    def setUp(self):
        super().setUp()
        self.ledger = {"schema": 1, "proposals": {}}
        nr.refresh(self.ledger, report(cluster("coach", ["a", "b", "c"])), TODAY)
        self.pid = next(iter(self.ledger["proposals"]))

    def test_empty_reason_is_refused(self):
        for bad in ("", "   ", None):
            with self.assertRaises(nr.DispositionError):
                nr.dispose(self.ledger, self.pid, "accept", bad, TODAY)
        self.assertEqual(self.ledger["proposals"][self.pid]["status"], "pending")

    def test_a_reason_is_recorded_verbatim(self):
        nr.dispose(self.ledger, self.pid, "accept", "  worth a knowledge page  ", TODAY)
        entry = self.ledger["proposals"][self.pid]
        self.assertEqual(entry["reason"], "worth a knowledge page")
        self.assertEqual(entry["decided"], TODAY)

    def test_unknown_status_is_refused(self):
        with self.assertRaises(nr.DispositionError):
            nr.dispose(self.ledger, self.pid, "maybe", "hmm", TODAY)


class DeferNeedsAReturnDate(BrainTmp, unittest.TestCase):
    """Reddens: the `if not until: raise` / `if until_date <= today: raise` pair
    in `dispose`.

    A deferral with no future date is a reject wearing a disguise — the entry
    stops being pending and nothing ever brings it back.
    """

    def setUp(self):
        super().setUp()
        self.ledger = {"schema": 1, "proposals": {}}
        nr.refresh(self.ledger, report(cluster("coach", ["a", "b", "c"])), TODAY)
        self.pid = next(iter(self.ledger["proposals"]))

    def test_defer_without_until_is_refused(self):
        with self.assertRaises(nr.DispositionError):
            nr.dispose(self.ledger, self.pid, "defer", "not now", TODAY)

    def test_defer_into_the_past_is_refused(self):
        with self.assertRaises(nr.DispositionError):
            nr.dispose(self.ledger, self.pid, "defer", "not now", TODAY, until="2026-01-01")

    def test_deferral_is_not_pending_until_it_expires_then_is_again(self):
        nr.dispose(self.ledger, self.pid, "defer", "after the sprint", TODAY,
                   until="2026-09-01")
        self.assertEqual(nr.pending(self.ledger, TODAY), [])
        self.assertEqual(len(nr.pending(self.ledger, "2026-09-02")), 1)

    def test_an_expired_deferral_is_not_written_back_to_the_ledger(self):
        """The reopening is computed, never persisted: a ledger rewritten on a
        quiet night dirties the brain tree, which is why merge-state.json and the
        digests are gitignored in the first place."""
        nr.dispose(self.ledger, self.pid, "defer", "after the sprint", TODAY,
                   until="2026-09-01")
        before = json.dumps(self.ledger, sort_keys=True)
        out = nr.refresh(self.ledger, report(cluster("coach", ["a", "b", "c"])), "2026-09-02")
        self.assertFalse(out["changed"])
        self.assertEqual(json.dumps(self.ledger, sort_keys=True), before)


class OverdueForcesTheDecision(BrainTmp, unittest.TestCase):
    """Reddens: the `overdue(...)` computation feeding `attestation["synthesis"]
    ["overdue"]` in `build_attestation` (change `> max_days` to `> 10**6`).

    Everything else in this feature merely records; this is the only part that
    makes an undecided proposal cost something.
    """

    def _ledger_pending_since(self, first_seen):
        return {"schema": 1, "proposals": {"pid0": {
            "id": "pid0", "topic": "coach", "signal": "mutual-link",
            "members": ["a", "b", "c"], "first_seen": first_seen,
            "status": "pending", "reason": None, "decided": None,
            "decided_by": None, "defer_until": None}}}

    def test_inside_the_budget_is_not_overdue(self):
        ledger = self._ledger_pending_since("2026-08-01")  # 10 days
        self.assertEqual(nr.overdue(ledger, TODAY), [])

    def test_past_the_budget_is_overdue(self):
        ledger = self._ledger_pending_since("2026-07-01")
        self.assertEqual(len(nr.overdue(ledger, TODAY)), 1)

    def test_an_expired_deferral_restarts_the_clock_from_the_expiry(self):
        """Otherwise a 30-day deferral comes back already overdue and the owner
        is paged for a decision they explicitly scheduled for today."""
        ledger = self._ledger_pending_since("2026-06-01")
        ledger["proposals"]["pid0"].update(status="defer", defer_until="2026-08-10",
                                           reason="later", decided="2026-06-01")
        self.assertEqual(nr.effective_status(ledger["proposals"]["pid0"], TODAY), "pending")
        self.assertEqual(nr.overdue(ledger, TODAY), [])

    def test_the_attestation_carries_the_overdue_ids(self):
        ledger = self._ledger_pending_since("2026-07-01")
        att = nr.build_attestation(
            ledger, report(), {"added": [], "suppressed": [], "changed": False},
            {"status": "in-sync", "source_count": 1, "sources": []}, TODAY)
        self.assertEqual(att["synthesis"]["overdue"], ["pid0"])
        self.assertEqual(att["synthesis"]["dispositions"]["pending"], 1)


class NothingIsEverAutoMerged(BrainTmp, unittest.TestCase):
    """Reddens: any future line in `run` that writes under memories/ or knowledge/.

    The standing prohibition: a proposal becomes a decision, never an edit.
    """

    def test_a_full_run_creates_no_memory_and_no_knowledge_page(self):
        os.makedirs(os.path.join(self.brain, "memories"))
        os.makedirs(os.path.join(self.brain, "knowledge"))
        self.write("INDEX.md", "# Index\n\n- [[a]] — a\n")
        nr.run(self.brain, report(cluster("coach", ["a", "b", "c"])), today=TODAY)
        self.assertEqual(os.listdir(os.path.join(self.brain, "memories")), [])
        self.assertEqual(os.listdir(os.path.join(self.brain, "knowledge")), [])


class TheDatedArtifactIsWritten(BrainTmp, unittest.TestCase):
    """Reddens: the artifact write block in `run` (delete it).

    Health section 9 fails when yesterday's file is missing, so this is the proof
    that the nightly ran at all.
    """

    def test_run_writes_a_dated_machine_readable_artifact(self):
        self.write("INDEX.md", "# Index\n\n- [[a]] — a\n")
        nr.run(self.brain, report(cluster("coach", ["a", "b", "c"])), today=TODAY)
        payload = self.read_json(f"hub/nightly/{TODAY}.json")
        self.assertEqual(payload["date"], TODAY)
        self.assertEqual(payload["schema"], nr.SCHEMA)
        self.assertEqual(payload["synthesis"]["dispositions"]["pending"], 1)
        self.assertIn("reconciliation", payload)

    def test_dry_run_writes_nothing_at_all(self):
        before = sorted(os.listdir(self.brain))
        nr.run(self.brain, report(cluster("coach", ["a", "b", "c"])),
               today=TODAY, dry_run=True)
        self.assertEqual(sorted(os.listdir(self.brain)), before)


class TheLedgerIsWrittenOnlyWhenItChanges(BrainTmp, unittest.TestCase):
    """Reddens: the `if refreshed["changed"]:` guard around `save_ledger` in `run`.

    The ledger is TRACKED. A tracked file rewritten every night leaves the brain
    tree dirty every night, which halts loreport-sync's post-merge guard — the
    documented reason merge-state.json and the digests are gitignored.
    """

    def test_a_repeat_night_does_not_touch_the_ledger_file(self):
        self.write("INDEX.md", "# Index\n\n- [[a]] — a\n")
        rep = report(cluster("coach", ["a", "b", "c"]))
        first = nr.run(self.brain, rep, today=TODAY)
        self.assertTrue(first["ledger_changed"])
        path = os.path.join(self.brain, nr.LEDGER_FILE)
        mtime = os.path.getmtime(path)
        os.utime(path, (mtime - 100, mtime - 100))

        second = nr.run(self.brain, rep, today="2026-08-12")
        self.assertFalse(second["ledger_changed"])
        self.assertEqual(os.path.getmtime(path), mtime - 100,
                         "an unchanged night rewrote the tracked ledger")

    def test_a_corrupt_ledger_is_never_silently_replaced(self):
        self.write(nr.LEDGER_FILE, "{not json")
        with self.assertRaises(nr.LedgerError):
            nr.load_ledger(self.brain)


class ReconciliationNamesItsBlindSpots(BrainTmp, unittest.TestCase):
    """Reddens: the `if not sources:` early return in `reconcile` (delete it, so
    zero sources fall through and report "in-sync").

    A check that iterates an empty collection is vacuously true, and this repo has
    shipped that exact bug twice — most recently a published-packet privacy check
    that passed on an EMPTY packet.
    """

    def setUp(self):
        super().setUp()
        self.write("INDEX.md", "# Index\n\n- [[alpha]] — a\n- [[beta]] — b\n")
        self.native = os.path.join(self.brain, "native")
        os.makedirs(self.native)

    def _sources(self, *sources):
        self.write(nr.RECONCILE_SOURCES_FILE, json.dumps({"sources": list(sources)}))

    def test_no_config_reports_unconfigured_not_in_sync(self):
        out = nr.reconcile(self.brain)
        self.assertEqual(out["status"], "unconfigured")

    def test_an_empty_source_list_reports_unconfigured_not_in_sync(self):
        self._sources()
        self.assertEqual(nr.reconcile(self.brain)["status"], "unconfigured")

    def test_an_empty_index_is_blind_not_clean(self):
        self.write("INDEX.md", "# Index\n\nnothing yet\n")
        self.write("native/alpha.md", "x")
        self._sources({"provider": "claude", "path": "native", "kind": "dir"})
        self.assertEqual(nr.reconcile(self.brain)["status"], "blind")

    def test_an_empty_native_store_is_blind_not_clean(self):
        self._sources({"provider": "claude", "path": "native", "kind": "dir"})
        out = nr.reconcile(self.brain)
        self.assertEqual(out["sources"][0]["status"], "blind")
        self.assertEqual(out["status"], "blind")

    def test_an_unreadable_source_is_an_error_not_a_clean_diff(self):
        self._sources({"provider": "claude", "path": "nope", "kind": "dir"})
        out = nr.reconcile(self.brain)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["sources"][0]["status"], "unreadable")

    def test_an_item_only_in_native_memory_is_reported_as_drift(self):
        self.write("native/alpha.md", "x")
        self.write("native/gamma.md", "x")
        self._sources({"provider": "claude", "path": "native", "kind": "dir"})
        out = nr.reconcile(self.brain)
        self.assertEqual(out["status"], "drift")
        self.assertEqual(out["sources"][0]["only_native"], ["gamma"])
        self.assertEqual(out["sources"][0]["both_count"], 1)

    def test_a_fully_captured_native_store_is_in_sync(self):
        self.write("native/alpha.md", "x")
        self.write("native/beta.md", "x")
        self._sources({"provider": "claude", "path": "native", "kind": "dir"})
        self.assertEqual(nr.reconcile(self.brain)["status"], "in-sync")

    def test_file_kind_reads_wikilinks(self):
        self.write("native/MEMORY.md", "- [[alpha]]\n- [[delta]]\n")
        self._sources({"provider": "claude", "path": "native/MEMORY.md", "kind": "file"})
        out = nr.reconcile(self.brain)
        self.assertEqual(out["sources"][0]["only_native"], ["delta"])

    def test_reconciliation_never_writes_to_the_native_store(self):
        self.write("native/gamma.md", "original")
        self._sources({"provider": "claude", "path": "native", "kind": "dir"})
        nr.reconcile(self.brain)
        with open(os.path.join(self.native, "gamma.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "original")
        self.assertEqual(sorted(os.listdir(self.native)), ["gamma.md"])


class DigestLinesNameTheQueue(BrainTmp, unittest.TestCase):
    """Reddens: the `unconfigured` branch of `format_lines` (drop it so the
    generic branch renders). A digest that omits an unchecked subsystem reads as
    "checked and clean"."""

    def test_unconfigured_reconciliation_says_so_in_the_digest(self):
        self.write("INDEX.md", "# Index\n\n- [[a]] — a\n")
        summary = nr.run(self.brain, report(cluster("coach", ["a", "b", "c"])), today=TODAY)
        text = "\n".join(nr.format_lines(summary))
        self.assertIn("NOT CONFIGURED", text)
        self.assertIn("1 pending", text)

    def test_a_missing_summary_does_not_render_as_healthy(self):
        self.assertIn("did not run", "\n".join(nr.format_lines(None)))


class DeletingTheFeatureRedensTheSuite(BrainTmp, unittest.TestCase):
    """The independent-review guard: name the wiring that must exist, so removing
    the feature wholesale cannot leave a green suite."""

    def test_brain_merge_calls_the_nightly_review_and_puts_it_in_the_digest(self):
        sys.path.insert(0, os.path.join(ROOT, "hub"))
        import brain_merge  # noqa: E402
        self.assertTrue(hasattr(brain_merge, "run_nightly_review"))
        src = open(os.path.join(ROOT, "hub", "brain_merge.py"), encoding="utf-8").read()
        self.assertIn('report["nightly_review"] = run_nightly_review(', src)
        self.assertIn("nightly_review.format_lines(report.get(\"nightly_review\"))", src)

    def test_the_health_check_asserts_yesterdays_artifact(self):
        src = open(os.path.join(ROOT, "scripts", "loreport-health"), encoding="utf-8").read()
        self.assertIn("nightly review did not run last night", src)
        self.assertIn("nightly review has never run", src)

    def test_a_detector_crash_cannot_take_the_merge_down(self):
        sys.path.insert(0, os.path.join(ROOT, "hub"))
        import brain_merge  # noqa: E402
        original = brain_merge.nightly_review.run
        brain_merge.nightly_review.run = lambda *a, **k: 1 / 0
        try:
            out = brain_merge.run_nightly_review(self.brain, report(), dry_run=False)
        finally:
            brain_merge.nightly_review.run = original
        self.assertIn("ZeroDivisionError", out["error"])
        self.assertFalse(out["ledger_changed"])


class CliRefusesAndRecords(BrainTmp, unittest.TestCase):
    """Reddens: the `except (LedgerError, DispositionError): return 2` arm in
    `main` (change it to `return 0`). A CLI that refuses a disposition but exits
    0 lets a script believe the decision landed."""

    def test_dispose_without_a_reason_exits_nonzero(self):
        self.write("INDEX.md", "# Index\n\n- [[a]] — a\n")
        nr.run(self.brain, report(cluster("coach", ["a", "b", "c"])), today=TODAY)
        pid = next(iter(nr.load_ledger(self.brain)["proposals"]))
        rc = nr.main(["--brain-dir", self.brain, "--today", TODAY,
                      "--dispose", pid, "--status", "accept"])
        self.assertEqual(rc, 2)
        self.assertEqual(nr.load_ledger(self.brain)["proposals"][pid]["status"], "pending")

    def test_dispose_with_a_reason_is_persisted(self):
        self.write("INDEX.md", "# Index\n\n- [[a]] — a\n")
        nr.run(self.brain, report(cluster("coach", ["a", "b", "c"])), today=TODAY)
        pid = next(iter(nr.load_ledger(self.brain)["proposals"]))
        rc = nr.main(["--brain-dir", self.brain, "--today", TODAY, "--dispose", pid,
                      "--status", "reject", "--reason", "one topic, not three",
                      "--by", "tester"])
        self.assertEqual(rc, 0)
        entry = nr.load_ledger(self.brain)["proposals"][pid]
        self.assertEqual((entry["status"], entry["reason"], entry["decided_by"]),
                         ("reject", "one topic, not three", "tester"))


if __name__ == "__main__":
    unittest.main()
