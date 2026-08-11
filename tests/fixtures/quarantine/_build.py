#!/usr/bin/env python3
"""Regenerate the redacted replay corpus in this directory.

Run with a directory of REAL quarantine artifacts to check the reproductions
still match the originals structurally:

    python3 tests/fixtures/quarantine/_build.py --verify /path/to/originals

It compares, per file: byte size for empty files, and for the rest the line
"skeleton" — the `<MEMORY …>` tag verbatim, each frontmatter key name in order,
the position of every `---` line, whether an `INDEX:` line is present, and
whether the file ends with a newline. It deliberately does NOT compare prose:
the reproductions are structure-exact and content-free on purpose (this repo is
public), so a content diff would always fire.
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s+\S")
FM_KEYS = {"name", "description", "type", "visibility", "source", "captured",
           "domain", "lifespan", "expires"}


def skeleton(text):
    if text == "":
        return ["<empty>"]
    lines = text.split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("<MEMORY") or s == "</MEMORY>":
            # Keep which attributes the tag carries, in order, and the folder
            # each path points at — but not the item name, which is redacted by
            # design in the reproductions.
            out.append(re.sub(r'(file=")([a-z]+)/[^"]+(")', r"\1\2/<name>.md\3", s))
        elif s == "---":
            out.append("---")
        elif s.startswith("INDEX:"):
            out.append("INDEX:")
        else:
            m = KEY_RE.match(line)
            if m and m.group(1) in FM_KEYS:
                out.append(f"{m.group(1)}:")
    out.append("EOF-newline" if text.endswith("\n") else "EOF-no-newline")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", metavar="DIR", required=True,
                    help="directory holding the real originals")
    args = ap.parse_args()

    bad = 0
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".txt"):
            continue
        orig = os.path.join(args.verify, name)
        if not os.path.isfile(orig):
            print(f"MISSING original: {name}")
            bad += 1
            continue
        with open(os.path.join(HERE, name), encoding="utf-8") as fh:
            mine = skeleton(fh.read())
        with open(orig, encoding="utf-8") as fh:
            theirs = skeleton(fh.read())
        if mine == theirs:
            print(f"OK   {name}")
        else:
            print(f"DIFF {name}\n  reproduction: {mine}\n  original:     {theirs}")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
