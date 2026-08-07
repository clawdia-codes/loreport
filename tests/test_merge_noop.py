#!/usr/bin/env python3
"""Tests for the no-op guard in hub/brain_merge.py.

The merge runs daily from a timer, and on most days nobody captured anything.
Before this guard, every one of those days still produced two commits — "drop
INDEX.md" and "rebuild INDEX.md" — plus a backup tag, for byte-identical
content. The cost is not untidiness: it buries the days something real happened
under noise, and makes `git log INDEX.md` useless for answering "when did the
catalog actually change?".

The risk in a guard like this is the opposite failure — skipping work that was
genuinely needed. So the tests below are weighted toward proving it does NOT
skip: a stale index still gets rebuilt, and a real capture still gets merged.
"""

import os
import subprocess
import sys
import unittest
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hub"))

import brain_merge  # noqa: E402


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True, check=check)


ITEM = """---
name: {name}
description: {name} hook
type: project
visibility: shared
---

Body.
"""


class NoopGuardTests(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="loreport-noop-")
        self.addCleanup(_rmtree, self.repo)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "test")
        os.makedirs(os.path.join(self.repo, "memories"))
        os.makedirs(os.path.join(self.repo, "prompts"))
        self._write("prompts/bootstrap.md", "# bootstrap\n")
        self._write("PROFILE.md", "# profile\n")
        self._write("memories/seed-item.md", ITEM.format(name="seed-item"))
        self._rebuild_index()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "seed")

    def _write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _rebuild_index(self):
        index, _, _, _ = brain_merge.build_index_bytes(self.repo)
        with open(os.path.join(self.repo, "INDEX.md"), "wb") as fh:
            fh.write(index)

    def _commits(self):
        return int(git(self.repo, "rev-list", "--count", "HEAD").stdout.strip())

    def _tags(self):
        return len([t for t in git(self.repo, "tag").stdout.split() if t])

    def _run_merge(self):
        return brain_merge.do_merge(self.repo, dry_run=False)

    # --- it must skip when there is genuinely nothing to do -----------------

    def test_quiet_day_creates_no_commit_and_no_tag(self):
        before = self._commits()
        report = self._run_merge()
        self.assertTrue(report.get("noop"))
        self.assertEqual(self._commits(), before)
        self.assertEqual(self._tags(), 0)

    def test_repeated_quiet_runs_stay_flat(self):
        before = self._commits()
        for _ in range(3):
            self._run_merge()
        self.assertEqual(self._commits(), before)

    # --- and it must NOT skip when work is actually needed -------------------

    def test_stale_index_is_still_rebuilt(self):
        # An interrupted earlier run can leave the index wrong even though no
        # branch has moved. Skipping here would leave it wrong forever.
        self._write("INDEX.md", "# Index\n\n## Memories\n- [[ghost]] — stale\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "corrupt the index")
        before = self._commits()
        report = self._run_merge()
        self.assertFalse(report.get("noop"))
        self.assertGreater(self._commits(), before)
        with open(os.path.join(self.repo, "INDEX.md"), encoding="utf-8") as fh:
            index = fh.read()
        self.assertIn("[[seed-item]]", index)
        self.assertNotIn("[[ghost]]", index)

    def test_a_real_capture_is_still_merged(self):
        git(self.repo, "checkout", "-qb", "provider/claude")
        self._write("memories/new-capture.md", ITEM.format(name="new-capture"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "capture\n\nTrust: local")
        git(self.repo, "checkout", "-q", "main")

        report = self._run_merge()
        self.assertFalse(report.get("noop"))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "memories", "new-capture.md")))
        with open(os.path.join(self.repo, "INDEX.md"), encoding="utf-8") as fh:
            self.assertIn("[[new-capture]]", fh.read())

    def test_quiet_run_after_a_real_merge_goes_flat_again(self):
        git(self.repo, "checkout", "-qb", "provider/claude")
        self._write("memories/later-capture.md", ITEM.format(name="later-capture"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "capture\n\nTrust: local")
        git(self.repo, "checkout", "-q", "main")
        self._run_merge()

        after_merge = self._commits()
        report = self._run_merge()
        self.assertTrue(report.get("noop"))
        self.assertEqual(self._commits(), after_merge)


class IndexCurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loreport-current-")
        self.addCleanup(_rmtree, self.tmp)
        os.makedirs(os.path.join(self.tmp, "memories"))
        with open(os.path.join(self.tmp, "memories", "a.md"), "w", encoding="utf-8") as fh:
            fh.write(ITEM.format(name="a"))

    def test_missing_index_is_not_current(self):
        self.assertFalse(brain_merge.indexes_are_current(self.tmp))

    def test_matching_index_is_current(self):
        index, _, _, _ = brain_merge.build_index_bytes(self.tmp)
        with open(os.path.join(self.tmp, "INDEX.md"), "wb") as fh:
            fh.write(index)
        self.assertTrue(brain_merge.indexes_are_current(self.tmp))

    def test_stray_archive_file_makes_it_not_current(self):
        # Nothing is expired here, so an INDEX-ARCHIVE.md left behind is wrong
        # and must not be mistaken for an up-to-date brain.
        index, _, _, _ = brain_merge.build_index_bytes(self.tmp)
        with open(os.path.join(self.tmp, "INDEX.md"), "wb") as fh:
            fh.write(index)
        with open(os.path.join(self.tmp, "INDEX-ARCHIVE.md"), "w", encoding="utf-8") as fh:
            fh.write("# Index — Archive\n")
        self.assertFalse(brain_merge.indexes_are_current(self.tmp))


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
