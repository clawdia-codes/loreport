#!/usr/bin/env python3
"""Tests for hub/decide_extract.py — Pass 1b.

This pass exists because 34% of the user's turns in the real corpus are 25 characters or
fewer and the assistant wrote 9x more than he did. It is the only stage that lets
assistant text into a prompt at all, so the guard that matters is the one keeping that
text on the context side of the line: a quote must come from the person, always.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import decide_extract as de  # noqa: E402

AGENT_TEXT = "I propose we use Postgres over SQLite for the tenant store."
USER_TEXT = "yes do that"


class QuoteGateStopsLaundering(unittest.TestCase):
    """The assistant may inform a claim; it may never be quoted as evidence for one."""

    def test_a_claim_quoting_the_assistant_is_dropped(self):
        """THE load-bearing test. Reddened by verifying quotes against the concatenation
        of user and context instead of the user's text alone.

        Without this, a model could lift the agent's own sentence, present it as what the
        person said, and have it 'verified' — which is precisely the attribution failure
        (a gateway-restart notice became a fact about the user) that the quote gate was
        built to prevent.
        """
        raw = ('[{"kind":"decision","claim":"They chose Postgres.",'
               '"verbatim_quote":"I propose we use Postgres over SQLite",'
               '"context_scope":"global"}]')
        kept, rejected = de.parse_and_verify(raw, [USER_TEXT])
        self.assertEqual([], kept)
        self.assertEqual(1, rejected)

    def test_a_claim_quoting_the_person_survives(self):
        """The other direction: a gate that rejected everything would pass the test above.
        Reddened by always returning ([], n)."""
        raw = ('[{"kind":"decision","claim":"They approved using Postgres over SQLite.",'
               '"verbatim_quote":"yes do that","context_scope":"global"}]')
        kept, rejected = de.parse_and_verify(raw, [USER_TEXT])
        self.assertEqual(1, len(kept))
        self.assertEqual(0, rejected)
        self.assertIn("approved", kept[0]["claim"])

    def test_a_claim_with_no_quote_is_dropped(self):
        raw = '[{"kind":"decision","claim":"They chose Postgres.","context_scope":"global"}]'
        kept, _ = de.parse_and_verify(raw, [USER_TEXT])
        self.assertEqual([], kept)

    def test_whitespace_differences_do_not_reject_a_real_quote(self):
        """Reddened by comparing raw strings instead of normalised ones — a model that
        reflows a quote across lines would otherwise lose a genuine finding."""
        kept, _ = de.parse_and_verify(
            '[{"kind":"decision","claim":"They agreed.","verbatim_quote":"yes   do\\n that"}]',
            [USER_TEXT])
        self.assertEqual(1, len(kept))


class ExchangeSelection(unittest.TestCase):
    def test_short_reply_with_context_is_selected(self):
        """This is the whole point: 'yes do that' becomes usable once the proposal above
        it travels with it. Reddened by requiring a minimum reply length."""
        rec = {"turns": [{"text": USER_TEXT, "context": AGENT_TEXT}]}
        self.assertEqual([(USER_TEXT, AGENT_TEXT)], de.exchanges_for_decide(rec))

    def test_short_reply_without_context_is_skipped(self):
        """Nothing to decide about. Sending it anyway invites the model to invent the
        proposal, which is how the Barnum drafts happened. Reddened by dropping the
        context check."""
        rec = {"turns": [{"text": "good", "context": ""}]}
        self.assertEqual([], de.exchanges_for_decide(rec))

    def test_a_fresh_request_is_not_a_verdict(self):
        """The regression that cost a run. Reddened by deleting the is_verdict check.

        "can you shorten it by 50%" is short and has context above it, but it decides
        nothing. Sent to a model asked what the person decided, it came back as "They
        require the assistant to shorten the provided text by 50%" — a one-off task
        promoted to a standing requirement.
        """
        for ask in ("can you shorten it by 50%",
                    "What about Holmenkollenstafetten?",
                    "is there a readout register in the sony imx536?",
                    "give me a one paragraph history of bin picking"):
            rec = {"turns": [{"text": ask, "context": AGENT_TEXT}]}
            self.assertEqual([], de.exchanges_for_decide(rec), ask)

    def test_real_verdicts_are_still_selected(self):
        """The other direction: a selector that rejected everything would pass the test
        above. Reddened by making is_verdict return False."""
        for verdict in ("yes do that", "good", "no, actually let's use SQLite",
                        "perfect", "I'd rather do it the other way", "sounds good"):
            rec = {"turns": [{"text": verdict, "context": AGENT_TEXT}]}
            self.assertEqual(1, len(de.exchanges_for_decide(rec)), verdict)

    def test_long_reply_is_left_to_pass_1a(self):
        """A substantial turn stands on its own and Pass 1a reads it well; re-reading it
        here duplicates findings. Reddened by removing the SHORT_TURN_CHARS ceiling."""
        rec = {"turns": [{"text": "x" * (de.SHORT_TURN_CHARS + 1), "context": AGENT_TEXT}]}
        self.assertEqual([], de.exchanges_for_decide(rec))

    def test_turns_missing_context_do_not_crash(self):
        rec = {"turns": [{"text": "ok"}, {}]}
        self.assertEqual([], de.exchanges_for_decide(rec))


class PromptHygiene(unittest.TestCase):
    def test_the_prompt_labels_the_assistant_text_as_not_the_persons(self):
        """The framing is the safety property, not decoration — the model must never be
        left free to attribute the proposal to the person. Reddened by removing the
        parenthetical from DECIDE_PROMPT."""
        # Normalise whitespace first: the prompt is hard-wrapped, so a literal match on
        # a phrase that happens to straddle a line break fails for no real reason.
        p = " ".join(de.DECIDE_PROMPT.format(context=AGENT_TEXT, reply=USER_TEXT).split())
        self.assertIn("the person did NOT write this", p)
        self.assertIn("Never quote the assistant", p)

    def test_the_identity_prompt_does_not_ask_for_preferences(self):
        """Pass 1a already collects those; asking twice yields duplicates that then look
        like corroboration. Reddened by dropping the exclusion line."""
        self.assertIn("Do NOT extract their preferences", de.IDENTITY_PROMPT)


if __name__ == "__main__":
    unittest.main()
