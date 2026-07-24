#!/usr/bin/env python3
"""
hub/brain_merge.py — Loreport daily reconciliation (design.md §D14, modules.md M13b).

Single-file, Python-3-stdlib-only. Performs the hub's daily branch merge:

  1. Backup   — tag `main` (`pre-merge/<date>`), the rollback point.
  2. Fetch    — pull all provider branches.
  3. Merge    — into `main`, fixed order: provider/openclaw -> provider/claude ->
                provider/chatgpt. INDEX.md is excluded from every merge (deleted from
                the working tree before each merge, regenerated in step 6 — it is a
                derived artifact and must never be hand-merged).
  4. Consolidation-lite — mechanical exact/near-duplicate-key flagging (fuzzy
     semantic dedup is left to `prompts/consolidate.md`, run by a human-in-the-loop).
  5. Secret-scrub gate — fail-closed: any hit aborts the merge commit; nothing enters
     `main`.
  6. INDEX rebuild — deterministic: same input item set -> same INDEX.md bytes, always.
  7. Fast-forward each provider branch to the new `main`.

CLI:
    python3 hub/brain_merge.py [--brain-dir PATH] [--test-determinism] [--dry-run]
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime

# --- constants -------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))

_FALLBACK_PROVIDER_ORDER = ["provider/openclaw", "provider/claude", "provider/chatgpt"]


def _load_provider_order():
    """Derive PROVIDER_ORDER (branch names sorted by merge_order) from
    hub/config/providers.json (path relative to this script's own dir). Falls
    back to the hardcoded default list above if the file is missing or
    unparseable, so a broken/absent config can never crash the merge."""
    config_path = os.path.join(HERE, "config", "providers.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        ordered = sorted(cfg["providers"].items(), key=lambda kv: kv[1]["merge_order"])
        return [info["branch"] for _name, info in ordered]
    except (OSError, ValueError, KeyError, TypeError):
        return list(_FALLBACK_PROVIDER_ORDER)


PROVIDER_ORDER = _load_provider_order()
ITEM_TYPES = {"user", "feedback", "project", "reference", "knowledge"}

# Same secret-regex set used by inbox_ingest.py and snapshot_publish.py (duplicated
# on purpose — every hub/*.py file is single-file and stdlib-only, so nothing is
# imported between them).
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9-]{20,}",                     # OpenAI-style secret key
    r"ghp_[A-Za-z0-9]{36}",                       # GitHub personal access token
    r"AKIA[0-9A-Z]{16}",                          # AWS access key id (bare API key pattern)
    r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
]

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


# --- small parsing helpers ---------------------------------------------------

def parse_simple_yaml_scalars(text):
    """Parse top-level `key: value` scalar lines. Ignores indented/list lines —
    enough for item frontmatter and skill meta.yaml (name/description/type)."""
    result = {}
    for line in text.splitlines():
        if not line or line[0] in " \t-":
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v:
            result[k] = v
    return result


def parse_frontmatter(text):
    """Return (frontmatter-dict-or-None, body)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return parse_simple_yaml_scalars(m.group(1)), text[m.end():]


def read_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# --- deterministic INDEX rebuild --------------------------------------------

def build_index_bytes(brain_dir):
    """Scan memories/, knowledge/, skills/ and build INDEX.md bytes.
    Deterministic: sorted alphabetically within each section; no reliance on
    filesystem iteration order, mtimes, or any other non-content input."""
    memories = []
    mem_dir = os.path.join(brain_dir, "memories")
    if os.path.isdir(mem_dir):
        for fname in sorted(os.listdir(mem_dir)):
            if not fname.endswith(".md"):
                continue
            fm, _ = parse_frontmatter(read_file(os.path.join(mem_dir, fname)))
            if not fm:
                continue
            name, desc, typ = fm.get("name"), fm.get("description"), fm.get("type")
            if not name or not desc or typ not in ITEM_TYPES:
                continue
            memories.append((name, desc, typ))

    knowledge = []
    know_dir = os.path.join(brain_dir, "knowledge")
    if os.path.isdir(know_dir):
        for fname in sorted(os.listdir(know_dir)):
            if not fname.endswith(".md"):
                continue
            fm, _ = parse_frontmatter(read_file(os.path.join(know_dir, fname)))
            if not fm:
                continue
            name, desc = fm.get("name"), fm.get("description")
            if not name or not desc:
                continue
            knowledge.append((name, desc))

    skills = []
    skills_dir = os.path.join(brain_dir, "skills")
    if os.path.isdir(skills_dir):
        for sname in sorted(os.listdir(skills_dir)):
            meta_path = os.path.join(skills_dir, sname, "meta.yaml")
            if not os.path.isfile(meta_path):
                continue
            meta = parse_simple_yaml_scalars(read_file(meta_path))
            name = meta.get("name", sname)
            desc = meta.get("description", "")
            skills.append((name, desc))

    memories.sort(key=lambda t: t[0])
    knowledge.sort(key=lambda t: t[0])
    skills.sort(key=lambda t: t[0])

    lines = ["# Index", "", "## Memories"]
    for name, desc, typ in memories:
        lines.append(f"- [[{name}]] — {desc}  ({typ})")
    lines.append("")
    lines.append("## Knowledge")
    for name, desc in knowledge:
        lines.append(f"- [[{name}]] — {desc}  (knowledge)")
    lines.append("")
    lines.append("## Skills")
    for name, desc in skills:
        lines.append(f"- [[{name}]] — {desc}  (skill)")

    content = "\n".join(lines) + "\n"
    return content.encode("utf-8"), len(memories), len(knowledge), len(skills)


# --- consolidation-lite: mechanical dedup flags -----------------------------

def find_dupes(brain_dir):
    """Mechanical-only dedup flags (fuzzy/semantic merge is consolidate.md's job):
    exact-duplicate body text, or two different names sharing one description."""
    seen_body = {}
    seen_desc = {}
    dupes = []
    for sub in ("memories", "knowledge"):
        d = os.path.join(brain_dir, sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".md"):
                continue
            rel = os.path.join(sub, fname)
            fm, body = parse_frontmatter(read_file(os.path.join(d, fname)))
            if fm is None:
                continue
            body_key = body.strip()
            if body_key and body_key in seen_body:
                dupes.append(f"exact-dup: {seen_body[body_key]} == {rel}")
            else:
                seen_body[body_key] = rel
            desc = fm.get("description")
            if desc:
                if desc in seen_desc and seen_desc[desc] != rel:
                    dupes.append(f"near-dup (same description): {seen_desc[desc]} ~ {rel}")
                else:
                    seen_desc[desc] = rel
    return dupes


# --- secret scrub ------------------------------------------------------------

def scan_text_for_secrets(text):
    for pat in SECRET_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return None


def mask(secret):
    return secret[:6] + "…" if len(secret) > 6 else secret


def test_scrub():
    """Self-test the secret-scrub detector in-memory (no disk/git writes, so
    there is zero residue either way): exit 0 when the injected secret IS
    detected (the scrub is verified working), nonzero only when it's MISSED
    (REVIEW.md #20/M4 — previously both paths exited 1, making this
    unfalsifiable in CI)."""
    injected = 'api_key: "sk-abcdefghijklmnopqrstuvwx1234567890"'
    hit = scan_text_for_secrets(injected)
    if hit:
        print(f"PASS: --test-scrub detected the injected secret ({mask(hit)})")
        return 0
    print("FAIL: --test-scrub did NOT detect the injected secret")
    return 1


def scan_brain_for_secrets(brain_dir):
    for sub in ("memories", "knowledge"):
        d = os.path.join(brain_dir, sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(d, fname)
            hit = scan_text_for_secrets(read_file(path))
            if hit:
                return os.path.join(sub, fname), mask(hit)
    skills_dir = os.path.join(brain_dir, "skills")
    if os.path.isdir(skills_dir):
        for root, _dirs, files in os.walk(skills_dir):
            for fname in sorted(files):
                path = os.path.join(root, fname)
                hit = scan_text_for_secrets(read_file(path))
                if hit:
                    return os.path.relpath(path, brain_dir), mask(hit)
    profile_path = os.path.join(brain_dir, "PROFILE.md")
    if os.path.isfile(profile_path):
        hit = scan_text_for_secrets(read_file(profile_path))
        if hit:
            return "PROFILE.md", mask(hit)
    return None


# --- git plumbing ------------------------------------------------------------

def git(brain_dir, *args, check=True, timeout=30):
    try:
        result = subprocess.run(
            ["git", "-C", brain_dir] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} timed out")
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


# Same repo-wide lock as inbox_ingest.py (identical relative path) so a
# capture and a merge can never interleave their git mutations (REVIEW.md
# #5). Single-file/stdlib-only means this is duplicated rather than
# imported, same as SECRET_PATTERNS etc.
LOCK_RELPATH = os.path.join("hub", ".loreport.lock")


@contextlib.contextmanager
def brain_lock(brain_dir):
    lock_path = os.path.join(brain_dir, LOCK_RELPATH)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def branch_exists(brain_dir, branch):
    r = git(brain_dir, "rev-parse", "--verify", "--quiet", branch, check=False)
    return r.returncode == 0


def conflicted_files(brain_dir):
    r = git(brain_dir, "diff", "--name-only", "--diff-filter=U", check=False)
    return [line for line in r.stdout.splitlines() if line]


def merge_in_progress(brain_dir):
    r = git(brain_dir, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
    return r.returncode == 0


def has_staged_changes(brain_dir):
    r = git(brain_dir, "diff", "--cached", "--quiet", check=False)
    return r.returncode != 0


def unique_rename(brain_dir, rel_path):
    """Return a `<name>-2`, `<name>-3`, ... rel path for `rel_path` that does
    NOT already exist on disk — skip any that do, so a fresh add/add
    collision never silently overwrites a previous `-N` twin (REVIEW.md #7)."""
    d = os.path.dirname(rel_path)
    base = os.path.basename(rel_path)
    stem, ext = os.path.splitext(base)
    n = 2
    while True:
        candidate = os.path.join(d, f"{stem}-{n}{ext}") if d else f"{stem}-{n}{ext}"
        if not os.path.exists(os.path.join(brain_dir, candidate)):
            return candidate
        n += 1


def conflict_stages(brain_dir, path):
    """Return the set of index stages present for an unmerged `path`, via
    `git ls-files -u` (stage 1 = merge-base/common ancestor, 2 = ours/HEAD,
    3 = theirs/incoming branch). Absence of stage 1 means there is no common
    ancestor version of this file at all — a true add/add, not an
    update/update of something that already existed (REVIEW.md #7/F4)."""
    r = git(brain_dir, "ls-files", "-u", "--", path, check=False)
    stages = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            try:
                stages.add(int(parts[2]))
            except ValueError:
                pass
    return stages


def resolve_conflicted_file(brain_dir, branch, f, report, today):
    """Classify and resolve one non-PROFILE/non-INDEX conflicted file from
    merging `branch` into main (REVIEW.md #7/F4):

      - base+ours+theirs all present -> update/update of an EXISTING item:
        the trust-order winner (--ours, i.e. main/earlier-merged branch)
        wins; theirs is discarded with a digest note, never forked into a
        `-2` twin.
      - ours+theirs present, base absent -> true add/add (same name coined
        independently, no common ancestor): rename theirs via a uniqueness
        loop (`-2`, `-3`, ... skipping any that already exist).
      - base+one side present, other absent -> modify/delete: the
        documented winner is the deletion; digest it explicitly rather than
        silently resurrecting+duplicating the deleted item.
    """
    stages = conflict_stages(brain_dir, f)
    has_base, has_ours, has_theirs = 1 in stages, 2 in stages, 3 in stages

    if has_base and has_ours and has_theirs:
        theirs_res = git(brain_dir, "show", f"{branch}:{f}", check=False)
        discarded = len(theirs_res.stdout.encode("utf-8")) if theirs_res.returncode == 0 else 0
        report["conflict_notes"].append(
            f"concurrent update on {f}: kept ours, discarded {discarded} bytes "
            f"from {branch} (recover at pre-merge/{today})"
        )
        git(brain_dir, "checkout", "--ours", "--", f, check=False)
        git(brain_dir, "add", f, check=False)
    elif has_ours and has_theirs:  # not has_base -> true add/add
        theirs_res = git(brain_dir, "show", f"{branch}:{f}", check=False)
        if theirs_res.returncode == 0:
            new_rel = unique_rename(brain_dir, f)
            new_abs = os.path.join(brain_dir, new_rel)
            os.makedirs(os.path.dirname(new_abs) or brain_dir, exist_ok=True)
            with open(new_abs, "w", encoding="utf-8") as fh:
                fh.write(retag_name(theirs_res.stdout, new_rel))
            git(brain_dir, "checkout", "--ours", "--", f, check=False)
            git(brain_dir, "add", f, new_rel, check=False)
            report["renamed"].append(f"{f} -> {new_rel}")
        else:
            git(brain_dir, "checkout", "--ours", "--", f, check=False)
            git(brain_dir, "add", f, check=False)
    elif has_base and has_ours and not has_theirs:
        report["conflict_notes"].append(
            f"modify/delete on {f}: {branch} deleted, ours modified -- deletion wins"
        )
        git(brain_dir, "rm", "-f", f, check=False)
    elif has_base and has_theirs and not has_ours:
        report["conflict_notes"].append(
            f"modify/delete on {f}: main deleted, {branch} modified -- deletion wins"
        )
        git(brain_dir, "rm", "-f", f, check=False)
    else:
        # Any other stage combination (shouldn't normally arise): fall back
        # to the old keep-ours/rename-theirs behavior rather than crashing.
        theirs_res = git(brain_dir, "show", f"{branch}:{f}", check=False)
        if theirs_res.returncode == 0:
            new_rel = unique_rename(brain_dir, f)
            new_abs = os.path.join(brain_dir, new_rel)
            os.makedirs(os.path.dirname(new_abs) or brain_dir, exist_ok=True)
            with open(new_abs, "w", encoding="utf-8") as fh:
                fh.write(retag_name(theirs_res.stdout, new_rel))
            git(brain_dir, "checkout", "--ours", "--", f, check=False)
            git(brain_dir, "add", f, new_rel, check=False)
            report["renamed"].append(f"{f} -> {new_rel}")
        else:
            git(brain_dir, "checkout", "--ours", "--", f, check=False)
            git(brain_dir, "add", f, check=False)


def retag_name(content, new_rel):
    stem = os.path.splitext(os.path.basename(new_rel))[0]
    fm, body = parse_frontmatter(content)
    if fm is None:
        return content
    fm = dict(fm)
    fm["name"] = stem
    lines = ["---"]
    for k in ("name", "description", "type"):
        if k in fm:
            lines.append(f"{k}: {fm[k]}")
    for k, v in fm.items():
        if k not in ("name", "description", "type"):
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


# --- the merge ----------------------------------------------------------------

def do_merge(brain_dir, dry_run):
    today = date.today().isoformat()
    report = {
        "merged": [],
        "renamed": [],
        "profile_conflicts": [],
        "near_dupes": [],
        "scrub": "PASS",
        "index_counts": (0, 0, 0),
        "fast_forwarded": [],
        "ff_skipped": [],
        "conflict_notes": [],
    }

    # The whole mutating sequence — including the --dry-run branch, which
    # still mutates before resetting — runs under one exclusive repo lock so
    # a capture (inbox_ingest.py) can never interleave with a merge
    # (REVIEW.md #5).
    with brain_lock(brain_dir):
        git(brain_dir, "checkout", "main")
        orig_head = git(brain_dir, "rev-parse", "HEAD").stdout.strip()
        tag_name = f"pre-merge/{today}"
        if not dry_run:
            git(brain_dir, "tag", "-f", tag_name, check=False)
        git(brain_dir, "fetch", "--all", check=False)

        # Compare-and-swap fast-forward (REVIEW.md #3/#5): record each
        # provider branch's SHA now, before any merging happens. At the
        # final ff step we only force a branch onto the new main if it
        # STILL points at the SHA we actually merged — if a capture landed
        # on it mid-merge, its extra commit(s) are left alone (main is
        # still their ancestor, so they merge cleanly next run) instead of
        # being silently discarded by a blind `branch -f`.
        pre_merge_shas = {}
        for branch in PROVIDER_ORDER:
            if branch_exists(brain_dir, branch):
                pre_merge_shas[branch] = git(brain_dir, "rev-parse", branch).stdout.strip()

        try:
            # INDEX.md is never a merge input: remove it once, up front, as
            # its own committed change. (A bare, uncommitted `git rm` here
            # would make every subsequent `git merge` fail outright — git
            # refuses to merge over uncommitted local changes — so the
            # removal must land in its own commit before any branch merge is
            # attempted.) It is regenerated wholesale in step 6, after every
            # provider branch has been merged in.
            index_path = os.path.join(brain_dir, "INDEX.md")
            if os.path.exists(index_path) and not dry_run:
                git(brain_dir, "rm", "-f", "--quiet", "INDEX.md", check=False)
                if has_staged_changes(brain_dir):
                    git(brain_dir, "commit", "-m", "brain(merge): drop INDEX.md (derived artifact)", check=False)

            for branch in PROVIDER_ORDER:
                if not branch_exists(brain_dir, branch):
                    continue

                r = git(brain_dir, "merge", "--no-commit", "--no-ff", branch, check=False)

                if r.returncode != 0 and not merge_in_progress(brain_dir):
                    # The merge never actually started (e.g. dirty working
                    # tree) — this is an operator error, not a content
                    # conflict. Abort loudly rather than silently skip the
                    # branch's contributions.
                    report["scrub"] = f"ABORT: merge of {branch} failed to start: {r.stderr.strip()}"
                    print_report(today, report)
                    git(brain_dir, "merge", "--abort", check=False)
                    git(brain_dir, "reset", "--hard", orig_head, check=False)
                    sys.exit(1)

                if r.returncode != 0:
                    for f in conflicted_files(brain_dir):
                        base = os.path.basename(f)
                        if base == "PROFILE.md":
                            # Identity edits are never silently LWW'd, and
                            # precedence is consistent with item resolution:
                            # `--ours` (main / earlier-merged branch) wins
                            # (REVIEW.md #8/F6), always flagged for human
                            # review.
                            report["profile_conflicts"].append(f)
                            git(brain_dir, "checkout", "--ours", "--", f, check=False)
                            git(brain_dir, "add", f, check=False)
                        elif base == "INDEX.md":
                            git(brain_dir, "rm", "-f", f, check=False)
                        else:
                            resolve_conflicted_file(brain_dir, branch, f, report, today)

                if has_staged_changes(brain_dir) or merge_in_progress(brain_dir):
                    git(brain_dir, "commit", "--no-edit", "-m", f"brain(merge): {branch} -> main", check=False)
                report["merged"].append(branch)

            # Consolidation-lite: mechanical dedup flags only (semantics -> consolidate.md).
            report["near_dupes"] = find_dupes(brain_dir)

            # Secret-scrub gate — fail-closed. Any hit aborts; nothing bad enters main.
            hit = scan_brain_for_secrets(brain_dir)
            if hit:
                report["scrub"] = f"ABORT: {hit[0]}: {hit[1]} blocked"
                print_report(today, report)
                # Roll back to the pre-merge state — the tag if this was a real
                # run, the recorded HEAD either way (equivalent commit; the tag
                # may not exist yet in --dry-run mode, where no commit is meant
                # to persist regardless).
                git(brain_dir, "reset", "--hard", orig_head, check=False)
                sys.exit(1)

            # Deterministic INDEX rebuild.
            index_bytes, m, k, s = build_index_bytes(brain_dir)
            report["index_counts"] = (m, k, s)
            if not dry_run:
                with open(os.path.join(brain_dir, "INDEX.md"), "wb") as fh:
                    fh.write(index_bytes)
                git(brain_dir, "add", "INDEX.md", check=False)
                git(brain_dir, "commit", "-m", "brain(merge): rebuild INDEX.md", check=False)

                # Fast-forward each provider branch to the new main — but
                # only if it still points at the SHA we recorded pre-merge
                # (compare-and-swap; REVIEW.md #3/#5).
                for branch in PROVIDER_ORDER:
                    if branch not in pre_merge_shas:
                        continue
                    current = git(brain_dir, "rev-parse", branch, check=False).stdout.strip()
                    if current == pre_merge_shas[branch]:
                        git(brain_dir, "branch", "-f", branch, "main", check=False)
                        report["fast_forwarded"].append(branch)
                    else:
                        report["ff_skipped"].append(
                            f"{branch} advanced during merge; left for next run"
                        )
            else:
                # --dry-run commits nothing: undo every merge commit made while
                # planning the report, restoring main to exactly where it started.
                git(brain_dir, "reset", "--hard", orig_head, check=False)
        except Exception:
            # ANY exception anywhere in the merge/scrub/index-rebuild/
            # fast-forward section (including a git timeout, or the scrub
            # scan itself throwing on a vanished/permission-changed file)
            # must roll back exactly like a scrub HIT — never leave
            # partially-merged or un-scrubbed content on main just because
            # something threw (REVIEW.md #18/M5).
            git(brain_dir, "reset", "--hard", orig_head, check=False)
            raise

    print_report(today, report)


def print_report(today, r):
    print(f"=== brain_merge report {today} ===")
    print(f"Merged: {', '.join(r['merged']) if r['merged'] else 'none'} -> main")
    print(f"Conflicts renamed: {r['renamed'] if r['renamed'] else 'none'}")
    print(f"PROFILE conflicts (human review required): {r['profile_conflicts'] if r['profile_conflicts'] else 'none'}")
    if r.get("conflict_notes"):
        print("Conflict notes:")
        for note in r["conflict_notes"]:
            print(f"  - {note}")
    print(f"Near-dupes flagged: {r['near_dupes'] if r['near_dupes'] else 'none'}")
    print(f"Secret-scrub: {r['scrub']}")
    m, k, s = r["index_counts"]
    print(f"INDEX rebuilt: {m + k + s} items ({m} memories, {k} knowledge, {s} skills)")
    print(f"Fast-forwarded: {', '.join(r['fast_forwarded']) if r['fast_forwarded'] else 'none'}")
    if r.get("ff_skipped"):
        print(f"Fast-forward skipped (CAS): {'; '.join(r['ff_skipped'])}")
    print("===")


# --- CLI -----------------------------------------------------------------

def default_brain_dir():
    # hub/brain_merge.py -> repo root is the parent of hub/.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description="Loreport daily reconciliation: merge provider branches "
                     "into main and rebuild INDEX.md deterministically."
    )
    parser.add_argument("--brain-dir", default=None,
                         help="Brain repo root (default: inferred from this script's location)")
    parser.add_argument("--test-determinism", action="store_true",
                         help="Rebuild INDEX.md twice in memory and diff; exit 0 if identical")
    parser.add_argument("--dry-run", action="store_true",
                         help="Plan the merge and print the report; commit nothing")
    parser.add_argument("--test-scrub", action="store_true",
                         help="Self-test the secret-scrub detector in-memory; "
                              "exit 0 if an injected secret is caught, nonzero if missed")
    args = parser.parse_args()

    brain_dir = args.brain_dir or default_brain_dir()

    if args.test_scrub:
        sys.exit(test_scrub())

    if args.test_determinism:
        first, _, _, _ = build_index_bytes(brain_dir)
        second, _, _, _ = build_index_bytes(brain_dir)
        if first == second:
            print("PASS: INDEX is byte-deterministic")
            sys.exit(0)
        print("FAIL: INDEX rebuild is not byte-deterministic")
        sys.exit(1)

    do_merge(brain_dir, args.dry_run)


if __name__ == "__main__":
    main()
