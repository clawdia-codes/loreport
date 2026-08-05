#!/usr/bin/env python3
"""Tests for hub/sweep_run.py — the ledger and provider rules it owns.

The gate chain (parse/schema/secret/imperative/commit) belongs to inbox_ingest and
is tested there; what is exercised here is what this runner adds on top: never
offering the same candidate twice, never inventing a chatgpt sweep, and a dry run
that changes nothing on disk.
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import sweep_run  # noqa: E402


FAKE_CANDIDATES = [
    {"fingerprint": "aaa", "provider": "claude", "slug": "user-a", "kind": "explicit_save",
     "block": "block-a", "log": "x"},
    {"fingerprint": "bbb", "provider": "openclaw", "slug": "user-b", "kind": "decision",
     "block": "block-b", "log": "y"},
    {"fingerprint": "ccc", "provider": "chatgpt", "slug": "user-c", "kind": "explicit_save",
     "block": "block-c", "log": "z"},
]


class SweepRunTests(unittest.TestCase):
    def setUp(self):
        self._scan = sweep_run.extract.scan_logs
        self._process = sweep_run.process_candidate
        sweep_run.extract.scan_logs = lambda *a, **k: list(FAKE_CANDIDATES)
        self.processed = []

        def fake_process(brain_dir, candidate, block_path, trust):
            self.processed.append(candidate["fingerprint"])
            return "committed"

        sweep_run.process_candidate = fake_process
        self.tmp = tempfile.mkdtemp(prefix="sweep-run-test-")
        self.state = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        sweep_run.extract.scan_logs = self._scan
        sweep_run.process_candidate = self._process

    def test_chatgpt_is_never_swept(self):
        summary = sweep_run.run(self.tmp, 1, self.state, False, "local")
        self.assertNotIn("ccc", self.processed)
        self.assertEqual(summary["skipped_provider"], 1)

    def test_second_run_offers_nothing(self):
        first = sweep_run.run(self.tmp, 1, self.state, False, "local")
        second = sweep_run.run(self.tmp, 1, self.state, False, "local")
        self.assertEqual(first["committed"], 2)
        self.assertEqual(second["committed"], 0)
        self.assertEqual(second["already_seen"], 2)
        self.assertEqual(self.processed, ["aaa", "bbb"])

    def test_ledger_records_outcomes(self):
        sweep_run.run(self.tmp, 1, self.state, False, "local")
        with open(self.state, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(set(state["seen"]), {"aaa", "bbb"})
        self.assertEqual(state["seen"]["aaa"]["outcome"], "committed")
        self.assertIn("last_run", state)

    def test_dry_run_commits_nothing_and_remembers_nothing(self):
        summary = sweep_run.run(self.tmp, 1, self.state, True, "local")
        self.assertEqual(self.processed, [])
        self.assertEqual(summary["committed"], 0)
        self.assertFalse(os.path.exists(self.state))

    def test_ledger_default_lives_outside_the_brain_repo(self):
        # sync.sh halts the nightly pipeline on a dirty tree, and the sweep runs
        # 30 minutes before it.
        self.assertNotIn("loreport-oyvind-theie", sweep_run.DEFAULT_STATE_FILE)


if __name__ == "__main__":
    unittest.main()
