#!/usr/bin/env python3
"""Unit tests for hub/brain_merge.py human-region guard."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain_merge import (
    apply_human_region_guard,
    extract_human_regions,
    human_region_violation,
)


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


class HumanRegionGuardIntegrationTests(unittest.TestCase):
    """The pure-function tests above prove the DETECTOR. These prove the GUARD:
    that a real merge which strips a human region ends with main's copy intact, a
    quarantine file on disk, and a digest line saying why."""

    def setUp(self):
        self.brain = tempfile.mkdtemp(prefix="loreport-guard-test-")
        self.addCleanup(shutil.rmtree, self.brain, ignore_errors=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        os.makedirs(os.path.join(self.brain, "memories"))
        self.rel = "memories/protected.md"
        self.write(self.rel, _wrap("Øyvind's own words"))
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", self.brain, *args],
            capture_output=True, text=True, check=True,
        )

    def write(self, rel, text):
        with open(os.path.join(self.brain, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, rel):
        with open(os.path.join(self.brain, rel), "r", encoding="utf-8") as fh:
            return fh.read()

    def _merge_branch_that(self, mutate):
        """Build provider/openclaw off main, apply `mutate`, merge it, run guard."""
        self.git("checkout", "-q", "-b", "provider/openclaw")
        mutate()
        self.git("add", "-A")
        self.git("commit", "-qm", "provider edit")
        self.git("checkout", "-q", "main")
        head_before = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("merge", "-q", "--no-ff", "-m", "merge", "provider/openclaw")
        report = {"human_region_violations": []}
        apply_human_region_guard(self.brain, head_before, "provider/openclaw", report)
        return report

    def _digest(self):
        path = os.path.join(self.brain, "hub", "quarantine", "digest.md")
        return self.read(os.path.relpath(path, self.brain)) if os.path.isfile(path) else ""

    def test_stripped_region_is_reverted_quarantined_and_digested(self):
        report = self._merge_branch_that(
            lambda: self.write(self.rel, "agent rewrote the whole body")
        )
        # 1. main's body survives verbatim
        self.assertIn("Øyvind's own words", self.read(self.rel))
        self.assertNotIn("agent rewrote", self.read(self.rel))
        # 2. the rejected version is parked, not lost
        qdir = os.path.join(self.brain, "hub", "quarantine", "provider-openclaw")
        parked = os.listdir(qdir) if os.path.isdir(qdir) else []
        if not parked:  # provider dir name follows _branch_provider_name()
            root = os.path.join(self.brain, "hub", "quarantine")
            parked = [
                os.path.join(d, f)
                for d in os.listdir(root)
                if os.path.isdir(os.path.join(root, d))
                for f in os.listdir(os.path.join(root, d))
            ]
        self.assertTrue(parked, "rejected update was dropped instead of quarantined")
        # 3. the digest says which file and why
        digest = self._digest()
        self.assertIn("human-region-guard", digest)
        self.assertIn(self.rel, digest)
        self.assertEqual(len(report["human_region_violations"]), 1)
        # 4. the guard leaves the tree committed and clean -- sync.sh HALTS the whole
        #    nightly run on a dirty tree, so a guard that dirties it breaks projection.
        self.assertEqual(
            self.git("status", "--porcelain", "--untracked-files=no").stdout.strip(), ""
        )

    def test_deleting_a_protected_file_is_reverted_not_a_crash(self):
        """A provider branch deleting the file used to raise FileNotFoundError and
        abort the merge mid-flight."""
        report = self._merge_branch_that(
            lambda: os.remove(os.path.join(self.brain, self.rel))
        )
        self.assertIn("Øyvind's own words", self.read(self.rel))
        self.assertEqual(len(report["human_region_violations"]), 1)

    def test_edit_outside_the_region_is_allowed_through(self):
        new = "rewritten intro\n<!-- human:start -->Øyvind's own words<!-- human:end -->\nrewritten outro"
        report = self._merge_branch_that(lambda: self.write(self.rel, new))
        self.assertEqual(self.read(self.rel), new)
        self.assertEqual(report["human_region_violations"], [])

    def test_profile_md_is_protected_too(self):
        """design-wiki-parity §1: PROFILE.md carries a human region and is projected
        into every provider surface -- it must be inside the guard's path scope."""
        self.write("PROFILE.md", _wrap("Boundaries: never share secrets"))
        self.git("add", "-A")
        self.git("commit", "-qm", "profile with human region")
        report = self._merge_branch_that(
            lambda: self.write("PROFILE.md", "agent flattened the profile")
        )
        self.assertIn("never share secrets", self.read("PROFILE.md"))
        self.assertEqual(len(report["human_region_violations"]), 1)


if __name__ == "__main__":
    unittest.main()
