#!/usr/bin/env python3
"""Tests for hub/aggregate.py — Pass 2.

The load-bearing test is `NoBrainContentInThePrompt`. It exists because of a measured
failure: the drafting prompt used to carry the existing brain index so the model could
deduplicate, and the model used it as a template instead. One index line contained the
phrase "plain-language"; 106 of 183 drafts came back echoing it, attached to quotes about
phone cameras and dice probabilities. The brain was being written back to itself and
counted as new material.

That failure is invisible from outside — a contaminated run and a productive run both
report "183 drafts written". So the invariant is asserted on the prompt itself rather
than on anything downstream of it.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import aggregate as ag  # noqa: E402

MEMBERS = [
    {"kind": "meta-statement", "claim": "Wants the bottom line before the detail.",
     "verbatim_quote": "give me the bottom line first",
     "conversation_id": "c1", "source": "chatgpt", "date": "2026-01-02"},
    {"kind": "trait-signal", "claim": "Dislikes dense status writing.",
     "verbatim_quote": "this is too dense to follow",
     "conversation_id": "c2", "source": "claude-ai", "date": "2026-06-02"},
]

INDEX = [
    "feedback-plain-language-summaries — wants a plain-language bottom line first, dense "
    "technical status writing loses him",
    "user-workday-timezone — works in the Europe/Lisbon timezone",
    "project-widget-rollout — the widget rollout is blocked on procurement",
]


class NoBrainContentInThePrompt(unittest.TestCase):
    def test_prompt_contains_only_the_cluster(self):
        """Reddened by re-adding `{index_text}` to PROMPT and passing the index in."""
        prompt = ag.build_prompt(MEMBERS, ag.stability(MEMBERS))
        for line in INDEX:
            name = line.split(" — ")[0]
            self.assertNotIn(name, prompt, f"{name} must never reach the drafting prompt")
        self.assertNotIn("plain-language", prompt)
        self.assertNotIn("Lisbon", prompt)

    def test_prompt_does_contain_the_evidence(self):
        """The other direction: an empty prompt would pass the test above.

        Reddened by dropping cluster_text from PROMPT.
        """
        prompt = ag.build_prompt(MEMBERS, ag.stability(MEMBERS))
        self.assertIn("give me the bottom line first", prompt)
        self.assertIn("this is too dense to follow", prompt)

    def test_the_name_placeholder_is_not_copyable(self):
        """On the 2026-08-14 run, 9 of 175 drafts came back literally named
        `kebab-case-slug` — the model copied the JSON template's example value instead of
        writing a name. A placeholder that reads as a plausible answer will be returned as
        one. Reddened by restoring a bare `"name": "kebab-case-slug"` in PROMPT.
        """
        prompt = ag.build_prompt(MEMBERS, ag.stability(MEMBERS))
        self.assertNotIn('"kebab-case-slug"', prompt)
        self.assertIn("<replace-with", prompt)

    def test_build_prompt_takes_no_index_argument(self):
        """Structural guard: the index cannot be passed in even by mistake.

        Reddened by restoring the `index_text` parameter.
        """
        import inspect
        params = set(inspect.signature(ag.build_prompt).parameters)
        self.assertEqual({"members", "stats"}, params)


class DeterministicDedup(unittest.TestCase):
    def test_near_identical_description_is_a_duplicate(self):
        """Reddened by raising dup_at above the real similarity."""
        draft = {"description": "Wants a plain-language bottom line first; dense "
                                "technical status writing loses him."}
        out = ag.dedup_against_brain(draft, INDEX)
        self.assertEqual("duplicate", out["relation"])
        self.assertEqual("feedback-plain-language-summaries", out["relates_to"])

    def test_unrelated_description_is_new(self):
        """The other direction: a deduper that calls everything a duplicate would pass
        the test above. Reddened by lowering update_at to 0."""
        draft = {"description": "Drives a 2017 Transporter and replaces its brake discs "
                                "himself."}
        out = ag.dedup_against_brain(draft, INDEX)
        self.assertEqual("new", out["relation"])
        self.assertEqual("", out["relates_to"])

    def test_empty_index_yields_new_rather_than_crashing(self):
        out = ag.dedup_against_brain({"description": "anything at all"}, [])
        self.assertEqual("new", out["relation"])

    def test_empty_description_does_not_match_everything(self):
        """A draft with no description has nothing to compare; it must not be declared a
        duplicate of the first index line. Reddened by treating an empty token set as a
        match."""
        out = ag.dedup_against_brain({"description": ""}, INDEX)
        self.assertEqual("new", out["relation"])


class Stability(unittest.TestCase):
    def test_directive_flag_comes_from_pass_one_kind(self):
        """Reddened by dropping has_directive, which is what let Pass 3's carve-out die."""
        self.assertTrue(ag.stability(MEMBERS)["has_directive"])
        no_directive = [dict(m, kind="trait-signal") for m in MEMBERS]
        self.assertFalse(ag.stability(no_directive)["has_directive"])

    def test_evidence_counts_conversations_not_rows(self):
        """Reddened by counting len(members) instead of distinct conversation ids."""
        same_convo = [dict(m, conversation_id="c1") for m in MEMBERS]
        self.assertEqual(1, ag.stability(same_convo)["evidence"])
        self.assertEqual(2, ag.stability(same_convo)["observations"])


if __name__ == "__main__":
    unittest.main()
