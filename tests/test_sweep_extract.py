#!/usr/bin/env python3
"""Tests for hub/sweep_extract.py."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

from sweep_extract import (
    classify_user_text,
    content_fingerprint,
    scan_logs,
)


FIXTURES = os.path.join(ROOT, "tests", "fixtures", "sweep")


class SweepExtractTests(unittest.TestCase):
    def test_explicit_save_detected(self):
        kind, _ = classify_user_text("Please remember this: always verify merges.")
        self.assertEqual(kind, "explicit_save")

    def test_decision_detected(self):
        kind, _ = classify_user_text("Ruling: keep synthesis report-only.")
        self.assertEqual(kind, "decision")

    def test_correction_with_quote(self):
        kind, _ = classify_user_text(
            "You said \"midnight\" but that's wrong — it's 00:00 UTC.",
            prior_assistant="The merge runs at midnight.",
        )
        self.assertEqual(kind, "correction")

    def test_low_signal_dropped(self):
        self.assertIsNone(classify_user_text("ok"))

    def test_fingerprint_idempotent(self):
        a = content_fingerprint("Hello   world")
        b = content_fingerprint("hello world")
        self.assertEqual(a, b)

    def test_fixture_logs_produce_candidates(self):
        paths = [
            os.path.join(FIXTURES, "claude", "sample.jsonl"),
            os.path.join(FIXTURES, "codex", "sample.jsonl"),
            os.path.join(FIXTURES, "openclaw", "sample.jsonl"),
        ]
        candidates = scan_logs(since_ts=None, window_days=None, extra_paths=paths, paths_only=True)
        self.assertGreaterEqual(len(candidates), 4)
        kinds = {c["kind"] for c in candidates}
        self.assertIn("explicit_save", kinds)
        self.assertIn("decision", kinds)
        self.assertIn("correction", kinds)
        fps = [c["fingerprint"] for c in candidates]
        self.assertEqual(len(fps), len(set(fps)))

    def test_rerun_same_window_zero_new_dupes(self):
        paths = [os.path.join(FIXTURES, "claude", "sample.jsonl")]
        first = scan_logs(None, None, extra_paths=paths, paths_only=True)
        second = scan_logs(None, None, extra_paths=paths, paths_only=True)
        self.assertEqual(len(first), len(second))
        self.assertEqual(
            sorted(c["fingerprint"] for c in first),
            sorted(c["fingerprint"] for c in second),
        )

    def test_emit_block_shape(self):
        paths = [os.path.join(FIXTURES, "codex", "sample.jsonl")]
        candidates = scan_logs(None, None, extra_paths=paths, paths_only=True)
        self.assertTrue(candidates)
        block = candidates[0]["block"]
        self.assertIn("<MEMORY file=", block)
        self.assertIn("INDEX:", block)


if __name__ == "__main__":
    unittest.main()
