#!/usr/bin/env python3
"""
hub/brain_audit.py — classification + egress audit for a brain you point at.

WHY THIS EXISTS
    On 2026-08-07 three batches of memories reached cloud-published surfaces
    without ever having been classified, and nothing on this machine could have
    told anyone. `brain-template/doctor.sh` derives its target from its own
    directory (`cd "$(dirname "$0")"`), so it can only ever audit the folder it
    is sitting in — it cannot be pointed at a brain. `scripts/loreport-health`
    can be pointed anywhere but carries no leak assertion at all. This file is
    the missing instrument: run it against any brain, from anywhere.

    Driven by `scripts/loreport-audit --config <brain>/loreport.conf`.

WHERE EACH FACT IS READ FROM — the split is forced, not a preference:
  * Item frontmatter and the INDEX come from `main` (`git show main:<path>`),
    because that is what the PRODUCER used: snapshot_publish._item_visibility
    reads from main precisely because the shared tree can be parked on any
    provider/* branch mid-capture. A checker that read the worktree while the
    producer read main would be two encodings of one rule fed different inputs —
    which is the class of bug this file exists to catch.
  * The published surfaces come from DISK, because two of the three
    (hub/surface-chatgpt.md, hub/surface-claude-ai.md) are gitignored and do not
    exist on main at all. Disk is the only place they are.
  If `main` cannot be resolved we refuse to run rather than silently falling back
  to the worktree — same rule as snapshot_publish.MainUnresolvable.

VACUITY IS A FAILURE, NOT A PASS
    A safety assertion that iterates a collection is vacuously true when the
    collection is empty. That bug has already shipped here once: the published-
    packet privacy check passed on an EMPTY packet, because its loop body never
    ran. So every "nothing found" here is qualified by a positive assertion that
    there was something to look at, and those guards are UNCONDITIONAL — no
    `if shared_items_exist:` wrapper, because that wrapper is itself a path back
    to reporting green having examined nothing.

WHICH VISIBILITY RULE
    Fail-closed (`_visibility_from_text`, the 5-copy parser in inbox_ingest.py /
    snapshot_publish.py / brain_merge.py / mcp_server.py / report_build.py):
    anything not parseable as an explicit `shared` is treated as `local`. The
    other family in this repo (hub/project.py, brain-template/make-surface.sh,
    brain-template/doctor.sh) matches the whole line `^visibility:\\s*local\\s*$`
    and therefore calls `visibility: "local"`, `visibility: local  # temp` and
    `visibility:local` NOT-local — so hub/project.py keeps such an item in the
    paste-into-cloud surfaces while snapshot_publish drops it from the packet,
    and doctor.sh, using the producer's own rule, reports green. A checker must
    use the stricter rule, so a divergence surfaces as a leak instead of hiding.
    CANONICAL_VIS_RE below is the intersection every rule in the repo agrees on;
    anything outside it is reported even when it is not (yet) a leak.

Single-file, Python-3-stdlib-only, like every other hub/*.py. Exit codes:
    0  audited something and found nothing
    1  findings (leak, blind spot, risk, or metadata gap)
    2  cannot audit (no brain, `main` unresolvable) — never conflated with "clean"

CLI:
    python3 hub/brain_audit.py --brain-dir PATH [--quiet]
"""

import argparse
import os
import re
import subprocess
import sys

# --- what counts as published ------------------------------------------------
#
# Everything a cloud provider can end up holding. packet.md is what MCP-connected
# providers are served; the two surface-*.md files are the paste-into-a-web-chat
# projections. All three are checked, because guarding only the packet leaves the
# paste path — the one a human operates by hand — completely unguarded.
PUBLISHED_SURFACES = (
    "hub/published/packet.md",
    "hub/surface-chatgpt.md",
    "hub/surface-claude-ai.md",
)

ITEM_DIRS = ("memories", "knowledge")
INDEX_FILES = ("INDEX.md", "INDEX-ARCHIVE.md")

ANY_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
CATALOG_LINE_RE = re.compile(r"(?m)^- \[\[([^\]]+)\]\]")

VALID_DOMAINS = ("work", "personal", "both")

# The one spelling every visibility rule in this repo agrees on. doctor.sh uses
# `grep -qx 'visibility: local'` (exact whole line), make-surface.sh allows
# surrounding space, project.py allows space and any case, and the fail-closed
# Python parser accepts quotes and trailing comments. Their intersection is
# exactly this, so anything else means two components disagree about one item.
CANONICAL_VIS_RE = re.compile(r"^visibility: (shared|local)$")


class Finding:
    """A single audit result. `severity` orders the report; every severity is a
    nonzero exit, because "the check could not see anything" and "the check saw a
    leak" are both failures — only one of them is an emergency."""

    ORDER = {"LEAK": 0, "BLIND": 1, "RISK": 2, "META": 3}

    def __init__(self, severity, message, fix):
        self.severity = severity
        self.message = message
        self.fix = fix

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Finding({self.severity!r}, {self.message!r})"


class CannotAudit(RuntimeError):
    """The brain cannot be read at all. Distinct from "found problems": exit 2,
    never folded into the findings list, so a broken invocation can never be
    mistaken for a clean bill of health."""


# --- git access (main only) --------------------------------------------------

def _run_git(brain_dir, *args, timeout=30):
    try:
        return subprocess.run(
            ["git", "-C", brain_dir] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise CannotAudit(f"git {' '.join(args)} timed out in {brain_dir}")


def get_main_sha(brain_dir):
    r = _run_git(brain_dir, "rev-parse", "--short", "main")
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def read_from_main(brain_dir, relpath):
    """Content of `relpath` as committed on main, or None if absent there."""
    r = _run_git(brain_dir, "show", f"main:{relpath}")
    if r.returncode != 0:
        return None
    return r.stdout


def list_main_files(brain_dir, prefix):
    """Every file under `prefix` on main, as repo-relative paths."""
    r = _run_git(brain_dir, "ls-tree", "-r", "--name-only", "main", "--", prefix)
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


# --- frontmatter -------------------------------------------------------------

def parse_frontmatter(text):
    """Return (has_frontmatter, fields, raw_lines_of_frontmatter).

    `fields` is last-wins, lowercased keys, values stripped of a trailing
    `# comment` and of surrounding quotes — byte-for-byte the normalisation the
    five copies of `_visibility_from_text` perform, so this checker classifies an
    item exactly the way the publisher does.
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False, {}, []
    fields = {}
    raw = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        raw.append(line)
        key, sep, value = line.partition(":")
        if not sep:
            continue
        cleaned = value.split("#", 1)[0].strip().strip('"').strip("'").strip()
        fields[key.strip().lower()] = cleaned.lower()
    return True, fields, raw


def effective_visibility(text):
    """FAIL CLOSED: `shared` only for an explicit, parseable `shared`; anything
    else — absent frontmatter, an unparseable value, a typo — is `local`.

    Note the deliberate asymmetry with the *spec*, where an ABSENT `visibility:`
    means shared. That default is the leak: an item nobody classified is
    cloud-published by omission. `visibility_problem` below reports the absence
    as a finding, while this function keeps the publisher's own semantics so the
    leak assertions compare like with like.
    """
    ok, fields, _ = parse_frontmatter(text)
    if not ok:
        return "local"
    seen = fields.get("visibility")
    if seen is None:
        return "shared"
    return "shared" if seen == "shared" else "local"


def visibility_problem(text):
    """Return None, or a human-readable reason this item's `visibility:` cannot
    be trusted: absent (silently cloud-published), unparseable, or spelled in a
    form the repo's two rule families disagree about."""
    ok, fields, raw = parse_frontmatter(text)
    if not ok:
        return "no frontmatter block at all"
    if "visibility" not in fields:
        return "no explicit `visibility:` — absent means SHARED, i.e. cloud-published"
    canonical = [ln for ln in raw if CANONICAL_VIS_RE.match(ln)]
    if not canonical:
        shown = next((ln.strip() for ln in raw if ln.partition(":")[0].strip().lower() == "visibility"), "?")
        return (f"`visibility:` is not in canonical form ({shown!r}) — hub/project.py and "
                "make-surface.sh will read it differently from snapshot_publish.py")
    return None


def domain_problem(text):
    ok, fields, _ = parse_frontmatter(text)
    if not ok:
        return "no frontmatter block at all"
    seen = fields.get("domain")
    if seen is None:
        return "no explicit `domain:`"
    if seen not in VALID_DOMAINS:
        return f"`domain: {seen}` is not one of {'/'.join(VALID_DOMAINS)}"
    return None


# --- item resolution ---------------------------------------------------------

def candidate_relpaths(name):
    """The same trio snapshot_publish._candidate_item_relpaths uses. Resolving
    only memories/ would miss a local *knowledge* item leaking into a surface."""
    return (
        f"memories/{name}.md",
        f"knowledge/{name}.md",
        f"skills/{name}/SKILL.md",
    )


def resolve_item(brain_dir, name, cache):
    """(relpath, text) for a `[[name]]` as it exists on main, or (None, None).

    Unlike snapshot_publish._item_visibility — which defaults an unresolvable
    name to "shared" because a PRODUCER must never silently drop an INDEX line it
    cannot identify — a CHECKER returns "unknown" here. You cannot certify a name
    you cannot resolve, and pretending otherwise is how a check stays green.
    """
    if name in cache:
        return cache[name]
    for relpath in candidate_relpaths(name):
        text = read_from_main(brain_dir, relpath)
        if text is not None:
            cache[name] = (relpath, text)
            return cache[name]
    cache[name] = (None, None)
    return cache[name]


# --- the audit ---------------------------------------------------------------

def audit(brain_dir):
    """Return (findings, counts). Raises CannotAudit if the brain is unreadable."""
    if not os.path.isdir(brain_dir):
        raise CannotAudit(f"not a directory: {brain_dir}")
    main_sha = get_main_sha(brain_dir)
    if main_sha is None:
        raise CannotAudit(
            f"cannot resolve `main` in {brain_dir} (no such ref, or not a git repo) — "
            "refusing to audit the working tree instead, which may be parked on any "
            "provider/* branch"
        )

    findings = []
    counts = {"main": main_sha}
    cache = {}

    def add(severity, message, fix):
        findings.append(Finding(severity, message, fix))

    # --- 1. every item on main is explicitly classified ----------------------
    item_paths = []
    for d in ITEM_DIRS:
        item_paths += [p for p in list_main_files(brain_dir, d)
                       if p.endswith(".md") and os.path.basename(p) != "README.md"]
    item_paths.sort()

    counts["items"] = len(item_paths)
    counts["memories"] = sum(1 for p in item_paths if p.startswith("memories/"))

    # UNCONDITIONAL vacuity guard. Every assertion below iterates this list; if it
    # is empty they are all vacuously true and the run would report a clean brain
    # having read nothing.
    if not item_paths:
        add("BLIND", f"no items found under {'/, '.join(ITEM_DIRS)}/ on main — this run "
                     "asserted nothing about classification",
            f"check that {brain_dir} is the brain you meant and that main carries its items")

    vis_ok = dom_ok = shared = local = 0
    for relpath in item_paths:
        text = read_from_main(brain_dir, relpath) or ""
        vproblem = visibility_problem(text)
        if vproblem:
            add("RISK", f"{relpath}: {vproblem}",
                "add an explicit `visibility: shared` or `visibility: local` line")
        else:
            vis_ok += 1
        dproblem = domain_problem(text)
        if dproblem:
            add("META", f"{relpath}: {dproblem}",
                f"add `domain: {'|'.join(VALID_DOMAINS)}` to the frontmatter")
        else:
            dom_ok += 1
        if effective_visibility(text) == "shared":
            shared += 1
        else:
            local += 1

    counts.update({"visibility_ok": vis_ok, "domain_ok": dom_ok,
                   "shared": shared, "local": local})

    # --- 2. no local item appears on a published surface ---------------------
    surface_refs = {}
    for rel in PUBLISHED_SURFACES:
        path = os.path.join(brain_dir, rel)
        if not os.path.isfile(path):
            # Missing is BLIND, not benign: the leak assertion for this surface
            # did not run, so nothing in this report speaks for it.
            add("BLIND", f"{rel} is missing — this run asserted nothing about it",
                "run bin/loreport-sync (packet) or hub/project.py (surfaces), then re-audit")
            surface_refs[rel] = 0
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            add("BLIND", f"{rel} is unreadable ({e}) — this run asserted nothing about it",
                "fix permissions on the file, then re-audit")
            surface_refs[rel] = 0
            continue

        catalog = CATALOG_LINE_RE.findall(text)
        surface_refs[rel] = len(catalog)

        # UNCONDITIONAL. The shipped bug was exactly this loop passing on an empty
        # packet. It is NOT gated on "…and shared items exist on disk": that gate
        # is itself a way to report green having looked at nothing.
        if not catalog:
            add("BLIND", f"{rel} carries zero `- [[item]]` lines — the leak scan over it "
                         "was vacuous, and providers reading it are receiving nothing",
                "rebuild it (bin/loreport-sync / hub/project.py) and re-audit")

        # Scan EVERY wikilink, not just catalogue lines: an item named in prose is
        # exposed just as much as one in a list. Names that resolve to nothing are
        # grammar placeholders like [[<kebab-slug>]] in the embedded bootstrap, and
        # are only worth reporting when they sit on a catalogue line (below).
        for name in sorted(set(ANY_WIKILINK_RE.findall(text))):
            item_rel, item_text = resolve_item(brain_dir, name, cache)
            if item_rel is None:
                continue
            if effective_visibility(item_text) == "local":
                add("LEAK", f"LEAK: local item [[{name}]] ({item_rel}) appears in {rel}",
                    "remove it from that surface and rebuild; treat the exposure as real")

        for name in sorted(set(catalog)):
            item_rel, _ = resolve_item(brain_dir, name, cache)
            if item_rel is None:
                add("RISK", f"{rel} catalogues [[{name}]], which resolves to no item on main — "
                            "its visibility cannot be certified",
                    "fix the name or remove the line; an unresolvable entry is unauditable")

    counts["surface_refs"] = surface_refs

    # --- 3. counts reconcile: on disk vs classified vs published -------------
    packet_rel = PUBLISHED_SURFACES[0]
    packet_path = os.path.join(brain_dir, packet_rel)
    packet_names = set()
    if os.path.isfile(packet_path):
        with open(packet_path, encoding="utf-8") as fh:
            packet_names = set(ANY_WIKILINK_RE.findall(fh.read()))

    indexed = 0
    reach_checked = 0
    for index_rel in INDEX_FILES:
        index_text = read_from_main(brain_dir, index_rel)
        if index_text is None:
            continue
        for name in CATALOG_LINE_RE.findall(index_text):
            indexed += 1
            item_rel, item_text = resolve_item(brain_dir, name, cache)
            if item_rel is None:
                continue
            if effective_visibility(item_text) != "shared":
                continue
            reach_checked += 1
            if name not in packet_names:
                add("RISK", f"shared item [[{name}]] is catalogued in {index_rel} but absent "
                            f"from {packet_rel} — providers cannot see it",
                    "re-run hub/snapshot_publish.py; if it stays absent the publish filter is dropping it")
    counts["indexed"] = indexed
    counts["reach_checked"] = reach_checked

    if indexed == 0:
        add("BLIND", "no INDEX catalogue lines on main — the on-disk/classified/published "
                     "reconciliation asserted nothing",
            "check INDEX.md on main; brain_merge.py rebuilds it")

    findings.sort(key=lambda f: (Finding.ORDER.get(f.severity, 9), f.message))
    return findings, counts


# --- reporting ---------------------------------------------------------------

def format_report(brain_dir, findings, counts):
    out = []
    out.append(f"=== Loreport audit: {brain_dir} (main@{counts['main']}) ===")
    out.append("")
    out.append(f"  items on main            {counts['items']}  ({counts['memories']} in memories/)")
    out.append(f"    explicit visibility    {counts['visibility_ok']}")
    out.append(f"    explicit domain        {counts['domain_ok']}")
    out.append(f"    shared / local         {counts['shared']} / {counts['local']}")
    out.append(f"  catalogued in INDEX      {counts['indexed']}")
    out.append(f"    shared, reach-checked  {counts['reach_checked']}")
    for rel, n in counts["surface_refs"].items():
        out.append(f"  {rel:<24} {n} catalogued item(s)")
    out.append("")
    if not findings:
        out.append("PASS — every item is explicitly classified and no local item reaches a "
                   "published surface.")
        return "\n".join(out) + "\n"
    for f in findings:
        out.append(f"✗ [{f.severity}] {f.message}")
        out.append(f"    fix: {f.fix}")
    out.append("")
    leaks = sum(1 for f in findings if f.severity == "LEAK")
    blind = sum(1 for f in findings if f.severity == "BLIND")
    out.append(f"FAIL — {len(findings)} finding(s): {leaks} leak(s), {blind} blind spot(s).")
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit a brain's classification and its published surfaces for leaks."
    )
    parser.add_argument("--brain-dir", required=True, help="Brain repo root")
    parser.add_argument("--quiet", action="store_true",
                        help="Print only findings and the verdict, not the counts")
    args = parser.parse_args(argv)

    try:
        findings, counts = audit(args.brain_dir)
    except CannotAudit as e:
        print(f"loreport-audit: CANNOT AUDIT: {e}", file=sys.stderr)
        return 2

    report = format_report(args.brain_dir, findings, counts)
    if args.quiet:
        report = "\n".join(ln for ln in report.splitlines()
                           if not ln.startswith("  ")) + "\n"
    print(report, end="")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
