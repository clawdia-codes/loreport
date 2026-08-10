#!/usr/bin/env python3
"""Tests for inbox_ingest.commit_block's post-failure cleanup.

The defect these guard against was live data loss, measured twice on
2026-08-07/08: the failure handler ran `git checkout -- .` plus a pathless
`git reset`, which discards EVERY uncommitted change in the shared brain
working tree, not just the path the capture wrote. A session's hand-edit was
destroyed at 23:06:25.069, 7ms before the quarantine file was written.

It was self-reinforcing, and that is why `test_dirty_capture_path_survives_
when_the_branch_checkout_itself_fails` exists: a dirty tree is exactly what
makes `git checkout provider/<host>` fail, so the handler wiped the tree and
destroyed the very edit that caused the failure. Scoping the cleanup to
`block["file"]` is NOT sufficient for that case — the capture mutated nothing,
so the correct amount of repair is none. Both properties need their own test.

Everything runs `commit_block` against a real git repo with real failures
(a `pre-commit` hook that exits 1; a genuinely dirty tree blocking a branch
switch) rather than asserting on the handler in isolation, because the failure
mode is a command's blast radius, not a wrong comparison.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hub"))

import inbox_ingest  # noqa: E402
from inbox_ingest import CleanupError, commit_block  # noqa: E402

PROVIDER = "claude"
BRANCH = f"provider/{PROVIDER}"
HAND_EDIT = "a session's uncommitted hand-edit\n"


def _item(name, body="seeded body"):
    return (
        "---\n"
        f"name: {name}\n"
        "description: hook\n"
        "type: project\n"
        "visibility: shared\n"
        f"source: {PROVIDER}\n"
        "---\n\n"
        f"{body}\n"
    )


def _block(name, action="update", body=None, path=None):
    return {
        "file": path or f"memories/{name}.md",
        "action": action,
        "body": _item(name, body) if body is not None else _item(name),
        "index_line": f"INDEX: - [[{name}]] — hook",
        "raw": "",
    }


class _BrainRepo(unittest.TestCase):
    """A two-branch brain (main + provider/claude) with real git history."""

    def setUp(self):
        self.brain = tempfile.mkdtemp(prefix="loreport-cleanup-")
        self.addCleanup(shutil.rmtree, self.brain, True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.brain], check=True)
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "test")

        os.makedirs(os.path.join(self.brain, "memories"))
        self.write("memories/target.md", _item("target"))
        self.write("memories/unrelated.md", _item("unrelated"))
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")
        self.git("branch", BRANCH)

    # --- helpers ---------------------------------------------------------
    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", self.brain] + list(args),
                              capture_output=True, text=True, check=check)

    def write(self, rel, text):
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, rel):
        with open(os.path.join(self.brain, rel), encoding="utf-8") as fh:
            return fh.read()

    def status(self, *paths):
        # rstrip only: the XY status codes are column-significant (" M" is an
        # unstaged modification, "M " a staged one).
        return self.git("status", "--porcelain", "--", *paths).stdout.rstrip("\n")

    def break_commit(self):
        """Make `git commit` fail for real, at the last step of commit_block —
        after the file is written and staged."""
        hook = os.path.join(self.brain, ".git", "hooks", "pre-commit")
        self.write_exec(hook, "#!/bin/sh\nexit 1\n")

    def write_exec(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o755)

    def dirty(self, rel):
        with open(os.path.join(self.brain, rel), "a", encoding="utf-8") as fh:
            fh.write(HAND_EDIT)

    def capture(self, block):
        return commit_block(self.brain, PROVIDER, block, "local")

    def assert_capture_failed(self, block, expect=RuntimeError):
        with self.assertRaises(expect) as ctx:
            self.capture(block)
        # Non-vacuity: `is_noop_commit` sits between the mutation and the
        # commit. If the fixture staged nothing, commit_block would return
        # "skipped: no change" and the except handler would never run at all.
        return str(ctx.exception)


class ScopedCleanupTests(_BrainRepo):
    """The capture mutates its own path, then fails at `git commit`."""

    def setUp(self):
        super().setUp()
        self.dirty("memories/unrelated.md")
        self.break_commit()

    def test_unrelated_dirty_file_survives_a_failed_capture(self):
        # THE regression. Without the scoped cleanup, `git checkout -- .`
        # discards this edit while quarantining someone else's block.
        self.assert_capture_failed(_block("target", body="captured body"))
        self.assertIn(HAND_EDIT, self.read("memories/unrelated.md"))
        self.assertEqual(self.status("memories/unrelated.md"), " M memories/unrelated.md")

    def test_the_captures_own_path_is_left_clean(self):
        self.assert_capture_failed(_block("target", body="captured body"))
        self.assertEqual(self.status("memories/target.md"), "")
        self.assertNotIn("captured body", self.read("memories/target.md"))

    def test_an_unrelated_staged_change_is_not_unstaged(self):
        # The pathless `git reset` in the old handler unstaged everything,
        # including work another writer had already staged.
        self.write("memories/staged.md", _item("staged"))
        self.git("add", "memories/staged.md")
        self.assert_capture_failed(_block("target", body="captured body"))
        self.assertEqual(self.status("memories/staged.md"), "A  memories/staged.md")

    def test_failed_create_leaves_no_untracked_leftover(self):
        # The old handler cleaned neither the index entry nor the file, so
        # `finally`'s `git checkout main` carried the orphan onto main.
        self.assert_capture_failed(_block("newborn", action="new"))
        self.assertFalse(os.path.exists(os.path.join(self.brain, "memories/newborn.md")))
        self.assertEqual(self.status("memories/newborn.md"), "")

    def test_failed_delete_restores_the_file(self):
        # The old handler left ` D memories/target.md` unstaged, which halts
        # the nightly merge until a human intervenes.
        self.assert_capture_failed(_block("target", action="delete"))
        self.assertTrue(os.path.exists(os.path.join(self.brain, "memories/target.md")))
        self.assertEqual(self.status("memories/target.md"), "")

    def test_nothing_is_committed_to_the_provider_branch(self):
        before = self.git("rev-parse", BRANCH).stdout.strip()
        self.assert_capture_failed(_block("target", body="captured body"))
        self.assertEqual(self.git("rev-parse", BRANCH).stdout.strip(), before)

    def test_the_tree_is_left_on_the_main_branch(self):
        # Asserted here, not in UnmutatedCaptureTests: only this class gets
        # far enough to actually switch to the provider branch, so only here
        # does the restore-to-main in `finally` have anything to undo.
        self.assert_capture_failed(_block("target", body="captured body"))
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "main")


class UntrackedDeleteTargetTests(_BrainRepo):
    """A delete capture aimed at a path that exists on disk but is untracked —
    a session's hand-created file. `git rm` refuses it (pathspec-match) having
    mutated nothing, so the capture touched nothing and must repair nothing.

    The old `git checkout -- .` handler left untracked files alone, so a
    scoped cleanup that removed this one would be a NEW data-loss path opened
    by the data-loss fix."""

    def setUp(self):
        super().setUp()
        self.write("memories/handmade.md", _item("handmade", "written by a session"))

    def test_the_rm_fails_without_mutating(self):
        msg = self.assert_capture_failed(_block("handmade", action="delete"))
        self.assertIn("did not match any files", msg)

    def test_the_untracked_file_is_not_deleted(self):
        self.assert_capture_failed(_block("handmade", action="delete"))
        self.assertTrue(os.path.exists(os.path.join(self.brain, "memories/handmade.md")))
        self.assertEqual(self.status("memories/handmade.md"), "?? memories/handmade.md")


class UnmutatedCaptureTests(_BrainRepo):
    """The self-reinforcing loop: the capture fails BEFORE mutating anything,
    because the tree was already dirty on the path it wanted."""

    def setUp(self):
        super().setUp()
        # Make the two branches diverge on target.md so a dirty worktree copy
        # genuinely blocks the branch switch.
        self.git("checkout", "-q", BRANCH)
        self.write("memories/target.md", _item("target", "provider-branch body"))
        self.git("commit", "-qam", "provider edit")
        self.git("checkout", "-q", "main")
        self.dirty("memories/target.md")

    def test_dirty_capture_path_survives_when_the_branch_checkout_fails(self):
        msg = self.assert_capture_failed(_block("target", body="captured body"))
        # Also the fixture's non-vacuity guard: the failure must be the branch
        # switch refusing to clobber the dirty file, not some other error and
        # not a `-f` switch that clobbered it and failed later.
        self.assertIn("checkout", msg)
        self.assertIn("would be overwritten", msg)
        # The capture wrote nothing, so the correct repair is none. Restoring
        # `block["file"]` here would destroy the hand-edit that caused the
        # failure — scoping alone does not prevent that.
        self.assertIn(HAND_EDIT, self.read("memories/target.md"))


class LoudCleanupFailureTests(_BrainRepo):
    """If the scoped cleanup cannot complete, say so — never widen it."""

    def setUp(self):
        super().setUp()
        self.dirty("memories/unrelated.md")
        self.break_commit()
        self.mem = os.path.join(self.brain, "memories")
        self.addCleanup(os.chmod, self.mem, 0o755)

    def _wedge_after_delete(self):
        """Let `git rm` succeed, then make restoring the file impossible."""
        real_git = inbox_ingest.git
        brain, mem = self.brain, self.mem

        def spy(brain_dir, *args, **kwargs):
            r = real_git(brain_dir, *args, **kwargs)
            if args[:2] == ("rm", "-f"):
                os.chmod(mem, 0o500)  # read+execute only: cannot recreate
            return r

        inbox_ingest.git = spy
        self.addCleanup(setattr, inbox_ingest, "git", real_git)
        self.assertTrue(os.path.isdir(brain))

    def test_unrestorable_path_raises_cleanup_error(self):
        self._wedge_after_delete()
        with self.assertRaises(CleanupError) as ctx:
            self.capture(_block("target", action="delete"))
        self.assertIn("memories/target.md", str(ctx.exception))
        self.assertIn("Refusing to fall back to wiping", str(ctx.exception))

    def test_unrestorable_path_still_does_not_wipe_the_tree(self):
        self._wedge_after_delete()
        with self.assertRaises(CleanupError):
            self.capture(_block("target", action="delete"))
        self.assertIn(HAND_EDIT, self.read("memories/unrelated.md"))

    def test_the_original_failure_is_not_lost(self):
        self._wedge_after_delete()
        with self.assertRaises(CleanupError) as ctx:
            self.capture(_block("target", action="delete"))
        self.assertIsNotNone(ctx.exception.__cause__)
        self.assertIn("commit", str(ctx.exception.__cause__))


class TouchedIsSetBeforeTheOpenTests(_BrainRepo):
    """`touched = True` sits BEFORE the `with open(abs_path, "w")` — and it has
    to, because `open(..., "w")` truncates on entry. Every other line of the
    update branch is guarded by a test; this placement was named as load-bearing
    in the commit message, the inline comment and the CHANGELOG, and nothing
    pinned it. The `git rm` half of the same decision IS pinned
    (`test_the_untracked_file_is_not_deleted`); this half was not.

    Failure mode if `touched` moves after the block: the file is already
    truncated when the write raises, the handler runs no cleanup at all, and a
    committed memory is left at 0 bytes as an unstaged modification — which
    poisons the next capture and halts the nightly merge, and loses the
    committed content on top.

    The trigger used here is a non-`str` body, so `open()` succeeds and
    `fh.write()` raises. It stands in for EIO/ENOSPC, which cannot be provoked
    portably in a test.
    """

    def _block_with_unwritable_body(self):
        block = _block("target", body="captured body")
        block["body"] = 1234          # open() succeeds, fh.write() raises TypeError
        return block

    def test_a_write_that_fails_after_open_does_not_leave_a_truncated_file(self):
        """MUTATION: in `inbox_ingest.commit_block`, move `touched = True` from
        before the `with open(abs_path, "w", ...)` block to after it.
        Observed: 2 failed, 14 passed — this test and
        `test_a_write_that_fails_after_open_leaves_the_tree_clean`. (The third
        test in this class stays green under the mutation by construction: with
        `touched` false no cleanup runs at all, so an unrelated dirty file is
        trivially untouched. It guards the opposite over-correction.)
        """
        before = self.read("memories/target.md")
        with self.assertRaises(TypeError):
            self.capture(self._block_with_unwritable_body())
        self.assertNotEqual(os.path.getsize(
            os.path.join(self.brain, "memories/target.md")), 0)
        self.assertEqual(self.read("memories/target.md"), before)

    def test_a_write_that_fails_after_open_leaves_the_tree_clean(self):
        """Same mutation. The 0-byte file is left as an unstaged modification,
        which is exactly the dirty-tree condition the next capture refuses on
        and the nightly merge halts on."""
        with self.assertRaises(TypeError):
            self.capture(self._block_with_unwritable_body())
        self.assertEqual(self.status("memories/target.md"), "")

    def test_an_unrelated_dirty_file_still_survives_that_failure(self):
        """Non-vacuity for the two above: the cleanup they require must still be
        the SCOPED one, not a tree-wide `git checkout -- .` that would satisfy
        them by discarding someone else's uncommitted work."""
        self.dirty("memories/unrelated.md")
        with self.assertRaises(TypeError):
            self.capture(self._block_with_unwritable_body())
        self.assertIn(HAND_EDIT, self.read("memories/unrelated.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
