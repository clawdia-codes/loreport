"""No tracked file in this PUBLIC repo may carry a personal identifier.

Loreport's whole contract is a boundary: the engine is shareable and holds nothing
personal; a brain instance is private and holds nothing generic. That boundary is easy
to state and easy to erode one convenient default at a time — a hardcoded fallback path
here, an example naming its author there. Each is individually harmless and collectively
a leak, and none of them announce themselves in review.

This existed before as a single assertion in test_sweep_run.py, guarding one module's
DEFAULT_STATE_FILE. That caught exactly the one place someone thought to check. By the
time it was generalized, seven other tracked files carried the author's name or home
directory — including hub/project.py's build_header(), which stamped a private brain's
path into the header of every generated surface, and therefore into text pasted into
third-party assistants.

The rule is mechanical, so let a machine hold it: scan every tracked text file, and fail
on any hit. Instance-specific values belong in that instance's config — see
bin/loreport-sync and loreport.conf — never in a default here.
"""

import os
import re
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Substrings that identify a specific human or machine rather than the software.
# Matched case-insensitively against tracked text files.
FORBIDDEN = (
    "/home/nvidia",     # this instance's home directory
    "oyvind",
    "theie",
    "øyvind",
)

# Deliberately precise rather than broad. An earlier draft forbade "/home/" outright and
# flagged a test fixture using the synthetic path /home/x — a false positive, and false
# positives are how a guard gets switched off. Another machine adopting this engine should
# add its own home directory here.

# THE LIST ABOVE COVERS THE INSTANCE OWNER. IT DOES NOT COVER THE PEOPLE AROUND THEM.
#
# On 2026-08-13 a calibration fixture was committed to this public repo carrying a family
# member's name and date of birth. Every test passed, because the owner's own name and
# home directory were nowhere in it. The guard was not wrong — it was narrow in a way
# nothing announced, which is the same failure mode its own docstring describes.
#
# Those names cannot simply be appended to FORBIDDEN: this file is public, so listing a
# partner's or child's name here publishes exactly what it is meant to protect. Instance-
# specific terms therefore load from outside the repo, and the file that holds them is
# gitignored. Set one up with:
#
#     printf 'name-one\nname-two\n' > .forbidden-extra
#
# or export LOREPORT_FORBIDDEN_EXTRA="name-one,name-two". Absent both, the guard behaves
# exactly as before — the extras are additive and never relax anything.
def instance_forbidden():
    terms = []
    env = os.environ.get("LOREPORT_FORBIDDEN_EXTRA", "")
    terms += [t.strip() for t in env.split(",") if t.strip()]
    path = os.path.join(REPO_ROOT, ".forbidden-extra")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            terms += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    return tuple(terms)

# This file necessarily names the strings it forbids. Nothing else is exempt:
# an exemption is how the previous single-assertion version stayed narrow.
EXEMPT_PATHS = {
    os.path.join("tests", "test_no_personal_identifiers.py"),
}

# Scratch directories from local tooling runs; not part of the shipped engine.
EXEMPT_PREFIXES = (".maestro/", ".sprint/")

SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".pyc")


def tracked_files():
    """Tracked files PLUS untracked-but-not-ignored ones.

    Scanning only `ls-files` output means a brand-new file is invisible to this guard
    until it is committed — so the sequence is: write the file, run the suite, watch it
    pass, commit, push, and only then does the check fire on the next run. That is exactly
    what happened on 2026-08-14: a test fixture carrying the owner's name and home
    directory went green, was pushed, and failed afterwards.

    A guard that reports a leak only once it is published is the wrong way round.
    `--others --exclude-standard` adds untracked files while still honouring .gitignore,
    so scratch output and the instance's own .forbidden-extra stay out of scope.
    """
    out = subprocess.run(
        ["git", "-C", REPO_ROOT, "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"],
        capture_output=True, text=True, check=True,
    ).stdout
    return sorted({p for p in out.split("\0") if p})


class TestNoPersonalIdentifiers(unittest.TestCase):
    def test_tracked_files_carry_no_personal_identifier(self):
        terms = FORBIDDEN + instance_forbidden()
        pattern = re.compile("|".join(re.escape(s) for s in terms), re.IGNORECASE)
        offenders = []

        for rel in tracked_files():
            if rel in EXEMPT_PATHS or rel.startswith(EXEMPT_PREFIXES):
                continue
            if rel.endswith(SKIP_SUFFIXES):
                continue
            path = os.path.join(REPO_ROOT, rel)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable: nothing to leak in text form
            for n, line in enumerate(lines, 1):
                if pattern.search(line):
                    offenders.append(f"{rel}:{n}: {line.strip()[:120]}")

        self.assertEqual(
            offenders, [],
            "Personal identifiers found in tracked files of a PUBLIC repo.\n"
            "Move the value into instance config (loreport.conf) or an environment\n"
            "variable and use a generic default:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
