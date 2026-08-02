#!/usr/bin/env python3
"""Unit tests for hub/project.py block writer and budget truncation."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from project import (
    BEGIN_MARKER,
    END_MARKER,
    _truncate_index_lines,
    atomic_write,
    extract_block_region,
    region_hash,
    replace_block,
    write_target,
)


class BlockWriterTests(unittest.TestCase):
    def test_absent_file_block_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "new.md")
            generated = "generated body\n"
            final = write_target(path, generated, "block", dry_run=False)
            self.assertIn(BEGIN_MARKER, final)
            self.assertIn(END_MARKER, final)
            self.assertIn("generated body", final)
            with open(path, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), final)

    def test_existing_file_with_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "existing.md")
            original = (
                "# user preamble\n\n"
                f"{BEGIN_MARKER}\nold loreport\n{END_MARKER}\n"
                "# user footer\n"
            )
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)
            write_target(path, "fresh loreport\n", "block", dry_run=False)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("# user preamble"))
            self.assertIn("fresh loreport", text)
            self.assertTrue(text.endswith("# user footer\n"))

    def test_existing_file_without_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plain.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("handwritten\n")
            write_target(path, "injected\n", "block", dry_run=False)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("handwritten", text)
            self.assertIn(BEGIN_MARKER, text)
            self.assertIn("injected", text)

    def test_atomic_write_refuses_symlink_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real.md")
            link = os.path.join(tmp, "link.md")
            os.symlink(real, link)
            with self.assertRaises(OSError):
                atomic_write(link, "x\n")

    def test_region_hash_stable(self):
        text = f"{BEGIN_MARKER}\nabc\n{END_MARKER}\n"
        self.assertEqual(
            region_hash(text, "block"),
            region_hash(text, "block"),
        )
        self.assertNotEqual(
            region_hash(text, "block"),
            region_hash(f"{BEGIN_MARKER}\ndef\n{END_MARKER}\n", "block"),
        )


class TruncationTests(unittest.TestCase):
    def test_drops_reference_before_project(self):
        index = (
            "- [[a]] — one  (reference)\n"
            "- [[b]] — two  (project)\n"
        )
        out, dropped = _truncate_index_lines(index, 30)
        self.assertEqual(dropped, 1)
        self.assertNotIn("reference", out)
        self.assertIn("project", out)


class ReplaceBlockTests(unittest.TestCase):
    def test_idempotent_replace(self):
        first = replace_block("", "one\n")
        second = replace_block(first, "two\n")
        self.assertEqual(extract_block_region(second).strip(), "two")
        self.assertEqual(second.count(BEGIN_MARKER), 1)


if __name__ == "__main__":
    unittest.main()
