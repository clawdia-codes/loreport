#!/usr/bin/env python3
"""
hub/domain_backfill.py — propose, review, then apply the optional `domain:` field
across an existing brain (docs/format-spec.md §1).

Single-file, Python-3-stdlib-only.

WHY THIS IS TWO COMMANDS AND NOT ONE
------------------------------------
`domain` says which side of a person's life an item belongs to. That is a
judgement about them, not a property of the text, and a machine reading
"deployed the staging cluster" cannot know whether that was their job or their
weekend. So this tool never decides: `propose` writes a plain-text mapping with
its reasoning, a human edits that file, and `apply` writes exactly what the file
says. An unreviewed proposal reaches no brain item.

That split is also what keeps this safe to run unattended. `propose` only reads;
`apply` refuses to run on a proposal whose items have changed since it was
written, so a stale review can never be replayed over newer content.

WHAT IT WILL NOT DO
-------------------
- It will not infer or alter `visibility`. `domain` is NOT cloud exposure — the
  two axes are independent (a work item may be local, a personal item may be
  shared), and letting one imply the other is exactly the mistake the field's
  spec text exists to prevent.
- It will not touch anything but a single `domain:` line in frontmatter. Bodies,
  human regions, and every other field are byte-preserved.
- It will not guess for a human: an item the heuristic is unsure about is
  proposed as `?`, and `apply` skips every `?` rather than defaulting it.

CLI:
    python3 hub/domain_backfill.py propose --brain-dir PATH --out FILE
    python3 hub/domain_backfill.py apply   --brain-dir PATH --from FILE [--dry-run]
"""

import argparse
import hashlib
import os
import re
import sys

DOMAINS = {"work", "personal", "both"}
UNSURE = "?"

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

# The heuristic is deliberately shallow and its output is deliberately labelled
# `?` whenever it does not fire. A cleverer classifier would produce confident
# wrong answers, which is worse here than an obvious blank: a wrong `personal`
# on a work item is invisible, while a `?` demands one second of human attention.
WORK_HINTS = ("employer", "colleague", "client", "customer", "invoice", "standup",
              "sprint planning", "jira", "on-call", "my job", "at work")
PERSONAL_HINTS = ("partner", "family", "child", "born", "training", "workout",
                  "home", "hobby", "holiday", "vacation")


def parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    fm = {}
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t-" or ":" not in line:
            continue
        k, v = line.split(":", 1)
        if v.strip():
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def item_paths(brain_dir):
    for sub in ("memories", "knowledge"):
        d = os.path.join(brain_dir, sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if fname.endswith(".md") and fname != "README.md":
                yield os.path.join(d, fname)


def file_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def propose_domain(fm, body):
    """Return (proposal, reason). Proposals are advisory only — see module docstring."""
    haystack = (fm.get("description", "") + " " + body).lower()
    work = [h for h in WORK_HINTS if h in haystack]
    personal = [h for h in PERSONAL_HINTS if h in haystack]
    if work and personal:
        return UNSURE, f"both signals present ({work[0]} / {personal[0]}) — a human decides"
    if work:
        return "work", f"matched {work[0]!r}"
    if personal:
        return "personal", f"matched {personal[0]!r}"
    return UNSURE, "no signal either way"


def cmd_propose(brain_dir, out_path):
    rows = []
    skipped = 0
    for path in item_paths(brain_dir):
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm, body = parse_frontmatter(text)
        if not fm or not fm.get("name"):
            skipped += 1
            continue
        if fm.get("domain"):
            skipped += 1  # already classified — never re-propose over a human's answer
            continue
        proposal, reason = propose_domain(fm, body)
        rows.append((fm["name"], proposal, file_digest(text), reason))

    lines = [
        "# domain backfill proposal — REVIEW THIS FILE, THEN RUN `apply`",
        "#",
        "# One item per line:  <name>\\t<domain>\\t<digest>\\t<reason>",
        "# Set <domain> to work | personal | both. Lines left as `?` are SKIPPED by",
        "# apply — that is the safe outcome, not an error. Delete a line to skip it.",
        "# Do not edit <digest>: it pins the item's content as of this proposal, and",
        "# apply refuses any line whose item has changed since, so a stale review can",
        "# never be replayed over newer text.",
        "#",
        "# `domain` is NOT cloud exposure. That is `visibility`. Never set one to match",
        "# the other — a work item may be local and a personal item may be shared.",
        "",
    ]
    for name, proposal, digest, reason in rows:
        lines.append(f"{name}\t{proposal}\t{digest}\t{reason}")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    unsure = sum(1 for r in rows if r[1] == UNSURE)
    print(f"proposed {len(rows)} item(s) -> {out_path} "
          f"({unsure} left as '{UNSURE}' for a human, {skipped} skipped as already-classified/invalid)")
    return 0


def read_proposal(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                raise ValueError(f"{path}:{lineno}: expected <name>\\t<domain>\\t<digest>")
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip(), lineno))
    return rows


def set_domain(text, value):
    """Return `text` with a single `domain:` frontmatter line set to `value`.

    Everything else is byte-preserved — the body is never re-serialised, so
    human regions and exact spacing survive untouched.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("no frontmatter")
    head = text[:m.end()]
    rest = text[m.end():]
    lines = head.splitlines()
    # lines[0] is the opening '---'; the closing '---' is the last non-empty one.
    close = max(i for i, l in enumerate(lines) if l.strip() == "---" and i > 0)
    replaced = False
    for i in range(1, close):
        if lines[i].split(":", 1)[0].strip() == "domain":
            lines[i] = f"domain: {value}"
            replaced = True
            break
    if not replaced:
        lines.insert(close, f"domain: {value}")
    return "\n".join(lines) + "\n" + rest


def cmd_apply(brain_dir, from_path, dry_run):
    by_name = {}
    for path in item_paths(brain_dir):
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        fm, _ = parse_frontmatter(text)
        if fm and fm.get("name"):
            by_name[fm["name"]] = (path, text)

    applied = skipped = 0
    errors = []
    for name, value, digest, lineno in read_proposal(from_path):
        if value == UNSURE:
            skipped += 1
            continue
        if value not in DOMAINS:
            errors.append(f"line {lineno}: domain '{value}' not in {sorted(DOMAINS)}")
            continue
        if name not in by_name:
            errors.append(f"line {lineno}: no item named '{name}' in this brain")
            continue
        path, text = by_name[name]
        if file_digest(text) != digest:
            errors.append(f"line {lineno}: '{name}' changed since the proposal was written "
                          f"(review it again — refusing to apply a stale decision)")
            continue
        new_text = set_domain(text, value)
        if new_text != text and not dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
        applied += 1

    for e in errors:
        print(f"ERROR: {e}")
    verb = "would apply" if dry_run else "applied"
    print(f"{verb} {applied}, skipped {skipped} unreviewed, {len(errors)} error(s)")
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose", help="write a reviewable domain mapping (reads only)")
    p.add_argument("--brain-dir", required=True)
    p.add_argument("--out", required=True)
    a = sub.add_parser("apply", help="apply a REVIEWED domain mapping")
    a.add_argument("--brain-dir", required=True)
    a.add_argument("--from", dest="from_path", required=True)
    a.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cmd == "propose":
        sys.exit(cmd_propose(args.brain_dir, args.out))
    sys.exit(cmd_apply(args.brain_dir, args.from_path, args.dry_run))


if __name__ == "__main__":
    main()
