#!/usr/bin/env python3
"""
hub/snapshot_publish.py — pinned-packet build + fail-closed egress scrub
(design.md §D16, modules.md M13d).

Single-file, Python-3-stdlib-only.

Pipeline:
  0. Classification gate — refuse to publish at all while any `memories/` or
     `knowledge/` item on `main` carries no explicit `visibility:` key
     (find_unclassified_items). The parser defaults an unmarked item to
     `local`, so it is already withheld and nothing leaks; this gate exists so
     that withholding is LOUD rather than a silently growing pile of items no
     provider can see. Same failure channel and same nonzero exit as the
     egress scrub below. Skills are exempt — they carry no `visibility:` at all.
  1. Build the packet — read from `main` (via `git show main:<path>`, NEVER
     the working tree, which the shared repo can have parked on any
     provider/* branch at any moment — mid-capture, or between merges):
     prompts/bootstrap.md + PROFILE.md + INDEX.md + INDEX-ARCHIVE.md (the
     last only when the brain has archived something), concatenated in that
     order, with a footer comment
     `<!-- loreport packet: main@<short-commit-hash> <date> -->` stamped from
     `main`'s own SHA (never `HEAD`, which may not be `main` at all). Refuses
     to run (clear error, nonzero exit) if `main` cannot be resolved.
     The packet contains EXACTLY those files, nothing else — no detail
     file bodies, ever. Both index portions are filtered: any line referencing
     a `visibility: local` item is dropped from the packet (the on-disk
     INDEX.md itself is untouched — only the published packet excludes local
     items; docs/visibility-design.md §3). Archived SHARED items are carried
     via INDEX-ARCHIVE.md so archiving never revokes cloud access to a shared
     item — see build_packet_text. That per-item visibility lookup is
     the ONLY place the fail-closed visibility parser (_visibility_from_text)
     is applied — it is never applied to bootstrap.md/PROFILE.md/INDEX.md
     themselves (none of which carry item frontmatter; running the parser on
     them would silently empty the packet, since it treats "no `---`
     frontmatter block" as `local`), nor to skills, which have no
     `visibility:` field at all and are always shared (_item_visibility).
  2. Fail-closed egress scrub — the same secret-regex patterns as
     brain_merge.py, run over the assembled packet. Any hit blocks the ENTIRE
     republish (exit nonzero, alert to stdout, alert written to
     hub/quarantine/digest.md). Never writes a partial packet.
  3. Deliver — on scrub pass: write hub/published/packet.md (current pinned
     packet) and hub/published/packet-<date>.md (archive copy).

CLI:
    python3 hub/snapshot_publish.py [--brain-dir PATH] [--dry-run] [--test-scrub]
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import date

# Identical secret-regex set to brain_merge.py / inbox_ingest.py (duplicated on
# purpose — every hub/*.py file is single-file and stdlib-only).
SECRET_PATTERNS = [
    # Best-effort defense-in-depth, NOT a guarantee of complete coverage — the
    # never-capture rule (prompts/bootstrap.md "Never capture") is the real
    # control; this scan is a backstop that a sufficiently novel secret shape
    # can still slip past.
    r"sk-[A-Za-z0-9-]{20,}",                                          # OpenAI-style secret key
    r"ghp_[A-Za-z0-9]{36}",                                           # GitHub PAT (classic)
    r"AKIA[0-9A-Z]{16}",                                              # AWS access key id
    r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
    r"github_pat_[A-Za-z0-9_]{20,}",                                  # GitHub fine-grained PAT
    r"gh[oprsu]_[A-Za-z0-9]{36,}",                                    # GitHub tokens: gho_/ghp_/ghu_/ghs_/ghr_
    r"xox[baprs]-[A-Za-z0-9-]{10,}",                                  # Slack token
    r"AIza[0-9A-Za-z_\-]{35}",                                        # Google API key
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",                            # PEM private-key block
    r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",       # JWT
    r"(postgres|mysql|mongodb(\+srv)?|redis|amqp)://[^\s]+:[^\s]+@",  # connection string w/ inline creds
]


def scan_secrets(text):
    for pat in SECRET_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return None


class MainUnresolvable(RuntimeError):
    """Raised when `main` cannot be resolved in this repo at all — never
    silently fall back to the working tree or HEAD in that case."""


def _run_git(brain_dir, *args, timeout=30):
    try:
        return subprocess.run(
            ["git", "-C", brain_dir] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} timed out")


def get_main_short_sha(brain_dir):
    """Return `main`'s short commit SHA, or None if `main` can't be resolved
    (not a git repo, or no such ref) — the caller must refuse to run rather
    than silently stamping/building the packet from something else."""
    r = _run_git(brain_dir, "rev-parse", "--short", "main")
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def read_from_main(brain_dir, relpath):
    """Return `relpath`'s content as committed on `main`, or None if it
    doesn't exist there. Reads via `git show main:<path>` — NEVER the working
    tree, which the shared repo can have parked on any provider/* branch at
    any moment (mid-capture, or between merges); mcp_server.py's
    _read_from_main does the same for the same reason."""
    r = _run_git(brain_dir, "show", f"main:{relpath}")
    if r.returncode != 0:
        return None
    text = r.stdout
    if not text.endswith("\n"):
        text += "\n"
    return text


# --- visibility filtering (§3/§4 of docs/visibility-design.md) ---------------
#
# Duplicated line-scan frontmatter parsing (same style as inbox_ingest.py /
# mcp_server.py — every hub/*.py file is single-file and stdlib-only, nothing
# is shared by import between them).

INDEX_ITEM_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _visibility_from_text(text):
    """Return "shared" or "local" for an item's raw text.

    FAIL CLOSED: an item is `shared` ONLY when its frontmatter carries an
    explicit `visibility: shared`. Everything else -- an ABSENT `visibility:`
    key, a malformed value, no frontmatter block at all -- is `local`. A false
    `local` merely hides an item from cloud providers -- visible and
    recoverable. A false `shared` leaks it -- neither.

    The absent-key case used to return "shared" (the old `absent = shared`
    frontmatter default). That was the only fail-OPEN default in the engine:
    an item nobody had classified was not merely unfiltered, it was positively
    published. See docs/format-spec.md 1 -- `visibility:` is now REQUIRED on
    items, and this parser is what makes forgetting it safe instead of costly.

    Skills are NOT items and never carry `visibility:` (format-spec.md 1).
    They are always shared. That carve-out lives in the RESOLVERS that know a
    path is a skill, never here -- this function only ever sees text.
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "local"
    seen = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if not sep or key.strip().lower() != "visibility":
            continue
        seen = value.split("#", 1)[0].strip().strip('"').strip("'").strip().lower()
    return "shared" if seen == "shared" else "local"


def _has_explicit_visibility(text):
    """True when the item's frontmatter carries a `visibility:` key at all,
    whatever its value.

    `_visibility_from_text` deliberately collapses "absent", "malformed" and
    "explicitly local" into one answer -- `local` -- because for EGRESS that
    is the whole point: all three must be withheld. But "absent" and
    "explicitly local" are not the same fact, and anything that RELAXES a
    control on the strength of `local` has to tell them apart, or the new
    default silently buys that relaxation for every unclassified item. Two
    callers need the distinction: the publish gate in snapshot_publish.py
    (which refuses while any item is unclassified) and the secret-scrub split
    in brain_merge.py (which may only demote a hit to a warning for an item a
    human actually marked local).

    Same line-scan and same fail-closed framing as `_visibility_from_text`: no
    `---` frontmatter block means no explicit visibility.
    """
    if text is None:
        return False
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, _value = line.partition(":")
        if sep and key.strip().lower() == "visibility":
            return True
    return False


def _candidate_item_relpaths(name):
    return (
        f"memories/{name}.md",
        f"knowledge/{name}.md",
        f"skills/{name}/SKILL.md",
    )


def _item_visibility(brain_dir, name):
    """Return "local" or "shared" for the named brain item, read FROM MAIN
    (never the working tree). An unresolvable [[name]] reference (no
    memories/knowledge/skills path by that name exists on main) defaults to
    "shared" — never silently drop an INDEX line the filter can't positively
    identify as an item at all. Once a path DOES resolve, its visibility is
    decided by _visibility_from_text, which fails CLOSED (shared ONLY on an
    explicit `visibility: shared`; an absent key, a malformed value and
    no-frontmatter-at-all are all `local`).

    SKILLS ARE THE ONE EXCEPTION, and it is not new policy — a skill is a
    package, not an item, and carries no `visibility:` field at all
    (docs/format-spec.md §1). hub/brain_merge.py's secret scrub and
    mcp_server's status counter have always said the same thing. Before the
    absent-key default was flipped to `local` this fell out for free; now it
    has to be stated, and it is stated HERE, at the resolver that knows the
    path is a skill, rather than in the parser (which only ever sees text and
    must stay byte-identical across its four copies).

    This function is only ever called from filter_index_text on resolved
    memories/knowledge/skills items; it is never applied to
    bootstrap.md/PROFILE.md/INDEX.md, which carry no item frontmatter and
    would otherwise be misclassified as local by the fail-closed parser."""
    for relpath in _candidate_item_relpaths(name):
        text = read_from_main(brain_dir, relpath)
        if text is not None:
            if relpath.startswith("skills/"):
                return "shared"
            return _visibility_from_text(text)
    return "shared"


def filter_index_text(brain_dir, index_text):
    """Drop any INDEX.md line that references a `visibility: local` item.
    Lines with no [[name]] reference (section headers, blank lines) always
    pass through unchanged. The on-disk INDEX.md is never modified — this
    filtering happens only to the in-memory text that goes into the packet."""
    out_lines = []
    for line in index_text.splitlines(keepends=True):
        m = INDEX_ITEM_RE.search(line)
        if m and _item_visibility(brain_dir, m.group(1)) == "local":
            continue
        out_lines.append(line)
    return "".join(out_lines)


def build_packet_text(brain_dir):
    """The §D1 operating surface, byte-for-byte: bootstrap + PROFILE + INDEX. Never
    widened with detail-file bodies (memories/, knowledge/, skills/ are excluded).

    Everything is read from `main` via `git show main:<path>` (read_from_main),
    never the working tree — the shared tree can be parked on any provider/*
    branch at any moment, and building/stamping the packet from that would
    silently publish an unmerged, un-scrubbed branch to every connected
    provider. Refuses to run if `main` can't be resolved at all."""
    main_sha = get_main_short_sha(brain_dir)
    if main_sha is None:
        raise MainUnresolvable(
            "cannot resolve `main` in this repo (no such ref, or not a git repo) — "
            "refusing to build a packet from an unknown/ambiguous source"
        )

    bootstrap = read_from_main(brain_dir, "prompts/bootstrap.md")
    profile = read_from_main(brain_dir, "PROFILE.md")
    index_raw = read_from_main(brain_dir, "INDEX.md")
    missing = [p for p, t in (
        ("prompts/bootstrap.md", bootstrap),
        ("PROFILE.md", profile),
        ("INDEX.md", index_raw),
    ) if t is None]
    if missing:
        raise FileNotFoundError(f"missing on main: {', '.join(missing)}")

    index = filter_index_text(brain_dir, index_raw)

    # THE ARCHIVE SEAM (docs/taxonomy-lifecycle-design.md Phase 2, "the sharp one").
    #
    # Cloud providers get this packet, not the repository — they cannot lazy-fetch
    # INDEX-ARCHIVE.md the way a filesystem host can. So if archiving removed an
    # item's line from INDEX.md and the packet carried INDEX.md alone, every
    # archived SHARED item would silently disappear from every cloud assistant's
    # view of the brain, with no error anywhere. Archiving is meant to shrink the
    # hot index on a filesystem host, NOT to revoke cloud access.
    #
    # Hence: the packet is INDEX + INDEX-ARCHIVE, both run through the SAME
    # visibility filter. The one and only thing ever excluded from the packet
    # stays `visibility: local` — that invariant is unchanged by this.
    #
    # INDEX-ARCHIVE.md is absent from a brain that has never archived anything;
    # that is normal and not an error, unlike the three files above.
    archive_raw = read_from_main(brain_dir, "INDEX-ARCHIVE.md")
    archive = filter_index_text(brain_dir, archive_raw) if archive_raw is not None else ""

    today = date.today().isoformat()
    footer = f"<!-- loreport packet: main@{main_sha} {today} -->\n"

    return bootstrap + profile + index + archive + footer


def find_unclassified_items(brain_dir):
    """Return the sorted relpaths of every `memories/`/`knowledge/` item on
    `main` that carries no explicit `visibility:` key.

    THE POINT OF THIS FUNCTION. Defaulting an unclassified item to `local`
    (see `_visibility_from_text`) makes forgetting the field safe, but it also
    makes forgetting it INVISIBLE: the item is quietly withheld from every
    provider, forever, and nothing ever says so. Silence is how a fail-open
    default went unnoticed in the first place; a fail-CLOSED default that is
    equally silent just moves the pile rather than draining it. So the default
    protects, and this gate reports.

    Skills are excluded: a skill is a package, not an item, and has no
    `visibility:` field to omit (docs/format-spec.md §1).

    Enumerated from `main` via `git ls-tree`, never a working-tree listing —
    the shared repo can be parked on any provider/* branch at any moment, and
    the packet this gates is built from `main` too, so the gate must judge
    exactly the tree that gets published."""
    r = _run_git(brain_dir, "ls-tree", "-r", "--name-only", "main")
    if r.returncode != 0:
        raise MainUnresolvable(
            "cannot enumerate `main` to check item classification — "
            "refusing to publish from an unknown tree"
        )
    unclassified = []
    for relpath in r.stdout.splitlines():
        if not relpath.endswith(".md"):
            continue
        if not (relpath.startswith("memories/") or relpath.startswith("knowledge/")):
            continue
        text = read_from_main(brain_dir, relpath)
        if text is None:
            continue  # raced with a concurrent write between ls-tree and show
        if not _has_explicit_visibility(text):
            unclassified.append(relpath)
    return sorted(unclassified)


def write_unclassified_alert(brain_dir, relpaths):
    """Log a blocked publish to the same quarantine digest the egress scrub
    uses. Deliberately the SAME channel and the same exit code: bin/loreport-sync
    already treats a nonzero snapshot_publish as "publish failed" and raises an
    alert (stderr + $LOREPORT_NOTIFY), so this reuses a signal path that is known
    to reach a human instead of inventing a second one nobody watches."""
    digest_path = os.path.join(brain_dir, "hub", "quarantine", "digest.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    is_new = not os.path.isfile(digest_path)
    from datetime import datetime
    ts = datetime.now().isoformat(timespec="seconds")
    with open(digest_path, "a", encoding="utf-8") as fh:
        if is_new:
            fh.write("# Quarantine digest\n\n"
                     "Every block that failed the inbox_ingest.py scan-before-commit\n"
                     "gate is logged here — nothing is silently dropped.\n\n")
        fh.write(f"## {ts} — PUBLISH BLOCKED (unclassified items)\n")
        fh.write(f"- reason: {len(relpaths)} item(s) carry no explicit `visibility:` "
                 "and are being withheld from every provider:\n")
        for relpath in relpaths:
            fh.write(f"  - {relpath}\n")
        fh.write("- action: add `visibility: shared` or `visibility: local` to each, "
                 "commit to main, then re-run snapshot_publish.py\n\n")


def write_alert(brain_dir, hit):
    digest_path = os.path.join(brain_dir, "hub", "quarantine", "digest.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    is_new = not os.path.isfile(digest_path)
    from datetime import datetime
    ts = datetime.now().isoformat(timespec="seconds")
    masked = hit[:6] + "…" if len(hit) > 6 else hit
    with open(digest_path, "a", encoding="utf-8") as fh:
        if is_new:
            fh.write("# Quarantine digest\n\n"
                     "Every block that failed the inbox_ingest.py scan-before-commit\n"
                     "gate is logged here — nothing is silently dropped.\n\n")
        fh.write(f"## {ts} — PUBLISH BLOCKED (egress scrub)\n")
        fh.write(f"- reason: secret found in packet: {masked}\n")
        fh.write("- action: rotate the secret, then re-run snapshot_publish.py\n\n")


def write_atomic(path, content):
    """Write `content` to `path` via a same-directory temp file + os.replace,
    so a crash mid-write can never leave a half-written packet on disk (a
    reader always sees either the old full file or the new full file, never a
    partial one) — REVIEW.md LOW-cluster polish item."""
    d = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".tmp-publish-")
    try:
        # mkstemp creates the temp file 0600 (owner-only); match the normal
        # 0644 a plain open("w") would have produced, so os.replace doesn't
        # silently tighten the published packet's permissions.
        os.chmod(tmp_path, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def default_brain_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_test_scrub(brain_dir):
    """Inject a fake secret into the assembled packet and verify the scrub blocks
    it. Never writes hub/published/ — this is an in-memory self-test."""
    try:
        packet = build_packet_text(brain_dir)
    except FileNotFoundError as e:
        print(f"ERROR: cannot build packet, missing file: {e}")
        sys.exit(1)
    except MainUnresolvable as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    poisoned = packet + "\nInjected test credential: sk-FAKE-item5-scrubme-0000\n"
    hit = scan_secrets(poisoned)
    if hit:
        print(f"PUBLISH BLOCKED: secret found in packet — {hit} Rotate the secret first.")
        write_alert(brain_dir, hit)
        sys.exit(1)
    print("FAIL: scrub did not detect the injected test secret")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Build the pinned PROFILE+INDEX publish packet with a fail-closed "
                    "egress secret scrub."
    )
    parser.add_argument("--brain-dir", default=None,
                        help="Brain repo root (default: inferred from this script's location)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and scrub the packet, print it, write nothing")
    parser.add_argument("--test-scrub", action="store_true",
                        help="Inject a fake secret and verify the scrub blocks the republish")
    args = parser.parse_args()

    brain_dir = args.brain_dir or default_brain_dir()

    if args.test_scrub:
        run_test_scrub(brain_dir)
        return

    # Classification gate — runs BEFORE the packet is built, so an unclassified
    # item is reported as itself rather than as a mysteriously shorter INDEX.
    # Blocks the whole republish, exactly as a secret hit does: the last good
    # packet.md stays on disk, so providers keep working from a slightly stale
    # index while a human classifies. Withholding is the safe half; refusing to
    # go on quietly is the half that keeps the pile from growing.
    try:
        unclassified = find_unclassified_items(brain_dir)
    except MainUnresolvable as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    if unclassified:
        print(f"PUBLISH BLOCKED: {len(unclassified)} item(s) carry no explicit "
              "`visibility:` and are being withheld from every provider:")
        for relpath in unclassified:
            print(f"  {relpath}")
        print("Add `visibility: shared` or `visibility: local` to each, commit to "
              "main, then re-run.")
        write_unclassified_alert(brain_dir, unclassified)
        sys.exit(1)

    try:
        packet = build_packet_text(brain_dir)
    except FileNotFoundError as e:
        print(f"ERROR: cannot build packet, missing file: {e}")
        sys.exit(1)
    except MainUnresolvable as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    hit = scan_secrets(packet)
    if hit:
        print(f"PUBLISH BLOCKED: secret found in packet — {hit} Rotate the secret first.")
        write_alert(brain_dir, hit)
        sys.exit(1)

    if args.dry_run:
        print(packet)
        print("--- dry-run: no files written ---")
        return

    published_dir = os.path.join(brain_dir, "hub", "published")
    os.makedirs(published_dir, exist_ok=True)
    today = date.today().isoformat()
    write_atomic(os.path.join(published_dir, "packet.md"), packet)
    write_atomic(os.path.join(published_dir, f"packet-{today}.md"), packet)
    print(f"Published packet.md ({len(packet)} bytes) and packet-{today}.md")


if __name__ == "__main__":
    main()
