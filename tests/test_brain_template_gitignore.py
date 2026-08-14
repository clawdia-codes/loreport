#!/usr/bin/env python3
"""Tests for brain-template/.gitignore — what a NEW brain inherits at init.

Why this file exists: the engine's own .gitignore does not reach a brain. A brain
gets its ignore rules exactly once, by `cp -r`, at `scripts/init-brain.sh` time. So
every runtime path the hub learns to write has to be added here by hand, and three
separate times it wasn't — leaving the working tree permanently dirty, which breaks
the "status must be empty before a capture" rule and halts loreport-sync's
post-merge clean-tree guard. This test is the mechanism that stops the fourth.

It asserts the real operation rather than the file's text: it builds a throwaway
repo with the template's .gitignore, creates each path, runs the same `git add -A`
that init-brain.sh runs, and looks at what actually got staged. A rule that is
present but does not match (`hub/digest-*.md` against a real filename, say) fails
here and would pass a grep.

Both directions are asserted. IGNORED alone would be satisfied by `*`; TRACKED
catches a rule that is too broad — which is the more dangerous mistake, because it
silently drops real memories out of the store.

The two lists are CURATED, not derived. Nothing in the repo can tell this test what
the hub will write next; adding a runtime path without adding it here is a gap this
test cannot see. That limitation is the reason the comments in .gitignore explain
*why* each entry exists.
"""

import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_GITIGNORE = os.path.join(ROOT, "brain-template", ".gitignore")

# Runtime state, derived artifacts and raw exports. None of these are brain content;
# every one of them appears in a working tree outside any commit.
IGNORED = [
    "surface.md",
    "exports/conversations.json",
    ".obsidian/workspace.json",
    ".obsidian/app.json",
    "hub/projection-manifest.json",
    "hub/surface-chatgpt.md",
    "hub/surface-claude-ai.md",
    "hub/health-state.json",
    "hub/synthesis-report.json",
    "hub/merge-state.json",
    "hub/digest-2026-08-11.md",
    "hub/attention.json",
    "hub/attention.json.tmp",
    "hub/nightly/2026-08-11.json",
    "hub/quarantine/parked-capture.md",
    "hub/logs/merge.log",
    "hub/.loreport.lock",
    "hub/published/packet-2026-08-11.md",
]

# Brain content and the one piece of hub state that MUST survive a re-clone:
# proposals/ledger.json carries the first_seen clock the overdue check measures
# from, so ignoring it would silently reset every proposal's age to zero.
TRACKED = [
    "INDEX.md",
    "PROFILE.md",
    "memories/user-example.md",
    "knowledge/example.md",
    "skills/example/SKILL.md",
    "prompts/bootstrap.md",
    "hub/proposals/ledger.json",
    "hub/published/packet.md",
]


def stage_all(gitignore_text, paths):
    """Build a throwaway repo, create `paths`, `git add -A`, return what got staged."""
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, ".gitignore"), "w") as fh:
            fh.write(gitignore_text)
        for rel in paths:
            full = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write("x")
        run = lambda *a: subprocess.run(
            ["git", "-C", tmp] + list(a), check=True, capture_output=True, text=True
        )
        run("init", "-q", "-b", "main")
        run("add", "-A")
        listed = run("ls-files").stdout.split("\n")
        return {p for p in listed if p}


class BrainTemplateGitignore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(TEMPLATE_GITIGNORE) as fh:
            cls.text = fh.read()
        cls.staged = stage_all(cls.text, IGNORED + TRACKED)

    def test_runtime_paths_are_not_staged(self):
        """Reddens if any IGNORED entry is dropped from brain-template/.gitignore."""
        leaked = sorted(p for p in IGNORED if p in self.staged)
        self.assertEqual(
            [],
            leaked,
            "these runtime paths would be committed into a new brain, leaving its "
            "tree dirty and halting loreport-sync's post-merge guard: %s" % leaked,
        )

    def test_brain_content_is_still_staged(self):
        """Reddens if a rule is broadened until it swallows real memories.

        Mutating `hub/nightly/` to `hub/` reddens this and NOT the test above.
        """
        dropped = sorted(p for p in TRACKED if p not in self.staged)
        self.assertEqual(
            [],
            dropped,
            "an ignore rule is too broad — a new brain would silently fail to "
            "track this content: %s" % dropped,
        )

    def test_the_fixture_can_actually_fail(self):
        """Guards against a vacuous pass: proves staging observes .gitignore at all.

        Without this, a bug that made `stage_all` return every path (or none) would
        leave both tests above passing for the wrong reason. A safety assertion that
        never sees a negative case has shipped in this repo twice.
        """
        with_no_rules = stage_all("", ["hub/merge-state.json"])
        self.assertIn("hub/merge-state.json", with_no_rules)
        with_the_rule = stage_all("hub/merge-state.json\n", ["hub/merge-state.json"])
        self.assertNotIn("hub/merge-state.json", with_the_rule)


if __name__ == "__main__":
    unittest.main()
