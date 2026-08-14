#!/usr/bin/env python3
"""Tests for sweep_extract.redact_secrets — the ingestion path's scrubber.

Written after a real miss. Every pattern in SECRET_PATTERNS is anchored to a known shape:
a vendor prefix (`sk-`, `ghp_`, `AKIA`) or one of four keywords before a colon. On
2026-08-13 a 43-character setup code pasted after "send me this on telegram:" matched none
of them, survived into three corpus files, and was then displayed in a session transcript.

A prefix list can only ever catch the credentials someone thought to enumerate. The added
entropy rule catches the shape instead — but a redactor that mangles ordinary text gets
switched off, so the false-positive cases below matter as much as the true-positive one
and are asserted just as hard.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))

import sweep_extract as sx  # noqa: E402

REDACTED = "[REDACTED]"


class MustRedact(unittest.TestCase):
    def test_the_credential_that_got_through(self):
        """The regression. Reddened by deleting the entropy rule from redact_secrets.

        Shape-alike of the real miss: no vendor prefix, and the preceding word is not one
        of the four the keyword pattern knows about.
        """
        text = "Can you send me this on telegram: 7Nchizr-t9D6YtETY22QF5Duf2cu1_fYSIEpdstFOF"
        self.assertIn(REDACTED, sx.redact_secrets(text))

    def test_known_vendor_prefixes_still_redact(self):
        """Reddened by dropping SECRET_PATTERNS — the entropy rule must be additive."""
        for text in ("my key is sk-abcdefghijklmnopqrstuvwxyz123456",
                     "token ghp_" + "a1B2" * 9,
                     "-----BEGIN RSA PRIVATE KEY-----"):
            self.assertIn(REDACTED, sx.redact_secrets(text), text[:30])


class MustNotRedact(unittest.TestCase):
    """False positives are how a scrubber gets switched off. These are the shapes that
    look long and random but are ordinary content."""

    def test_git_sha_survives(self):
        """A commit sha is not a secret, and redacting it corrupts real technical talk.
        Excluded because it has no uppercase. Reddened by dropping the mixed-case rule."""
        text = "fixed in commit 88c5aec3f1b2d4e5a6b7c8d9e0f1a2b3c4d5e6f7"
        self.assertNotIn(REDACTED, sx.redact_secrets(text))

    def test_uuid_survives(self):
        text = "project 019ce1f0-8259-7704-98c8-38f3952ae965 is the one"
        self.assertNotIn(REDACTED, sx.redact_secrets(text))

    def test_ordinary_prose_survives(self):
        text = ("I would really appreciate a plain-language summary of the situation "
                "before you give me the detailed breakdown of what went wrong")
        self.assertNotIn(REDACTED, sx.redact_secrets(text))

    def test_file_paths_survive(self):
        """Paths are the single most common long token in this corpus, and the user has a
        standing rule that full absolute paths must be given. Redacting them would break
        exactly the thing he asked for."""
        text = "see /home/x/projects/loreport-example-brain/memories/user-timezone.md"
        self.assertNotIn(REDACTED, sx.redact_secrets(text))

    def test_low_entropy_long_token_survives(self):
        """Long but repetitive. Reddened by dropping the entropy threshold."""
        self.assertNotIn(REDACTED, sx.redact_secrets("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaAAAA1111"))

    def test_a_long_kebab_identifier_survives(self):
        """Loreport's own memory names are long and hyphenated. Reddened by lowering the
        length floor far enough to catch them."""
        text = "see [[feedback-worktree-removal-loses-reflog-history]] for the details"
        self.assertNotIn(REDACTED, sx.redact_secrets(text))


class TheRuleItself(unittest.TestCase):
    def test_looks_like_secret_requires_all_three_classes(self):
        """Reddened by relaxing the mixed lower/upper/digit requirement to any two."""
        self.assertFalse(sx.looks_like_secret("abcdefghijklmnopqrstuvwxyzabcdef"))
        self.assertFalse(sx.looks_like_secret("ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEF"))
        self.assertFalse(sx.looks_like_secret("0123456789012345678901234567890123"))
        # Genuinely high-entropy: mixed classes AND a wide alphabet. An earlier version
        # of this assertion repeated a 7-character block, which has only 7 distinct
        # symbols and correctly failed the entropy bar — the fixture was wrong, not the
        # rule. Both properties have to hold for a token to look machine-generated.
        self.assertTrue(sx.looks_like_secret("aB3dE7fG9hJ2kL4mN6pQ8rS0tU5vW1xZ"))

    def test_short_tokens_are_never_secrets(self):
        """Reddened by lowering the length floor."""
        self.assertFalse(sx.looks_like_secret("aB3xY7z"))


if __name__ == "__main__":
    unittest.main()
