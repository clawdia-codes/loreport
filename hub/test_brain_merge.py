#!/usr/bin/env python3
"""Unit tests for hub/brain_merge.py human-region guard."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain_merge import extract_human_regions, human_region_violation


def _wrap(body):
    return f"intro\n<!-- human:start -->{body}<!-- human:end -->\noutro"


class HumanRegionGuardTests(unittest.TestCase):
    def test_preserved_verbatim_passes(self):
        main = _wrap("keep me")
        incoming = "new intro\n" + _wrap("keep me") + "\nnew outro"
        self.assertIsNone(human_region_violation(main, incoming))

    def test_dropped_region_quarantines(self):
        main = _wrap("must stay") + "\n" + _wrap("also stay")
        incoming = _wrap("must stay")
        self.assertEqual(human_region_violation(main, incoming), "dropped human region(s)")

    def test_altered_region_quarantines(self):
        main = _wrap("original text")
        incoming = _wrap("changed text")
        self.assertEqual(human_region_violation(main, incoming), "altered human region")

    def test_reordered_but_verbatim_regions_pass(self):
        main = _wrap("alpha") + "\n" + _wrap("beta")
        incoming = _wrap("beta") + "\n" + _wrap("alpha")
        self.assertIsNone(human_region_violation(main, incoming))

    def test_file_without_regions_unaffected(self):
        main = "plain memory body"
        incoming = "totally different body"
        self.assertIsNone(human_region_violation(main, incoming))

    def test_extract_human_regions_pairwise_in_order(self):
        text = _wrap("one") + "\nmid\n" + _wrap("two")
        self.assertEqual(extract_human_regions(text), ["one", "two"])


if __name__ == "__main__":
    unittest.main()
