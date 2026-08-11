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
import subprocess
import tempfile
import unittest
import unittest.mock
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

    def test_a_broken_clock_is_graded_overdue_rather_than_ignored(self):
        """Mutation: `return entry.get("first_seen") or today` in _clock_start,
        or `return 0` in the ValueError arm of _days_between — the pre-fix code,
        either of which recomputes the age as 0 every night.

        The overdue check is described in-file as the only thing that actually
        forces a decision. A `first_seen` that is absent, null or unparseable
        made the proposal permanently un-overdue — silently, forever, in exactly
        the assertion that must not fail open. The ledger is a human-editable
        tracked JSON file the design invites the owner to operate on, so a
        malformed date is a real input; and an entry nobody can date is exactly
        an entry nobody has looked at, so the safe grade is overdue."""
        for label, value in (("unparseable", "garbage"),
                             ("null", None),
                             ("empty", "")):
            with self.subTest(first_seen=label):
                ledger = self._ledger_pending_since(value)
                self.assertEqual(len(nr.pending(ledger, TODAY)), 1)
                self.assertEqual(
                    [e["id"] for e in nr.overdue(ledger, TODAY)], ["pid0"],
                    f"a {label} first_seen stays pending forever without ever "
                    f"being graded overdue")
        missing = self._ledger_pending_since("2026-08-01")
        del missing["proposals"]["pid0"]["first_seen"]
        with self.subTest(first_seen="absent"):
            self.assertEqual([e["id"] for e in nr.overdue(missing, TODAY)],
                             ["pid0"])

    def test_a_good_clock_inside_the_budget_is_still_not_overdue(self):
        """Mutation: `return True` unconditionally in _is_overdue.

        Failing closed must not become "everything is overdue" — that is the
        alert fatigue the state-change gate exists to prevent."""
        self.assertEqual(nr.overdue(self._ledger_pending_since("2026-08-01"),
                                    TODAY), [])

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


class ReconciliationResolvesPathsAsDocumented(BrainTmp, unittest.TestCase):
    """The config in hub/HUB.md is copy-paste, and every example path in it
    starts with `~`.

    _native_names joined the raw path onto the brain root BEFORE expanding, and
    `~/x` is not os.path.isabs — so `~/.claude/...` became
    `<brain>/~/.claude/...`, which expanduser cannot recover because it only
    expands a LEADING `~`. Every source configured exactly as documented came
    back `unreadable`, making the whole reconciliation `error`. Reconciliation
    is one of P6's three deliverables and it was unusable as documented."""

    def setUp(self):
        super().setUp()
        self.write("INDEX.md", "# Index\n\n- [[alpha]] — a\n")
        self.home = tempfile.mkdtemp(prefix="nightly-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def _sources(self, *sources):
        self.write(nr.RECONCILE_SOURCES_FILE, json.dumps({"sources": list(sources)}))

    def test_a_tilde_path_expands_before_it_is_joined_to_the_brain_root(self):
        """Mutation: restore `path = raw if os.path.isabs(raw) else
        os.path.join(source_dir_root, raw); path = os.path.expanduser(path)` in
        _native_names."""
        store = os.path.join(self.home, "native-store")
        os.makedirs(store)
        for name in ("alpha", "gamma"):
            with open(os.path.join(store, f"{name}.md"), "w") as fh:
                fh.write("x")
        with unittest.mock.patch.dict(os.environ, {"HOME": self.home}):
            self._sources({"provider": "claude", "path": "~/native-store",
                           "kind": "dir"})
            out = nr.reconcile(self.brain)
        self.assertEqual(out["status"], "drift",
                         f"a documented `~` path did not resolve: {out}")
        self.assertEqual(out["sources"][0]["only_native"], ["gamma"])

    def test_a_relative_path_still_resolves_against_the_brain_root(self):
        """Mutation: drop the `os.path.join(source_dir_root, path)` fallback in
        _native_names — the other half of the documented contract."""
        self.write("native/alpha.md", "x")
        self.write("native/delta.md", "x")
        self._sources({"provider": "claude", "path": "native", "kind": "dir"})
        out = nr.reconcile(self.brain)
        self.assertEqual(out["sources"][0]["only_native"], ["delta"])


class ReconciliationAlwaysCarriesATopLevelDetail(BrainTmp, unittest.TestCase):
    """scripts/loreport-health §9 renders `rec.get('detail')`.

    The multi-source return had no top-level `detail`, so an `error` printed the
    literal string "None": a weekly FAIL naming neither the source, nor the
    path, nor the reason, for a config the owner wrote from the documentation.
    """

    def setUp(self):
        super().setUp()
        self.write("INDEX.md", "# Index\n\n- [[alpha]] — a\n")

    def _sources(self, *sources):
        self.write(nr.RECONCILE_SOURCES_FILE, json.dumps({"sources": list(sources)}))

    def test_every_status_carries_a_detail_a_reader_can_act_on(self):
        """Mutation: delete the `out["detail"] = ...` block at the end of
        reconcile (returning the bare dict, i.e. the pre-fix code)."""
        cases = []
        self._sources({"provider": "claude", "path": "nope", "kind": "dir"})
        cases.append(("error", nr.reconcile(self.brain)))
        self.write("native/alpha.md", "x")
        self.write("native/gamma.md", "x")
        self._sources({"provider": "claude", "path": "native", "kind": "dir"})
        cases.append(("drift", nr.reconcile(self.brain)))
        os.remove(os.path.join(self.brain, "native", "gamma.md"))
        cases.append(("in-sync", nr.reconcile(self.brain)))
        for label, out in cases:
            self.assertEqual(out["status"], label, out)
            self.assertTrue(out.get("detail"),
                            f"{label} carries no top-level detail: health "
                            f"renders this as the literal string 'None'")
            self.assertNotIn("None", str(out["detail"]))

    def test_the_error_detail_names_the_source_and_the_path(self):
        """Mutation: `out["detail"] = "reconciliation failed"` in reconcile.

        "could not run: reconciliation failed" is the uninformative alert P4
        exists to abolish; the owner must not have to go investigate."""
        self._sources({"provider": "claude", "path": "nope", "kind": "dir"})
        detail = nr.reconcile(self.brain)["detail"]
        self.assertIn("claude", detail)
        self.assertIn("nope", detail)


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


# --- the staging path, executed rather than reasoned about -------------------

ITEM = """---
name: {name}
description: {name} hook
type: project
visibility: shared
---

Body linking {links}.
"""


class TheLedgerIsActuallyCommittedByTheMerge(unittest.TestCase):
    """Drives the REAL merge, because the one line that stages the ledger was
    otherwise only argued for in a comment.

    The ledger is tracked, and a tracked file the nightly leaves modified would
    halt loreport-sync's post-merge guard — the documented reason merge-state.json
    and the digests are gitignored. Two claims, both asserted against
    `git status --porcelain` rather than against return codes:

      * a merge that detects a NEW proposal commits the ledger, leaving no residue
      * a merge that detects nothing new leaves the ledger entirely alone

    The 1.14.0 scoped-cleanup work found two placement assumptions of exactly this
    shape wrong by reasoning and right only by asserting the postcondition.
    """

    def setUp(self):
        import brain_merge  # noqa: E402
        self.bm = brain_merge
        self.repo = tempfile.mkdtemp(prefix="nightly-merge-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self._git("init", "-q", "-b", "main", ".")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "test")
        # Mirrors brain-template/.gitignore: hub/nightly/ is a derived per-run
        # report, hub/proposals/ is NOT and must stay trackable.
        self._write(".gitignore", "hub/nightly/\nhub/digest-*.md\nhub/synthesis-report.json\n")
        self._write("PROFILE.md", "# profile\n")
        self._write("prompts/bootstrap.md", "# bootstrap\n")
        # A mutual-link triangle: the shape synth_detect emits a cluster for.
        for name, links in (("alpha", "[[beta]] [[gamma]]"),
                            ("beta", "[[alpha]] [[gamma]]"),
                            ("gamma", "[[alpha]] [[beta]]")):
            self._write(f"memories/{name}.md", ITEM.format(name=name, links=links))
        self._rebuild_index()
        self._git("add", "-A")
        self._git("commit", "-qm", "seed")

    def _git(self, *args, check=True):
        return subprocess.run(["git", "-C", self.repo, *args],
                              capture_output=True, text=True, check=check)

    def _write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _rebuild_index(self):
        index, _, _, _ = self.bm.build_index_bytes(self.repo)
        with open(os.path.join(self.repo, "INDEX.md"), "wb") as fh:
            fh.write(index)

    def _porcelain(self, *paths):
        return self._git("status", "--porcelain", "--", *paths).stdout.strip()

    def _force_a_merge(self):
        """A real capture on a provider branch, so the run is not a no-op."""
        self._git("checkout", "-qb", "provider/claude")
        self._write("memories/delta.md", ITEM.format(name="delta", links="[[alpha]]"))
        self._git("add", "-A")
        self._git("commit", "-qm", "capture\n\nTrust: local")
        self._git("checkout", "-q", "main")

    def test_a_merge_that_finds_a_proposal_commits_the_ledger_and_leaves_no_residue(self):
        self._force_a_merge()
        report = self.bm.do_merge(self.repo, dry_run=False)
        self.assertTrue(report["nightly_review"]["ledger_changed"],
                        "the mutual-link triangle produced no proposal — fixture is wrong")
        tracked = self._git("ls-files", nr.LEDGER_FILE).stdout.strip()
        self.assertEqual(tracked, nr.LEDGER_FILE,
                         "the ledger was written but never committed: first_seen would "
                         "not survive a re-clone and the overdue check fails open")
        self.assertEqual(self._porcelain(nr.LEDGER_FILE), "",
                         "the merge left the tracked ledger dirty for loreport-sync")

    def test_a_quiet_night_leaves_the_ledger_and_the_tree_untouched(self):
        self._force_a_merge()
        self.bm.do_merge(self.repo, dry_run=False)
        head = self._git("rev-parse", "HEAD").stdout.strip()
        report = self.bm.do_merge(self.repo, dry_run=False)
        self.assertFalse(report["nightly_review"]["ledger_changed"])
        self.assertEqual(self._git("rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(self._porcelain(nr.LEDGER_FILE), "")

    def test_the_first_quiet_night_after_deploy_still_commits_the_ledger(self):
        """Mutation: delete the `commit_ledger_on_noop(...)` call from the
        no-op `else:` arm of brain_merge.do_merge — i.e. the pre-fix code.

        THE DEFAULT PATH. do_merge's own comment calls a no-op night "most
        days", and the very first night after this feature deploys is one: the
        brain already holds the clusters, so proposals are detected against a
        repository nothing has pushed to in weeks. Before the fix, run 1 gave
        noop=True, ledger_changed=True, and `git ls-files` on the ledger came
        back EMPTY; run 2 gave ledger_changed=False, so it was never staged
        again. Live proposals and every disposition recorded against them lived
        only as an untracked local file — never pushed, gone on re-clone, at
        which point every rejection returns as pending and every clock resets.

        Deliberately NO _force_a_merge(): both pre-existing merge tests call it
        first, which is exactly why the default path went untested."""
        report = self.bm.do_merge(self.repo, dry_run=False)
        self.assertTrue(report["noop"],
                        "fixture is wrong: this run was not a no-op")
        self.assertTrue(report["nightly_review"]["ledger_changed"],
                        "fixture is wrong: no proposal was detected")
        tracked = self._git("ls-files", nr.LEDGER_FILE).stdout.strip()
        self.assertEqual(tracked, nr.LEDGER_FILE,
                         "a quiet night wrote live proposals to an untracked file")
        self.assertEqual(self._porcelain(nr.LEDGER_FILE), "",
                         "the ledger was left dirty for loreport-sync")
        head = self._git("rev-parse", "HEAD").stdout.strip()
        again = self.bm.do_merge(self.repo, dry_run=False)
        self.assertFalse(again["nightly_review"]["ledger_changed"])
        self.assertEqual(self._git("rev-parse", "HEAD").stdout.strip(), head,
                         "an unchanged ledger produced a commit anyway")

    def test_a_quiet_night_never_leaves_the_ledger_staged_but_uncommitted(self):
        """Mutation: in brain_merge.commit_ledger_on_noop, delete the
        `git commit` and keep the `git add` — the naive fix for the bug above.

        Staging without committing is WORSE than the bug it fixes:
        bin/loreport-sync's post-merge guard runs `git status --porcelain
        --untracked-files=no`, which DOES see staged changes, so the sync would
        print `Loreport sync HALTED: repo state unsafe after merge` every single
        night. Assert the index, not only the worktree."""
        self.bm.do_merge(self.repo, dry_run=False)
        staged = self._git("diff", "--cached", "--name-only").stdout.strip()
        self.assertEqual(staged, "",
                         "a staged-but-uncommitted tree halts the nightly sync")
        guard = self._git("status", "--porcelain",
                          "--untracked-files=no").stdout.strip()
        self.assertEqual(guard, "", "loreport-sync's post-merge guard would HALT")

    def test_a_quiet_night_does_not_revert_a_disposition_written_by_hand(self):
        """Mutation: merge the `elif dry_run:` arm of do_merge back into the
        single `else:` it came from, restoring the unconditional
        `git reset --hard orig_head` on a no-op night.

        `--dispose` writes the ledger out of band, so between the owner's
        decision in the afternoon and the merge at night the ledger is a
        tracked, MODIFIED file — and `reset --hard` reverts tracked files. The
        decision would be thrown away silently and the proposal would be back as
        pending in the morning."""
        self.bm.do_merge(self.repo, dry_run=False)  # ledger becomes tracked
        ledger = nr.load_ledger(self.repo)
        pid = sorted(ledger["proposals"])[0]
        nr.dispose(ledger, pid, "reject", "too broad to be one page",
                   nr.today_iso())
        nr.save_ledger(self.repo, ledger)
        self.bm.do_merge(self.repo, dry_run=False)
        after = nr.load_ledger(self.repo)
        self.assertEqual(after["proposals"][pid]["status"], "reject",
                         "a quiet night reverted the owner's decision")
        self.assertEqual(after["proposals"][pid]["reason"],
                         "too broad to be one page")
        self.assertEqual(self._porcelain(nr.LEDGER_FILE), "",
                         "the decision survived on disk but was never committed")
        committed = self._git("show", f"HEAD:{nr.LEDGER_FILE}").stdout
        self.assertIn("too broad to be one page", committed,
                      "the decision is not in git; it dies on the next re-clone")

    def test_the_dated_artifact_is_not_tracked(self):
        self._force_a_merge()
        self.bm.do_merge(self.repo, dry_run=False)
        day = date.today().isoformat()
        self.assertTrue(os.path.isfile(
            os.path.join(self.repo, nr.ATTESTATION_DIR, f"{day}.json")))
        self.assertEqual(self._git("ls-files", nr.ATTESTATION_DIR).stdout.strip(), "",
                         "a per-run report got tracked; the tree will be dirty nightly")


class TheBrainTemplateIgnoresTheRightThings(unittest.TestCase):
    """MERGE GUARD on brain-template/.gitignore, which no other test reads.

    A brain gets that file ONCE, at init; the engine's own .gitignore does not
    reach it. Two feature branches appended to the same block, and a "union" or
    "complete the set" resolution that swept `hub/proposals/` in would silently
    undo the ledger-commit fix — the tracked ledger is what makes `first_seen`
    survive a re-clone, and without it the overdue check fails open — with the
    entire suite still green, because every merge fixture writes its own
    .gitignore in setUp.
    """

    def setUp(self):
        self.path = os.path.join(ROOT, "brain-template", ".gitignore")
        with open(self.path, encoding="utf-8") as fh:
            self.lines = [ln.strip() for ln in fh
                          if ln.strip() and not ln.startswith("#")]

    def test_the_derived_per_run_artifacts_are_ignored(self):
        """Mutation: delete `hub/nightly/` (or `hub/attention.json`) from
        brain-template/.gitignore. Both are rewritten outside a commit, so a
        tracked one leaves the tree dirty nightly and halts loreport-sync's
        post-merge guard."""
        for entry in ("hub/nightly/", "hub/attention.json",
                      "hub/merge-state.json", "hub/digest-*.md"):
            self.assertIn(entry, self.lines,
                          f"{entry} is derived per-run state and must be ignored")

    def test_the_disposition_ledger_is_never_ignored(self):
        """Mutation: add `hub/proposals/` to brain-template/.gitignore.

        THE point of that file being tracked: it holds human dispositions and
        the first_seen clock the overdue check measures from, so it must survive
        a re-clone. Ignoring it resets every clock and returns every rejected
        proposal to pending, silently."""
        for line in self.lines:
            self.assertNotIn(
                "proposals", line,
                f"brain-template/.gitignore would untrack the ledger: {line!r}")
