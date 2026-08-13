#!/usr/bin/env python3
"""Tests for hub/export_readers.py — the provider-export Pass 0 adapters.

The load-bearing test here is the allowlist one. The OpenAI download is a Privacy Center
archive: alongside the conversations it carries Financial/Charge History.csv, Payments
Invoices, a Payments Customer Profile, Contact Info, and a 196 MB archive of uploaded
files. The user's standing boundary is that finances stay `visibility: local` and never
reach the shared tier, so none of that may enter the pipeline at all.

Every test names the single-line production change that reddens it, and each of those
mutations was run rather than asserted.
"""

import io
import json
import os
import sys
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import export_readers as er  # noqa: E402

FINANCIAL_SECRET = "CHARGE-HISTORY-4111-1111-1111-1111"


def make_chatgpt_archive(path, conversations, include_financial=True):
    """Build an archive shaped like the real OpenAI Privacy Center download."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("conversations-000.json", json.dumps(conversations))
        z.writestr("chat.html", "<html>irrelevant</html>")
    with zipfile.ZipFile(path, "w") as outer:
        outer.writestr(
            "User Online Activity/Conversations__abc-chatgpt-0001.zip", inner.getvalue()
        )
        if include_financial:
            # Exactly the members the real archive carries beside the conversations.
            outer.writestr("Financial/Charge History.csv", f"amount,card\n99.00,{FINANCIAL_SECRET}\n")
            outer.writestr("Financial/Payments Invoices.csv", f"invoice,{FINANCIAL_SECRET}\n")
            outer.writestr("User Profile/Payments Customer Profile.csv", FINANCIAL_SECRET)
            outer.writestr("Contact Info/Contact Info.csv", FINANCIAL_SECRET)
            outer.writestr("User Online Activity/Files__abc-files-0001.zip", b"PK\x03\x04junk")
            outer.writestr("User Online Activity/Ads__abc-ads-0001.zip", b"PK\x03\x04junk")


def convo(text="I prefer self-hosted tools.", content_type="text",
          do_not_remember=False, cid="c1", parts=None):
    return {
        "conversation_id": cid,
        "title": "t",
        "create_time": 1700000000.0,
        "is_do_not_remember": do_not_remember,
        "mapping": {
            "n1": {"id": "n1", "parent": None, "children": [],
                   "message": {
                       "id": "m1",
                       "author": {"role": "user"},
                       "create_time": 1700000000.0,
                       "metadata": {},
                       "content": {"content_type": content_type,
                                   "parts": parts if parts is not None else [text]},
                   }},
            "n2": {"id": "n2", "parent": "n1", "children": [],
                   "message": {
                       "id": "m2",
                       "author": {"role": "assistant"},
                       "create_time": 1700000001.0,
                       "metadata": {},
                       "content": {"content_type": "text", "parts": ["assistant reply"]},
                   }},
        },
    }


class AllowlistKeepsFinanceOut(unittest.TestCase):
    """The safety property. Reddened by widening CHATGPT_OUTER_PREFIX to ''."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "openai.zip")
        make_chatgpt_archive(self.path, [convo()])

    def test_policy_names_only_conversation_archives(self):
        with zipfile.ZipFile(self.path) as z:
            names = z.namelist()
        allowed = er.chatgpt_allowed_outer_members(names)
        self.assertEqual(
            ["User Online Activity/Conversations__abc-chatgpt-0001.zip"], allowed
        )
        # Assert the negative directly rather than trusting the positive.
        for n in names:
            if n.startswith(("Financial/", "Contact Info/", "User Profile/")) or "Files__" in n:
                self.assertNotIn(n, allowed, f"{n} must never be opened")

    def test_financial_content_never_reaches_a_record(self):
        records, _ = er.read_chatgpt_export(self.path)
        blob = json.dumps(records)
        self.assertNotIn(FINANCIAL_SECRET, blob)
        self.assertNotIn("Charge History", blob)

    def test_the_fixture_actually_contains_the_finance_data(self):
        """Guards against a vacuous pass: if the fixture had no financial member, the
        test above would pass while proving nothing. This repo has shipped exactly that
        shape before — a privacy check that passed on an empty packet."""
        with zipfile.ZipFile(self.path) as z:
            self.assertIn("Financial/Charge History.csv", z.namelist())
            self.assertIn(FINANCIAL_SECRET, z.read("Financial/Charge History.csv").decode())


class ChatGPTTurnFilter(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def _read(self, conversations):
        path = os.path.join(self.tmp, f"a{len(os.listdir(self.tmp))}.zip")
        make_chatgpt_archive(path, conversations, include_financial=False)
        return er.read_chatgpt_export(path)

    def test_keeps_typed_user_text_and_drops_assistant(self):
        """Reddened by removing the author.role == 'user' check."""
        records, _ = self._read([convo(text="I prefer self-hosted tools.")])
        self.assertEqual(1, len(records))
        self.assertEqual(1, records[0]["turn_count"])
        self.assertIn("self-hosted", records[0]["turns"][0]["text"])
        self.assertNotIn("assistant reply", json.dumps(records))

    def test_keeps_typed_text_from_multimodal_turns(self):
        """Reddened by dropping 'multimodal_text' from the accepted content types.

        1,659 messages in the real corpus are multimodal; the text typed beside an image
        lives in that message's string parts and would vanish silently.
        """
        records, _ = self._read([convo(
            content_type="multimodal_text",
            parts=[{"asset_pointer": "file-abc"}, "what is wrong with this chart?"],
        )])
        self.assertEqual(1, len(records))
        self.assertIn("what is wrong with this chart?", records[0]["turns"][0]["text"])

    def test_image_pointers_do_not_become_text(self):
        """Reddened by dropping the isinstance(p, str) filter on parts."""
        records, _ = self._read([convo(
            content_type="multimodal_text",
            parts=[{"asset_pointer": "file-SECRETPOINTER"}, "hello"],
        )])
        self.assertNotIn("SECRETPOINTER", json.dumps(records))

    def test_custom_instructions_are_not_typed_turns(self):
        """Reddened by removing the is_user_system_message check."""
        c = convo(text="CUSTOM-INSTRUCTION-BLOB")
        c["mapping"]["n1"]["message"]["metadata"]["is_user_system_message"] = True
        records, skipped = self._read([c])
        self.assertEqual([], records)
        self.assertEqual({"no_user_turns": 1}, skipped)

    def test_do_not_remember_is_honoured(self):
        """Reddened by deleting the do_not_remember branch in should_skip_conversation."""
        records, skipped = self._read([convo(do_not_remember=True)])
        self.assertEqual([], records)
        self.assertEqual({"do_not_remember": 1}, skipped)

    def test_ordinary_conversations_are_not_skipped(self):
        """The other direction: a policy that skips everything would pass the test above.

        Reddened by making should_skip_conversation return (True, 'x') unconditionally.
        """
        records, skipped = self._read([convo(do_not_remember=False)])
        self.assertEqual(1, len(records))
        self.assertNotIn("do_not_remember", skipped)


class ClaudeAiTurnFilter(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "claude.zip")

    def _write(self, convos):
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("conversations.json", json.dumps(convos))
            z.writestr("memories.json", json.dumps({"unrelated": "MEMORIES-BLOB"}))
            z.writestr("login_history.json", "LOGIN-BLOB")
        return er.read_claude_ai_export(self.path)

    def test_human_text_kept_tool_result_dropped(self):
        """Reddened by accepting content blocks whose type is not 'text'.

        A tool result wearing a user turn is the attribution error that once promoted a
        gateway-restart notice into a candidate memory about the user.
        """
        records, _ = self._write([{
            "uuid": "u1", "name": "n", "created_at": "2026-03-02T10:00:00Z",
            "chat_messages": [
                {"sender": "human", "text": "", "created_at": "2026-03-02T10:00:00Z",
                 "content": [{"type": "text", "text": "I train four times a week."},
                             {"type": "tool_result", "text": "TOOL-RESULT-BLOB"}]},
                {"sender": "assistant", "text": "ASSISTANT-BLOB",
                 "created_at": "2026-03-02T10:01:00Z", "content": []},
            ],
        }])
        blob = json.dumps(records)
        self.assertIn("I train four times a week.", blob)
        self.assertNotIn("TOOL-RESULT-BLOB", blob)
        self.assertNotIn("ASSISTANT-BLOB", blob)

    def test_only_conversations_json_is_read(self):
        """Reddened by pointing CLAUDE_MEMBER at another member or globbing the archive."""
        records, _ = self._write([{
            "uuid": "u1", "name": "n", "created_at": "2026-03-02T10:00:00Z",
            "chat_messages": [{"sender": "human", "text": "hello",
                               "created_at": "2026-03-02T10:00:00Z", "content": []}],
        }])
        blob = json.dumps(records)
        self.assertNotIn("MEMORIES-BLOB", blob)
        self.assertNotIn("LOGIN-BLOB", blob)

    def test_empty_messages_produce_no_record(self):
        """The 2024 conversations in the real export are genuinely empty — no text, no
        content blocks. Reddened by emitting a record when turns is empty."""
        records, skipped = self._write([{
            "uuid": "u1", "name": "n", "created_at": "2024-07-08T10:00:00Z",
            "chat_messages": [{"sender": "human", "text": "",
                               "created_at": "2024-07-08T10:00:00Z", "content": []}],
        }])
        self.assertEqual([], records)
        self.assertEqual({"no_user_turns": 1}, skipped)


if __name__ == "__main__":
    unittest.main()
