#!/usr/bin/env python3
"""Unit tests for hub/project.py block writer and budget truncation."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from project import (
    BEGIN_MARKER,
    END_MARKER,
    _truncate_index_lines,
    atomic_write,
    build_surface_body,
    extract_block_region,
    project_one,
    region_hash,
    replace_block,
    write_target,
)
import brain_merge


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


class BrainFixture:
    """A minimal real brain on disk: memory files, an INDEX built by the
    producer that actually builds it, a PROFILE, and a git repo so the CLI's
    `git rev-parse main` resolves."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loreport-project-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.brain = os.path.join(self.tmp, "brain")
        os.makedirs(os.path.join(self.brain, "memories"))
        self.write("PROFILE.md", "# Profile\n\nshort profile\n")

    def write(self, rel, text):
        path = os.path.join(self.brain, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def save_memory(self, name, visibility, typ="reference", desc=None):
        """Write a memory the way a capture would, then rebuild INDEX.md with
        hub/brain_merge.build_index_bytes — the only producer of index lines.
        Hand-writing the index line here would make the projection test verify
        the fixture instead of the pipeline."""
        self.write(
            f"memories/{name}.md",
            f"---\nname: {name}\ndescription: {desc or ('desc for ' + name)}\n"
            f"type: {typ}\nvisibility: {visibility}\n---\nbody of {name}\n",
        )
        self.rebuild_index()

    def rebuild_index(self):
        content, _m, _k, _s = brain_merge.build_index_bytes(self.brain)
        self.write("INDEX.md", content.decode("utf-8"))

    def git_init(self):
        run = lambda *a: subprocess.run(["git", "-C", self.brain, *a],
                                        capture_output=True, text=True, check=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "test@example.invalid")
        run("config", "user.name", "test")
        run("add", "-A")
        run("commit", "-qm", "seed")

    def target(self, **overrides):
        t = {"provider": "chatgpt", "path": "hub/surface-chatgpt.md",
             "mode": "block", "budget_chars": 4000, "protocol": "pointer"}
        t.update(overrides)
        return t


class BudgetAccountingTests(BrainFixture, unittest.TestCase):
    """`_truncate_index_lines` exists to bring the surface inside budget_chars.
    It budgeted against the item lines only, while the emitted index also
    carries `# Index`, `## Memories`, `## Knowledge`, `## Skills` and the blank
    lines between them — so it could report a completed truncation and still
    hand back an over-budget body."""

    def test_truncation_brings_the_whole_body_within_budget(self):
        for i in range(12):
            self.save_memory(f"item-{i:02d}", "shared",
                             desc="a description that is reasonably long here")
        budget = 900
        body, filtered, truncated = build_surface_body(
            self.brain, self.target(budget_chars=budget), "abc1234")
        self.assertGreater(truncated, 0,
                           "fixture did not force truncation; the assertion below is vacuous")
        self.assertEqual(filtered, 0)
        self.assertLessEqual(
            len(body), budget,
            f"truncation cut {truncated} line(s) and still returned "
            f"{len(body)} chars against a {budget}-char budget",
        )

    def test_index_structure_alone_can_exhaust_the_budget(self):
        """The pathological end of the same accounting: when the headings and
        the fixed prefix already exceed the budget, every item line is cut —
        and that is reported, so health can fail on it, rather than the surface
        quietly shipping items it never budgeted for."""
        self.save_memory("only-one", "shared")
        body, _filtered, truncated = build_surface_body(
            self.brain, self.target(budget_chars=1), "abc1234")
        self.assertEqual(truncated, 1)
        self.assertNotIn("[[only-one]]", body)


class DropAccountingTests(BrainFixture, unittest.TestCase):
    """G8: `dropped=68` on a cloud surface was 68 local items correctly
    withheld. The same word also counted index lines cut for budget, which is
    data loss. The two must never be summed into one reported number."""

    def test_manifest_reports_filtering_and_truncation_separately(self):
        for i in range(6):
            self.save_memory(f"local-{i}", "local",
                             desc="a local item with a reasonably long description")
        for i in range(6):
            self.save_memory(f"shared-{i}", "shared",
                             desc="a shared item with a reasonably long description")
        entry = project_one(
            self.brain, self.target(budget_chars=800, path="surface.md"),
            "0" * 40, "abc1234", dry_run=True)
        self.assertEqual(entry["dropped_visibility"], 6,
                         "the six local items are the visibility filter's work")
        self.assertGreater(entry["dropped_budget"], 0,
                           "fixture did not force truncation")
        self.assertNotEqual(
            entry["dropped_visibility"], entry["dropped_budget"],
            "the two counters moved together; they no longer distinguish the causes")
        self.assertEqual(entry["dropped"],
                         entry["dropped_visibility"] + entry["dropped_budget"])

    def test_filtering_alone_reports_zero_truncation(self):
        """The healthy nightly shape: local items withheld, nothing cut. If
        truncation is ever computed from the combined figure, this goes red and
        `loreport-health` starts failing every night on a working filter."""
        for i in range(6):
            self.save_memory(f"local-{i}", "local")
        self.save_memory("shared-0", "shared")
        entry = project_one(
            self.brain, self.target(budget_chars=100000, path="surface.md"),
            "0" * 40, "abc1234", dry_run=True)
        self.assertEqual(entry["dropped_visibility"], 6)
        self.assertEqual(entry["dropped_budget"], 0)

    def test_stdout_names_the_two_causes_with_two_different_words(self):
        """The sync journal is where a human reads this. One word for both
        conditions is the defect; `dropped=` must not come back."""
        self.save_memory("local-0", "local")
        self.save_memory("shared-0", "shared")
        self.write("hub/projection-targets.json", json.dumps(
            {"targets": [self.target(path="hub/surface-chatgpt.md")]}))
        self.git_init()
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, "project.py"),
             "--brain-dir", self.brain, "--dry-run"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("filtered=1", result.stdout)
        self.assertIn("truncated=0", result.stdout)
        self.assertNotIn("dropped=", result.stdout)


class SavedMemoryReachesTheSurfaceTests(BrainFixture, unittest.TestCase):
    """save → project → grep. The point of the whole pipeline is that a memory
    saved today is in the block a session reads tomorrow; nothing asserted that
    end to end."""

    def test_a_newly_saved_shared_memory_lands_inside_the_projected_block(self):
        self.save_memory("older-item", "shared")
        self.write("hub/projection-targets.json", json.dumps(
            {"targets": [self.target(path="hub/surface-chatgpt.md")]}))
        self.git_init()

        self.save_memory("brand-new-item", "shared",
                         desc="the fact saved a moment ago")
        subprocess.run(
            [sys.executable, os.path.join(HERE, "project.py"),
             "--brain-dir", self.brain],
            capture_output=True, text=True, check=True)

        with open(os.path.join(self.brain, "hub", "surface-chatgpt.md"),
                  encoding="utf-8") as fh:
            surface = fh.read()
        region = extract_block_region(surface)
        # The exact bytes brain_merge emits, not a hand-written approximation:
        # every consumer downstream copies the line verbatim, so the fixture
        # must not be allowed to drift from the producer.
        expected = "- [[brand-new-item]] — the fact saved a moment ago  (reference)"
        self.assertIn(expected, brain_merge.build_index_bytes(self.brain)[0].decode("utf-8"),
                      "the expected line is not what brain_merge produces; fixture drifted")
        self.assertIn(expected, region,
                      "a shared memory saved before projection never reached the surface")

    def test_a_local_memory_does_not_land_on_a_cloud_surface(self):
        self.save_memory("private-thing", "local")
        self.save_memory("public-thing", "shared")
        body, filtered, truncated = build_surface_body(
            self.brain, self.target(), "abc1234")
        self.assertIn("[[public-thing]]", body)
        self.assertNotIn("[[private-thing]]", body)
        self.assertEqual((filtered, truncated), (1, 0))


class ReplaceBlockTests(unittest.TestCase):
    def test_idempotent_replace(self):
        first = replace_block("", "one\n")
        second = replace_block(first, "two\n")
        self.assertEqual(extract_block_region(second).strip(), "two")
        self.assertEqual(second.count(BEGIN_MARKER), 1)


if __name__ == "__main__":
    unittest.main()
