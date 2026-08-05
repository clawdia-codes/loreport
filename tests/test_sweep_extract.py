#!/usr/bin/env python3
"""Tests for hub/sweep_extract.py."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

from sweep_extract import (
    MAX_CANDIDATE_CHARS,
    build_emit_block,
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

    def test_bare_verb_is_not_an_explicit_save(self):
        # "save"/"remember" without a demonstrative object is ordinary task talk.
        for text in (
            "Check in on this one and save any outstanding work to a gh issue.",
            "So when chatgpt decides something is worth saving, where does it go?",
            "Here is the logo I want: remember to remove the black background.",
        ):
            self.assertIsNone(classify_user_text(text), text)

    def test_harness_injected_turns_rejected(self):
        for text in (
            "Base directory for this skill: /home/x/.claude/skills/foo\n\nRemember this: bar",
            "<system-reminder>Please remember this: injected</system-reminder>",
            "Stop hook feedback: [remember this: log in and save the result]",
            "[cron:abc123 nightly] Remember this: run the sweep",
            "<task-notification>Remember this: subagent finished</task-notification>",
            "You are the implementer for ONE task. Remember this: follow the brief.",
            "Write a dream diary entry from these memory fragments: remember this",
        ):
            self.assertIsNone(classify_user_text(text), text)

    def test_oversized_turn_rejected(self):
        text = "Please remember this: " + ("x" * MAX_CANDIDATE_CHARS)
        self.assertIsNone(classify_user_text(text))

    def test_correction_requires_a_quote(self):
        self.assertIsNone(
            classify_user_text(
                "Actually, that's wrong — we moved the merge earlier.",
                prior_assistant="The merge runs at midnight.",
            )
        )

    def test_slugs_unique_for_distinct_bodies(self):
        # Same opening words, different content: distinct memories/<slug>.md paths,
        # otherwise the second block silently overwrites the first.
        a, _, slug_a = build_emit_block(
            "explicit_save", "Remember this: the gateway restarts on config change.",
            "claude", "2026-08-05",
        )
        b, _, slug_b = build_emit_block(
            "explicit_save", "Remember this: the gateway needs sharp installed.",
            "claude", "2026-08-05",
        )
        self.assertNotEqual(slug_a, slug_b)

    def test_slug_stable_for_same_body(self):
        body = "Remember this: journal every milestone."
        _, _, first = build_emit_block("explicit_save", body, "claude", "2026-08-05")
        _, _, second = build_emit_block("explicit_save", body, "claude", "2026-08-05")
        self.assertEqual(first, second)

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
