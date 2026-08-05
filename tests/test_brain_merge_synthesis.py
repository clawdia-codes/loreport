#!/usr/bin/env python3
"""Report-only guarantees for the synthesis detector as wired into brain_merge.

The pure detector has its own report-only test; this one covers the wiring, which
is where a write path would actually get introduced.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import brain_merge  # noqa: E402

FIXTURE_BRAIN = os.path.join(ROOT, "tests", "fixtures", "synth", "brain")


class SynthesisWiringTests(unittest.TestCase):
    def setUp(self):
        self.brain = tempfile.mkdtemp(prefix="synth-wiring-")
        shutil.copytree(
            os.path.join(FIXTURE_BRAIN, "memories"), os.path.join(self.brain, "memories")
        )
        os.makedirs(os.path.join(self.brain, "hub"))
        os.makedirs(os.path.join(self.brain, "knowledge"))

    def tearDown(self):
        shutil.rmtree(self.brain, ignore_errors=True)

    def _tree(self):
        found = []
        for root, _dirs, files in os.walk(self.brain):
            for name in files:
                found.append(os.path.relpath(os.path.join(root, name), self.brain))
        return sorted(found)

    def test_no_knowledge_page_is_ever_created(self):
        before = self._tree()
        brain_merge.run_synthesis_report(self.brain, dry_run=False)
        after = self._tree()
        self.assertEqual(os.listdir(os.path.join(self.brain, "knowledge")), [])
        # The only new file is the report artifact itself.
        self.assertEqual(set(after) - set(before), {brain_merge.SYNTHESIS_REPORT_FILE})

    def test_dry_run_writes_nothing_at_all(self):
        before = self._tree()
        result = brain_merge.run_synthesis_report(self.brain, dry_run=True)
        self.assertEqual(self._tree(), before)
        self.assertIn("clusters", result)

    def test_report_is_readable_json_for_the_health_check(self):
        brain_merge.run_synthesis_report(self.brain, dry_run=False)
        with open(os.path.join(self.brain, brain_merge.SYNTHESIS_REPORT_FILE), encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertIn("warnings", payload)
        self.assertIn("generated", payload)

    def test_detector_failure_cannot_abort_the_merge(self):
        original = brain_merge.synth_detect.detect_clusters
        brain_merge.synth_detect.detect_clusters = lambda *a, **k: 1 / 0
        try:
            result = brain_merge.run_synthesis_report(self.brain, dry_run=False)
        finally:
            brain_merge.synth_detect.detect_clusters = original
        self.assertIn("error", result)
        self.assertIn("ZeroDivisionError", result["error"])

    def test_digest_lines_always_say_report_only(self):
        result = brain_merge.run_synthesis_report(self.brain, dry_run=True)
        lines = brain_merge.format_synthesis_lines(result)
        self.assertIn("REPORT-ONLY, none filed", lines[0])
        for bad in ({}, None, {"error": "boom", "clusters": [], "warnings": []}):
            self.assertIn("REPORT-ONLY, none filed", brain_merge.format_synthesis_lines(bad)[0])


if __name__ == "__main__":
    unittest.main()
