#!/usr/bin/env python3
"""Tests for hub/screen.py — Pass 3's arithmetic gate.

These exist because of a real failure, not a hypothetical one. The rule is "kill when
evidence is under 3 conversations AND the cluster is not an explicit directive". The
first implementation read the directive half off the draft's `type` field, and on the
first full run over the real corpus 146 of 183 drafts came back typed `person` — so the
carve-out never fired once, the gate killed 166 drafts it was supposed to keep, and the
whole funnel yielded zero survivors against a plan that expected 30-80.

The lesson generalises past this function: a conditional whose guard never evaluates true
is indistinguishable from a missing feature, and no test that only checks the kill path
would have caught it. Both directions are asserted here for that reason.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import screen  # noqa: E402


def draft(evidence=1, has_directive=False, dtype="user"):
    return {
        "name": "d", "type": dtype, "description": "x", "body": "y",
        "stability": {"evidence": evidence, "has_directive": has_directive},
    }


class ThinEvidenceGate(unittest.TestCase):
    def test_thin_and_not_a_directive_dies(self):
        """The kill path. Reddened by returning False unconditionally."""
        self.assertTrue(screen.thin_evidence(draft(evidence=1, has_directive=False)))
        self.assertTrue(screen.thin_evidence(draft(evidence=2, has_directive=False)))

    def test_a_directive_survives_on_one_telling(self):
        """The carve-out. Reddened by deleting the has_directive branch.

        This is the assertion that was missing. The user only has to say "always give me
        the bottom line first" once; requiring three sightings would discard exactly the
        instructions they were most explicit about.
        """
        self.assertFalse(screen.thin_evidence(draft(evidence=1, has_directive=True)))

    def test_broad_evidence_survives_without_a_directive(self):
        """Reddened by raising the threshold above 3."""
        self.assertFalse(screen.thin_evidence(draft(evidence=3, has_directive=False)))

    def test_the_carve_out_does_not_depend_on_the_draft_type(self):
        """The actual regression. Reddened by restoring the `type in (...)` check.

        A directive typed `person` — the label the model assigned to 146 of 183 real
        drafts — must still survive. Whether the user issued a directive is a fact about
        the source text; the type is a label a later model chose.
        """
        for dtype in ("person", "user", "project", "reference", "fact", "feedback"):
            self.assertFalse(
                screen.thin_evidence(draft(evidence=1, has_directive=True, dtype=dtype)),
                f"a directive typed {dtype!r} must survive the arithmetic gate",
            )

    def test_missing_stability_fails_closed(self):
        """A draft with no stability block is not evidence of anything. Reddened by
        defaulting has_directive to True."""
        self.assertTrue(screen.thin_evidence({"name": "d", "type": "user"}))


class Scoring(unittest.TestCase):
    def test_rank_prefers_high_confidence_and_impact(self):
        """Reddened by swapping the sort to ascending, or by ignoring impact."""
        strong = screen.score(draft(evidence=5),
                              {"confidence": "high", "behavioral_impact": "high"})
        weak = screen.score(draft(evidence=5),
                            {"confidence": "low", "behavioral_impact": "low"})
        self.assertGreater(strong, weak)

    def test_unknown_labels_do_not_crash_and_rank_lowest(self):
        """A model that answers 'quite high' must not take down the run."""
        odd = screen.score(draft(evidence=1),
                           {"confidence": "quite high", "behavioral_impact": None})
        floor = screen.score(draft(evidence=1),
                             {"confidence": "low", "behavioral_impact": "low"})
        self.assertEqual(odd, floor)


if __name__ == "__main__":
    unittest.main()
