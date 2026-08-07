#!/usr/bin/env python3
"""Tests for hub/corpus_prep.py — knowledge-grab Pass 0.

The two tests that matter here are negative ones, and both encode a failure that was
observed in production rather than imagined:

  1. A "[System] …" gateway notice became a candidate memory *about the user* on
     2026-08-06. Pass 0 must never let that class of text reach a model.
  2. Transcripts demonstrably contain pasted credentials (a PAT once spread through
     Clawdia's dreaming pipeline). Nothing credential-shaped may survive into the
     intermediate artifact.

Both are asserted against the real code path — `build_records` over a fixture log — not
against the helper functions in isolation, because the bug this guards against is a
missing call, not a wrong regex.
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

from corpus_prep import build_records, user_turns_from_log  # noqa: E402


def write_claude_log(path, entries):
    """Minimal Claude Code .jsonl shape: one JSON object per line."""
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def user_entry(text, **extra):
    e = {"type": "user", "message": {"role": "user", "content": text},
         "timestamp": "2026-08-01T10:00:00Z"}
    e.update(extra)
    return e


def assistant_entry(text):
    return {"type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            "timestamp": "2026-08-01T10:00:01Z"}


class CorpusPrepTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "sample.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def _records(self):
        return build_records(sources=(), extra_logs=[self.log])

    def _all_text(self, records):
        return "\n".join(t["text"] for r in records for t in r["turns"])

    # ---- the two mandatory negative tests -----------------------------------

    def test_system_injection_never_reaches_the_artifact(self):
        """NEGATIVE: a "[System] " gateway notice must not survive Pass 0.

        Observed 2026-08-06: this exact text became a candidate memory about the user.
        """
        write_claude_log(self.log, [
            user_entry("[System] Your previous turn was interrupted by a gateway "
                       "restart while OpenClaw was waiting on tool/model work. "
                       "Continue from where you left off."),
            user_entry("Let's use Postgres for the Coach database, not SQLite."),
        ])
        records = self._records()
        text = self._all_text(records)
        self.assertNotIn("previous turn was interrupted", text)
        self.assertNotIn("[System]", text)
        # Positive control: the genuine turn in the SAME log must survive, proving the
        # filter is selective rather than dropping the conversation wholesale.
        self.assertIn("Postgres", text)

    def test_pasted_credentials_are_redacted(self):
        """NEGATIVE: credential-shaped strings must not survive into the artifact."""
        write_claude_log(self.log, [
            user_entry("here is the token ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "
                       "use it for the deploy"),
            user_entry("and the key is sk-abcdefghijklmnopqrstuvwxyz012345"),
        ])
        text = self._all_text(self._records())
        self.assertNotIn("ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", text)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz012345", text)
        self.assertIn("REDACTED", text)

    def test_marker_buried_past_the_head_is_still_caught(self):
        """NEGATIVE: a harness marker deep inside a long turn must still be caught.

        Observed on the live corpus 2026-08-07: two `/security-review` payloads (14,996
        and 42,399 chars) leaked into the artifact because `sweep_extract.is_synthetic_turn`
        only scans the first 400 characters and their markers sat at char 2378 / 4024.
        """
        buried = ("Review this change for security vulnerabilities.\n\n"
                  + ("padding line that looks like ordinary prose. " * 60)
                  + "\n<system-reminder>harness text</system-reminder>\n"
                  + ("more padding. " * 40))
        self.assertGreater(buried.lower().index("<system-reminder>"), 400,
                           "fixture must place the marker past the head window")
        write_claude_log(self.log, [
            user_entry(buried),
            user_entry("Coach should use Postgres, not SQLite."),
        ])
        text = self._all_text(self._records())
        self.assertNotIn("system-reminder", text)
        self.assertIn("Postgres", text)  # positive control

    def test_absurdly_long_turn_is_dropped(self):
        """A human does not type 42,000 characters; that is an injected payload."""
        write_claude_log(self.log, [
            user_entry("x" * 42000),
            user_entry("Coach should use Postgres, not SQLite."),
        ])
        records = self._records()
        self.assertEqual(records[0]["turn_count"], 1)
        self.assertIn("Postgres", self._all_text(records))

    # ---- the filter is selective, not indiscriminate -------------------------

    def test_assistant_turns_are_excluded(self):
        write_claude_log(self.log, [
            user_entry("Remember that the tunnel id override lives in .env."),
            assistant_entry("I have noted that the tunnel id override lives in .env."),
        ])
        text = self._all_text(self._records())
        self.assertIn("Remember that the tunnel id", text)
        self.assertNotIn("I have noted", text)

    def test_tool_results_wearing_role_user_are_excluded(self):
        """A turn carrying toolUseResult is a tool result, not the human speaking."""
        write_claude_log(self.log, [
            user_entry("total 48\ndrwxr-xr-x 2 nvidia nvidia 4096 Aug 1 10:00 build",
                       toolUseResult={"stdout": "..."}),
            user_entry("The build directory should be gitignored."),
        ])
        text = self._all_text(self._records())
        self.assertNotIn("drwxr-xr-x", text)
        self.assertIn("gitignored", text)

    def test_subagent_and_meta_turns_are_excluded(self):
        write_claude_log(self.log, [
            user_entry("subagent chatter that is not the user", isSidechain=True),
            user_entry("harness notice that is not the user", isMeta=True),
            user_entry("Deploy Coach with sg docker -c, never plain docker."),
        ])
        text = self._all_text(self._records())
        self.assertNotIn("subagent chatter", text)
        self.assertNotIn("harness notice", text)
        self.assertIn("sg docker", text)

    # ---- shape and idempotence ----------------------------------------------

    def test_empty_conversation_is_dropped_not_emitted(self):
        """A log whose turns all filter away must produce NO record, not an empty one.

        An empty record downstream reads as "this conversation had nothing to say",
        which is a different claim from "this was never the user".
        """
        write_claude_log(self.log, [
            user_entry("[System] gateway restarted"),
            assistant_entry("ok"),
        ])
        self.assertEqual(self._records(), [])

    def test_duplicate_turns_within_a_conversation_collapse(self):
        write_claude_log(self.log, [
            user_entry("Use Postgres for Coach, not SQLite."),
            user_entry("Use Postgres for Coach, not SQLite."),
            user_entry("And put the login UI behind Zitadel."),
        ])
        records = self._records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["turn_count"], 2)

    def test_record_shape(self):
        write_claude_log(self.log, [user_entry("Coach should use Postgres, not SQLite.")])
        r = self._records()[0]
        for key in ("conversation_id", "source", "date", "log", "turn_count", "turns"):
            self.assertIn(key, r)
        self.assertEqual(r["date"], "2026-08-01")
        self.assertTrue(r["turns"][0]["text"])

    def test_rerun_is_deterministic(self):
        write_claude_log(self.log, [user_entry("Coach should use Postgres, not SQLite.")])
        self.assertEqual(json.dumps(self._records(), sort_keys=True),
                         json.dumps(self._records(), sort_keys=True))

    def test_unreadable_log_does_not_abort_the_pass(self):
        """One corrupt log must not cost the whole corpus."""
        bad = os.path.join(self.tmp.name, "corrupt.jsonl")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
        write_claude_log(self.log, [user_entry("Coach should use Postgres, not SQLite.")])
        records = build_records(sources=(), extra_logs=[bad, self.log])
        self.assertEqual(len(records), 1)
        self.assertIn("Postgres", self._all_text(records))


if __name__ == "__main__":
    unittest.main()
