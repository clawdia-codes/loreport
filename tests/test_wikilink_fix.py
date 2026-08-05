#!/usr/bin/env python3
"""Tests for hub/wikilink_fix.py."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

from wikilink_fix import rewrite_outside_human_regions, scan_brain


SRC_BRAIN = os.path.join(ROOT, "tests", "fixtures", "wikilink", "brain")


class WikilinkFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wikilink-fix-test-")
        shutil.copytree(SRC_BRAIN, self.tmp, dirs_exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rewrites_unique_hyphen_target(self):
        results, link_total = scan_brain(self.tmp)
        self.assertGreaterEqual(link_total, 1)
        rels = {r["path"] for r in results}
        self.assertIn("memories/bad-link.md", rels)

    def test_skips_human_region(self):
        path = os.path.join(self.tmp, "memories", "human-protected.md")
        with open(path, "r", encoding="utf-8") as fh:
            original = fh.read()
        updated, links = rewrite_outside_human_regions(original, self.tmp)
        self.assertIn("[[foo_bar]]", updated.split("human:end")[0] + updated.split("human:end")[1])
        self.assertIn("Inside [[foo_bar]]", updated)
        self.assertEqual(len(links), 1)

    def test_apply_writes(self):
        results, _ = scan_brain(self.tmp)
        bad_path = os.path.join(self.tmp, "memories", "bad-link.md")
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write(results[0]["updated"])
        with open(bad_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("[[foo-bar]]", text)
        self.assertNotIn("[[foo_bar]]", text)


if __name__ == "__main__":
    unittest.main()
