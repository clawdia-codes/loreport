#!/usr/bin/env python3
"""Tests for hub/domain_backfill.py.

The tests that matter are the refusals. `domain` is a judgement about a person,
so the tool's value is entirely in what it declines to do on its own: it must
skip its own unsure proposals rather than defaulting them, refuse a review that
has gone stale, refuse a value outside the enum, and leave every other byte of
an item alone — including `visibility`, which is a different axis and must never
be inferred from this one.
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hub"))

from domain_backfill import (  # noqa: E402
    cmd_apply,
    cmd_propose,
    file_digest,
    parse_frontmatter,
    set_domain,
)

ITEM = """---
name: {name}
description: {desc}
type: project
visibility: {vis}
---

{body}
"""


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loreport-domain-")
        self.addCleanup(_rmtree, self.tmp)
        os.makedirs(os.path.join(self.tmp, "memories"))

    def write(self, name, desc="a hook", vis="shared", body="Some body text."):
        path = os.path.join(self.tmp, "memories", f"{name}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ITEM.format(name=name, desc=desc, vis=vis, body=body))
        return path

    def read(self, name):
        with open(os.path.join(self.tmp, "memories", f"{name}.md"), encoding="utf-8") as fh:
            return fh.read()

    def proposal_path(self):
        return os.path.join(self.tmp, "proposal.tsv")

    def write_proposal(self, lines):
        with open(self.proposal_path(), "w", encoding="utf-8") as fh:
            fh.write("# comment line\n\n" + "\n".join(lines) + "\n")


class ProposeTests(Harness):
    def test_proposal_is_read_only(self):
        path = self.write("untouched-item")
        before = self.read("untouched-item")
        cmd_propose(self.tmp, self.proposal_path())
        self.assertEqual(self.read("untouched-item"), before)
        self.assertTrue(os.path.isfile(path))

    def test_unsure_items_are_marked_not_guessed(self):
        self.write("neutral-item", desc="a hook", body="Nothing indicative here.")
        cmd_propose(self.tmp, self.proposal_path())
        with open(self.proposal_path(), encoding="utf-8") as fh:
            row = [l for l in fh if l.startswith("neutral-item")][0]
        self.assertEqual(row.split("\t")[1], "?")

    def test_already_classified_item_is_not_re_proposed(self):
        path = self.write("decided-item")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(set_domain(text, "work"))
        cmd_propose(self.tmp, self.proposal_path())
        with open(self.proposal_path(), encoding="utf-8") as fh:
            self.assertNotIn("decided-item", fh.read())


class ApplyTests(Harness):
    def test_applies_a_reviewed_value(self):
        self.write("reviewed-item")
        digest = file_digest(self.read("reviewed-item"))
        self.write_proposal([f"reviewed-item\twork\t{digest}\tbecause"])
        self.assertEqual(cmd_apply(self.tmp, self.proposal_path(), dry_run=False), 0)
        fm, _ = parse_frontmatter(self.read("reviewed-item"))
        self.assertEqual(fm["domain"], "work")

    def test_unreviewed_question_mark_is_skipped_not_defaulted(self):
        self.write("unsure-item")
        digest = file_digest(self.read("unsure-item"))
        self.write_proposal([f"unsure-item\t?\t{digest}\tno signal"])
        cmd_apply(self.tmp, self.proposal_path(), dry_run=False)
        fm, _ = parse_frontmatter(self.read("unsure-item"))
        self.assertNotIn("domain", fm)

    def test_stale_review_is_refused(self):
        self.write("moving-item")
        digest = file_digest(self.read("moving-item"))
        self.write("moving-item", body="Rewritten since the proposal was made.")
        self.write_proposal([f"moving-item\twork\t{digest}\tbecause"])
        self.assertEqual(cmd_apply(self.tmp, self.proposal_path(), dry_run=False), 1)
        fm, _ = parse_frontmatter(self.read("moving-item"))
        self.assertNotIn("domain", fm)

    def test_value_outside_the_enum_is_refused(self):
        self.write("bad-value-item")
        digest = file_digest(self.read("bad-value-item"))
        self.write_proposal([f"bad-value-item\toffice\t{digest}\tbecause"])
        self.assertEqual(cmd_apply(self.tmp, self.proposal_path(), dry_run=False), 1)
        fm, _ = parse_frontmatter(self.read("bad-value-item"))
        self.assertNotIn("domain", fm)

    def test_unknown_item_name_is_refused(self):
        self.write("real-item")
        self.write_proposal(["ghost-item\twork\tdeadbeef1234\tbecause"])
        self.assertEqual(cmd_apply(self.tmp, self.proposal_path(), dry_run=False), 1)

    def test_dry_run_writes_nothing(self):
        self.write("dry-item")
        digest = file_digest(self.read("dry-item"))
        before = self.read("dry-item")
        self.write_proposal([f"dry-item\tboth\t{digest}\tbecause"])
        cmd_apply(self.tmp, self.proposal_path(), dry_run=True)
        self.assertEqual(self.read("dry-item"), before)

    def test_visibility_and_body_are_byte_preserved(self):
        # The two axes are independent: writing `domain` must never disturb
        # `visibility`, and must not reflow anything else in the file either.
        self.write("careful-item", vis="local", body="Body   with  odd   spacing.\n\nAnd a blank line.")
        before = self.read("careful-item")
        digest = file_digest(before)
        self.write_proposal([f"careful-item\tpersonal\t{digest}\tbecause"])
        cmd_apply(self.tmp, self.proposal_path(), dry_run=False)
        after = self.read("careful-item")
        self.assertIn("visibility: local", after)
        self.assertEqual(before.split("---", 2)[2], after.split("---", 2)[2])
        self.assertEqual(
            [l for l in before.splitlines() if not l.startswith("domain:")],
            [l for l in after.splitlines() if not l.startswith("domain:")],
        )

    def test_set_domain_replaces_rather_than_duplicates(self):
        text = ITEM.format(name="x", desc="d", vis="shared", body="b")
        once = set_domain(text, "work")
        twice = set_domain(once, "personal")
        self.assertEqual(twice.count("domain:"), 1)
        self.assertIn("domain: personal", twice)


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
