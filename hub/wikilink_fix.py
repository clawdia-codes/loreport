#!/usr/bin/env python3
"""
hub/wikilink_fix.py — rewrite underscore wikilinks to hyphen slugs when uniquely resolvable.

Rewrites [[a_b]] → [[a-b]] in memories/ and knowledge/ bodies ONLY where the hyphen
target uniquely resolves across memories/, knowledge/, skills/. Skips content inside
<!-- human:start --> … <!-- human:end --> regions.

CLI:
    python3 hub/wikilink_fix.py --brain-dir PATH [--dry-run] [--apply]
"""

import argparse
import os
import re
import sys

HUMAN_REGION_RE = re.compile(
    r"(<!--\s*human:start\s*-->.*?<!--\s*human:end\s*-->)",
    re.DOTALL,
)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
UNDERSCORE_LINK_RE = re.compile(r"\[\[([a-z0-9]+(?:_[a-z0-9]+)+)\]\]")


def resolve_link(brain_dir, name):
    hits = []
    for candidate in (
        os.path.join(brain_dir, "memories", f"{name}.md"),
        os.path.join(brain_dir, "knowledge", f"{name}.md"),
        os.path.join(brain_dir, "skills", name, "SKILL.md"),
    ):
        if os.path.isfile(candidate):
            hits.append(candidate)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return "ambiguous"
    return None


def rewrite_outside_human_regions(text, brain_dir):
    parts = HUMAN_REGION_RE.split(text)
    changed_links = []
    out_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out_parts.append(part)
            continue
        def repl(m):
            old = m.group(1)
            new = old.replace("_", "-")
            if old == new:
                return m.group(0)
            old_res = resolve_link(brain_dir, old)
            new_res = resolve_link(brain_dir, new)
            if new_res and new_res != "ambiguous" and not old_res:
                changed_links.append((old, new))
                return f"[[{new}]]"
            return m.group(0)

        out_parts.append(UNDERSCORE_LINK_RE.sub(repl, part))
    return "".join(out_parts), changed_links


def scan_brain(brain_dir):
    results = []
    link_total = 0
    for folder in ("memories", "knowledge"):
        root = os.path.join(brain_dir, folder)
        if not os.path.isdir(root):
            continue
        for fname in sorted(os.listdir(root)):
            if not fname.endswith(".md") or fname == "README.md":
                continue
            path = os.path.join(root, fname)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                original = fh.read()
            updated, links = rewrite_outside_human_regions(original, brain_dir)
            if links:
                link_total += len(links)
                results.append(
                    {
                        "path": os.path.join(folder, fname),
                        "links": links,
                        "original": original,
                        "updated": updated,
                    }
                )
    return results, link_total


def main():
    parser = argparse.ArgumentParser(description="Fix underscore wikilinks where hyphen resolves")
    parser.add_argument("--brain-dir", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print per-file diff summary without writing (default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write fixed files",
    )
    args = parser.parse_args()
    brain_dir = os.path.abspath(args.brain_dir)
    if args.apply and args.dry_run:
        print("wikilink_fix: --apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    apply = args.apply
    dry_run = not apply

    results, link_total = scan_brain(brain_dir)
    for item in results:
        rel = item["path"]
        for old, new in item["links"]:
            print(f"  {rel}: [[{old}]] -> [[{new}]]")
    print(
        f"wikilink_fix: {len(results)} file(s), {link_total} link(s) rewritable"
        + (" (dry-run)" if dry_run else " (applied)")
    )

    if apply:
        for item in results:
            path = os.path.join(brain_dir, item["path"])
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(item["updated"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
