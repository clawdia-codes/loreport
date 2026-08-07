#!/usr/bin/env python3
"""Tests for hub/observe_extract.py — knowledge-grab Pass 1.

Almost everything here tests the QUOTE GATE, because that is the single mechanism
standing between "a model asserted something about the user" and "the user demonstrably
said it". A fluent model will produce confident, plausible, entirely invented claims about
a person; nothing downstream can distinguish those from real ones. The gate can, cheaply
and deterministically, and it cannot be argued past.

No model is called here — parsing and verification are pure functions by design, so this
suite is fast and hermetic.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

from observe_extract import (  # noqa: E402
    MIN_QUOTE_CHARS,
    normalize,
    parse_observations,
    verify_and_clean,
)

TURNS = [
    "From now on, always give me the bottom line first — dense status writing loses me.",
    "We decided to use Postgres for Coach rather than SQLite, because the multi-tenant "
    "migration needs real concurrent writes.",
    "try now",
]


def obs(kind="fact", claim="A claim", quote=None, scope="global"):
    return {"kind": kind, "claim": claim,
            "verbatim_quote": quote if quote is not None else TURNS[1][:40],
            "context_scope": scope}


class QuoteGateTests(unittest.TestCase):

    def test_real_quote_is_kept(self):
        kept, rejects = verify_and_clean([obs(quote="always give me the bottom line first")], TURNS)
        self.assertEqual(len(kept), 1)
        self.assertEqual(sum(rejects.values()), 0)

    def test_invented_quote_is_rejected(self):
        """NEGATIVE + the whole point: a plausible but never-said quote must not pass.

        The claim reads perfectly and is the sort of thing this user might well believe —
        which is exactly why prompt discipline alone cannot catch it.
        """
        kept, rejects = verify_and_clean([obs(
            claim="Prefers Kubernetes for all deployments",
            quote="I always deploy everything on Kubernetes")], TURNS)
        self.assertEqual(kept, [])
        self.assertEqual(rejects["quote_not_found"], 1)

    def test_paraphrase_is_rejected(self):
        """A near-miss paraphrase of a REAL statement is still not a quote."""
        kept, rejects = verify_and_clean([obs(
            quote="always give me the bottom line up front")], TURNS)  # "up front" != "first"
        self.assertEqual(kept, [])
        self.assertEqual(rejects["quote_not_found"], 1)

    def test_whitespace_and_case_differences_are_tolerated(self):
        """Models reflow whitespace and casing; that must not count as fabrication."""
        kept, _ = verify_and_clean(
            [obs(quote="ALWAYS   GIVE ME\n THE BOTTOM   LINE first")], TURNS)
        self.assertEqual(len(kept), 1)

    def test_too_short_quote_is_rejected(self):
        """A 3-char quote matches almost anything and is not evidence."""
        self.assertLess(len("try now"), MIN_QUOTE_CHARS)
        kept, rejects = verify_and_clean([obs(quote="try now")], TURNS)
        self.assertEqual(kept, [])
        self.assertEqual(rejects["quote_too_short"], 1)

    def test_unknown_kind_is_rejected(self):
        kept, rejects = verify_and_clean(
            [obs(kind="weakness", quote="always give me the bottom line first")], TURNS)
        self.assertEqual(kept, [])
        self.assertEqual(rejects["bad_shape"], 1)

    def test_missing_claim_or_quote_is_rejected(self):
        kept, rejects = verify_and_clean(
            [{"kind": "fact", "claim": "", "verbatim_quote": ""}], TURNS)
        self.assertEqual(kept, [])
        self.assertEqual(rejects["bad_shape"], 1)

    def test_context_scope_defaults_to_global(self):
        kept, _ = verify_and_clean(
            [{"kind": "fact", "claim": "x",
              "verbatim_quote": "always give me the bottom line first"}], TURNS)
        self.assertEqual(kept[0]["context_scope"], "global")

    def test_mixed_batch_keeps_only_the_verifiable(self):
        kept, rejects = verify_and_clean([
            obs(quote="always give me the bottom line first"),      # real
            obs(quote="I always deploy everything on Kubernetes"),  # invented
            obs(quote="Postgres for Coach rather than SQLite"),     # real
        ], TURNS)
        self.assertEqual(len(kept), 2)
        self.assertEqual(rejects["quote_not_found"], 1)


class ResponseParsingTests(unittest.TestCase):

    def test_plain_array(self):
        self.assertEqual(len(parse_observations('[{"kind":"fact"}]')), 1)

    def test_fenced_array(self):
        self.assertEqual(len(parse_observations('```json\n[{"kind":"fact"}]\n```')), 1)

    def test_array_with_surrounding_prose(self):
        raw = 'Sure! Here are the observations:\n[{"kind":"fact"}]\nHope that helps.'
        self.assertEqual(len(parse_observations(raw)), 1)

    def test_garbage_yields_nothing_rather_than_raising(self):
        """A model that ignores the format must degrade to zero, never crash the run."""
        for raw in ("", None, "I could not find anything.", "[unclosed", "{}"):
            self.assertEqual(parse_observations(raw), [])

    def test_non_dict_elements_are_dropped(self):
        self.assertEqual(parse_observations('["a string", {"kind":"fact"}]'),
                         [{"kind": "fact"}])


class NormalizeTests(unittest.TestCase):

    def test_collapses_whitespace_and_lowercases(self):
        self.assertEqual(normalize("  A   B\n\tC  "), "a b c")

    def test_handles_none(self):
        self.assertEqual(normalize(None), "")


if __name__ == "__main__":
    unittest.main()
