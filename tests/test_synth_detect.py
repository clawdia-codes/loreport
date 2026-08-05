#!/usr/bin/env python3
"""Tests for hub/synth_detect.py."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

from synth_detect import detect_clusters, is_degenerate_topic


BRAIN = os.path.join(ROOT, "tests", "fixtures", "synth", "brain")


class SynthDetectTests(unittest.TestCase):
    def test_missing_link_cluster(self):
        report = detect_clusters(BRAIN)
        missing = [
            c for c in report["clusters"] if c["signal"] == "common-missing-link"
        ]
        self.assertTrue(missing)
        top = missing[0]
        self.assertEqual(top["topic"], "missing-topic")
        self.assertGreaterEqual(len(top["members"]), 3)

    def test_mutual_link_cluster(self):
        report = detect_clusters(BRAIN)
        mutual = [c for c in report["clusters"] if c["signal"] == "mutual-link"]
        self.assertTrue(mutual)
        names = set(mutual[0]["members"])
        self.assertGreaterEqual(len(names), 3)
        self.assertEqual(names, {"alpha-link", "beta-link", "delta-link"})

    def test_one_way_linker_not_pulled_into_cluster(self):
        # design §2 says "mutually link" — epsilon links alpha and beta, neither
        # links back. Counting one-way edges collapses the brain into one blob.
        report = detect_clusters(BRAIN)
        for cluster in report["clusters"]:
            if cluster["signal"] == "mutual-link":
                self.assertNotIn("epsilon-link", cluster["members"])

    def test_oversized_component_is_a_warning_not_a_proposal(self):
        from synth_detect import MAX_CLUSTER_MEMBERS

        report = detect_clusters(BRAIN)
        for cluster in report["clusters"]:
            self.assertLessEqual(len(cluster["members"]), MAX_CLUSTER_MEMBERS)

    def test_degenerate_topic_warning(self):
        self.assertTrue(is_degenerate_topic("the"))
        report = detect_clusters(BRAIN)
        self.assertTrue(report["warnings"])

    def test_report_only_no_files_written(self):
        before = os.listdir(os.path.join(BRAIN, "memories"))
        detect_clusters(BRAIN)
        after = os.listdir(os.path.join(BRAIN, "memories"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
