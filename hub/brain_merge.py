#!/usr/bin/env python3
"""
hub/brain_merge.py — Loreport daily reconciliation (design.md §D14, modules.md M13b).

Single-file, Python-3-stdlib-only. Performs the hub's daily branch merge:

  1. Backup   — tag `main` (`pre-merge/<date>-<HHMMSS>[-N]`, unique per run, never
                force-moved), the rollback point.
  2. Fetch    — pull all provider branches.
  3. Merge    — into `main`, fixed order: provider/openclaw -> provider/claude ->
                provider/codex -> provider/chatgpt. INDEX.md is excluded from every merge (deleted from
                the working tree before each merge, regenerated in step 6 — it is a
                derived artifact and must never be hand-merged).
  3b. Provenance gate — for every provider-branch commit not yet on main whose
      commit message lacks a trusted `Trust: local` trailer (i.e. `Trust: cloud`,
      or no `Trust:` trailer at all — fail closed, catching a direct `git push`
      that bypassed `inbox_ingest.py`), any touched `memories/`/`knowledge/` path
      that main already owns as `visibility: local` or under a different
      `source:` is reverted to its pre-merge content in a follow-up commit and
      recorded as a violation. One bad path never aborts the whole merge — every
      other change still lands.
  4. Consolidation-lite — mechanical exact/near-duplicate-key flagging (fuzzy
     semantic dedup is left to `prompts/consolidate.md`, run by a human-in-the-loop).
  5. Secret-scrub gate — fail-closed for anything egress-critical: a hit in a SHARED
     item, PROFILE.md, or a skill package aborts the merge commit + resets to the
     backup tag; nothing bad enters `main`. A hit in a LOCAL-visibility item (never
     published, never read by a cloud-trust caller) is recorded as a WARNING in the
     report/digest instead — it does not abort, since local items never reach a
     cloud provider (the never-capture rule + the private backup are the controls
     there).
  6. INDEX rebuild — deterministic: same input item set -> same INDEX.md bytes, always.
  7. Fast-forward each provider branch to the new `main`.

Exit code is nonzero whenever a human should look: PROFILE conflicts, add/add
renames, secret-scrub warnings, or provenance-gate violations.

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

# Sibling import: this module is imported by tooling that does not put hub/ on the
# path (scripts/check-docs.sh does exactly that), so make the directory importable
# rather than relying on being run as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synth_detect  # noqa: E402

# Where the merge parks the detector's report for the weekly health check to read.
# Gitignored alongside the other hub state files: it is a report artifact, and a
# tracked file rewritten nightly would leave the tree dirty for sync.sh's guard.
SYNTHESIS_REPORT_FILE = "hub/synthesis-report.json"

# --- constants -------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))

_FALLBACK_PROVIDER_ORDER = [
    "provider/openclaw", "provider/claude", "provider/codex", "provider/chatgpt"
]


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
ITEM_TYPES = {"user", "feedback", "project", "reference", "knowledge", "person", "decision"}

# Lifecycle (docs/taxonomy-lifecycle-design.md Phase 2). Both fields are OPTIONAL;
# an absent `lifespan` means `permanent`.
LIFESPANS = {"permanent", "active", "temporary"}
# The work-vs-private axis. Orthogonal to `visibility`, which is cloud EXPOSURE —
# a `domain: work` item may be local, and a `domain: personal` item may be shared.
DOMAINS = {"work", "personal", "both"}

EXPIRES_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HUMAN_REGION_RE = re.compile(
    r"<!--\s*human:start\s*-->(.*?)<!--\s*human:end\s*-->",
    re.DOTALL,
)

# Same secret-regex set used by inbox_ingest.py and snapshot_publish.py (duplicated
# on purpose — every hub/*.py file is single-file and stdlib-only, so nothing is
# imported between them).
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


# --- lifecycle ---------------------------------------------------------------

def parse_expires(value):
    """Return a `date` for a well-formed `expires: YYYY-MM-DD`, else None.

    Anything unparseable returns None, which means "not expired" — a garbled
    date must never cause an item to silently vanish from the hot index. The
    strict-format complaint belongs at capture time (inbox_ingest.validate_schema),
    where the author can still fix it; by the time an item is on disk, the safe
    reading of a broken date is to leave the item alone.
    """
    if not value or not EXPIRES_RE.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def is_expired(fm, today):
    """True when this item's INDEX line belongs on the cold shelf.

    The trigger is MECHANICAL and is the `expires` date alone — a before/after
    comparison, never duration math and never a model's judgement of "stale"
    (docs/taxonomy-lifecycle-design.md Phase 2). An item with no `expires` is
    never archived by this code, so `permanent` and `active` items are untouched
    by construction rather than by a separate rule that could drift.
    """
    expires = parse_expires(fm.get("expires"))
    return expires is not None and expires < today


# --- deterministic INDEX rebuild --------------------------------------------

def _collect_memories(brain_dir):
    """Return [(name, desc, typ, frontmatter)] for every valid memory item."""
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
            memories.append((name, desc, typ, fm))
    return memories


def build_archive_index_bytes(brain_dir, today=None):
    """Build INDEX-ARCHIVE.md bytes — the cold shelf.

    Only the catalog LINE moves here; the item's file stays exactly where it is
    on disk, so an archived item is still readable, still wikilink-resolvable,
    and still merged/scrubbed like any other. Archiving is a hot/cold split of
    the index, not a deletion.

    Returns (bytes, archived_count). Deterministic for a given day and item set.
    """
    today = today or date.today()
    archived = [
        (name, desc, typ)
        for name, desc, typ, fm in _collect_memories(brain_dir)
        if is_expired(fm, today)
    ]
    archived.sort(key=lambda t: t[0])
    lines = [
        "# Index — Archive",
        "",
        # No wikilink-shaped example text in this header: every downstream
        # consumer (snapshot_publish's visibility filter, doctor.sh's link pass)
        # treats a `[[...]]` anywhere on a line as an item reference, so a
        # decorative one here would be scanned as if it were a real entry.
        "Items whose `expires` date has passed. Their files are untouched and still",
        "resolve by wikilink; only their catalog line left the hot `INDEX.md`.",
        "",
        "## Memories",
    ]
    for name, desc, typ in archived:
        lines.append(f"- [[{name}]] — {desc}  ({typ})")
    content = "\n".join(lines) + "\n"
    return content.encode("utf-8"), len(archived)


def build_index_bytes(brain_dir, today=None):
    """Scan memories/, knowledge/, skills/ and build INDEX.md bytes.
    Deterministic: sorted alphabetically within each section; no reliance on
    filesystem iteration order, mtimes, or any other non-content input.

    Expired items (`expires` in the past) are omitted here and listed in
    INDEX-ARCHIVE.md instead — see build_archive_index_bytes."""
    today = today or date.today()
    memories = [
        (name, desc, typ)
        for name, desc, typ, fm in _collect_memories(brain_dir)
        if not is_expired(fm, today)
    ]

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


def is_ancestor(brain_dir, maybe_ancestor, descendant):
    """True when `maybe_ancestor` is already reachable from `descendant`."""
    r = git(brain_dir, "merge-base", "--is-ancestor", maybe_ancestor, descendant, check=False)
    return r.returncode == 0


def indexes_are_current(brain_dir):
    """True when INDEX.md and INDEX-ARCHIVE.md on disk already equal a rebuild.

    Deliberately compares the WORKING-TREE bytes, not a git object: a run that
    was interrupted after writing the index but before committing it must still
    count as "needs work", not as "already current".
    """
    index_bytes, _, _, _ = build_index_bytes(brain_dir)
    archive_bytes, archived_n = build_archive_index_bytes(brain_dir)

    index_path = os.path.join(brain_dir, "INDEX.md")
    if not os.path.isfile(index_path):
        return False
    with open(index_path, "rb") as fh:
        if fh.read() != index_bytes:
            return False

    archive_path = os.path.join(brain_dir, "INDEX-ARCHIVE.md")
    if archived_n == 0:
        return not os.path.isfile(archive_path)
    if not os.path.isfile(archive_path):
        return False
    with open(archive_path, "rb") as fh:
        return fh.read() == archive_bytes


# --- consolidation-lite: mechanical dedup flags -----------------------------

def extract_human_regions(text):
    """Return human-region bodies in document order (pairwise start/end markers)."""
    return [m.group(1) for m in HUMAN_REGION_RE.finditer(text)]


def human_region_violation(main_text, incoming_text):
    """Return a reason string when `incoming_text` drops or alters any human
    region present in `main_text`, else None. Region bodies are compared as
    multisets so a reorder with verbatim content passes; a file with no human
    regions on main is never a violation."""
    main_regions = extract_human_regions(main_text)
    if not main_regions:
        return None
    incoming_regions = extract_human_regions(incoming_text)
    if len(incoming_regions) < len(main_regions):
        return "dropped human region(s)"
    main_counts = {}
    for region in main_regions:
        main_counts[region] = main_counts.get(region, 0) + 1
    incoming_counts = {}
    for region in incoming_regions:
        incoming_counts[region] = incoming_counts.get(region, 0) + 1
    for region, count in main_counts.items():
        if incoming_counts.get(region, 0) < count:
            if len(incoming_regions) >= len(main_regions):
                return "altered human region"
            return "dropped human region(s)"
    return None


def quarantine_merge_update(brain_dir, provider, rel_path, reason, detail, incoming_text):
    """Park a rejected merge update under hub/quarantine/ and log to digest.md."""
    qdir = os.path.join(brain_dir, "hub", "quarantine", provider)
    os.makedirs(qdir, exist_ok=True)
    today = date.today().isoformat()
    safe_name = rel_path.replace(os.sep, "__")
    dest = os.path.join(qdir, f"{today}-{safe_name}")
    n = 1
    root_dest = dest
    while os.path.exists(dest):
        n += 1
        dest = f"{root_dest}.{n}"
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(incoming_text)

    digest_path = os.path.join(brain_dir, "hub", "quarantine", "digest.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    is_new = not os.path.isfile(digest_path)
    ts = datetime.now().isoformat(timespec="seconds")
    with open(digest_path, "a", encoding="utf-8") as fh:
        if is_new:
            fh.write("# Quarantine digest\n\n"
                     "Rejected captures and merge updates land here — nothing is "
                     "silently dropped.\n\n")
        fh.write(f"## {ts} — QUARANTINE ({provider})\n")
        fh.write(f"- file: {os.path.relpath(dest, brain_dir)}\n")
        fh.write(f"- reason: {reason}\n")
        fh.write(f"- detail: {detail}\n\n")


def files_changed_between(brain_dir, old_ref, new_ref):
    """Return memory/knowledge/PROFILE paths that differ between two git refs.

    Human regions are an item-body convention, but PROFILE.md carries one too
    (design-wiki-parity §1: the Boundaries section), and it is projected into every
    provider surface -- so it is the single highest-value file to protect.
    ITEM_PROVENANCE_DIRS is read at call time: it is defined further down the file.
    """
    scope = ITEM_PROVENANCE_DIRS + ("PROFILE.md",)
    r = git(brain_dir, "diff", "--name-only", old_ref, new_ref, check=False)
    changed = []
    for path in r.stdout.splitlines():
        if path.startswith(scope):
            changed.append(path)
    return changed


def apply_human_region_guard(brain_dir, head_before, branch, report):
    """After one provider branch lands on main, reject any update to an existing
    item that drops or alters a human region present before the merge."""
    head_after = git(brain_dir, "rev-parse", "HEAD").stdout.strip()
    if head_before == head_after:
        return
    provider = _branch_provider_name(branch)
    violations = []
    for path in files_changed_between(brain_dir, head_before, head_after):
        prior = _read_ref_path(brain_dir, head_before, path)
        if prior is None:
            continue  # new file — human regions pass through untouched
        # A merge that DELETES the file leaves nothing on disk. Treat it as an
        # empty body: every human region is "dropped", so the guard restores the
        # pre-merge copy. Reading it unguarded would raise FileNotFoundError and
        # abort the whole nightly merge -- the one thing this must never do.
        abs_path = os.path.join(brain_dir, path)
        current = read_file(abs_path) if os.path.isfile(abs_path) else ""
        reason = human_region_violation(prior, current)
        if not reason:
            continue
        detail = f"{path}: {reason} from {branch} merge (kept pre-merge copy)"
        quarantine_merge_update(
            brain_dir, provider, path, "human-region-guard", detail, current
        )
        git(brain_dir, "checkout", head_before, "--", path, check=False)
        git(brain_dir, "add", path, check=False)
        violations.append(detail)
    if violations:
        report["human_region_violations"].extend(violations)
        if has_staged_changes(brain_dir):
            msg_lines = ["brain(merge): revert human-region-guard violations", ""]
            msg_lines.extend(f"- {detail}" for detail in violations)
            git(brain_dir, "commit", "-m", "\n".join(msg_lines), check=False)


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
    """Scan the merged-but-not-yet-committed tree for secret-shaped hits, split
    into two buckets (REVIEW.md P3 #17/egress-scope fix):

      - fail_closed: a hit in a SHARED item (an explicit `visibility: shared`),
        in an item carrying NO explicit `visibility:` at all, in `skills/` (no
        visibility frontmatter exists for skills, so they're always
        egress-critical), or in PROFILE.md -> cloud egress must stay strictly
        gated. The FIRST such hit found is returned (same "abort on first hit"
        contract as before).
      - warnings: a hit in an explicitly LOCAL item -> never aborts the merge.
        `local` items never reach a cloud provider (never-capture rule +
        the private backup are the real controls there; publish/read/search
        already refuse `local` items to cloud callers), so a secret-shaped
        false positive in local infra prose ("token: stored in the keyring")
        must not block SHARED-item propagation to every provider. ALL such
        hits are collected, not just the first.

    NOTE the deliberate asymmetry with `_visibility_from_text`. That parser
    now classifies an UNMARKED item as `local`, so routing this split through
    it alone would quietly move every unmarked secret hit out of fail_closed
    and into warnings — turning "abort the merge" into "a line in a digest".
    The warnings bucket is only defensible because a `local` item is KNOWN to
    be withheld from every egress path; an unmarked item is not known to be
    anything, because no human has classified it yet. So the demotion requires
    an EXPLICIT `visibility: local`, which is what `_has_explicit_visibility`
    below is for. Withholding an item from publish on a default is safe;
    relaxing a secret gate on the same default is not.

    Every egress-critical file is scanned regardless of what's found in
    local-visibility files first (and vice versa) — a local-hit warning must
    never short-circuit past a later shared-item hit that has to fail closed.

    Returns (fail_closed_hit_or_None, warnings_list) where fail_closed_hit is
    (relpath, masked) and warnings_list is a list of (relpath, masked) tuples.
    """
    fail_closed = None
    warnings = []

    for sub in ("memories", "knowledge"):
        d = os.path.join(brain_dir, sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(d, fname)
            text = read_file(path)
            hit = scan_text_for_secrets(text)
            if not hit:
                continue
            rel = os.path.join(sub, fname)
            explicitly_local = (
                _has_explicit_visibility(text)
                and _visibility_from_text(text) == "local"
            )
            if explicitly_local:
                warnings.append((rel, mask(hit)))
            elif fail_closed is None:
                fail_closed = (rel, mask(hit))

    # skills/ packages carry no `visibility:` frontmatter at all (§6 of
    # format-spec.md) -- always treated as shared/egress-critical.
    skills_dir = os.path.join(brain_dir, "skills")
    if os.path.isdir(skills_dir):
        for root, _dirs, files in os.walk(skills_dir):
            for fname in sorted(files):
                path = os.path.join(root, fname)
                hit = scan_text_for_secrets(read_file(path))
                if hit and fail_closed is None:
                    fail_closed = (os.path.relpath(path, brain_dir), mask(hit))

    # PROFILE.md is always egress-critical, regardless of any visibility-like
    # field a user might add to it -- it's the identity file pinned into
    # every provider's context by snapshot_publish.py.
    profile_path = os.path.join(brain_dir, "PROFILE.md")
    if os.path.isfile(profile_path):
        hit = scan_text_for_secrets(read_file(profile_path))
        if hit and fail_closed is None:
            fail_closed = ("PROFILE.md", mask(hit))

    return fail_closed, warnings


def count_quarantine_items(brain_dir):
    """Count quarantined blocks under hub/quarantine/ (excluding the digest.md
    log itself) for the daily digest's summary line."""
    qdir = os.path.join(brain_dir, "hub", "quarantine")
    count = 0
    if os.path.isdir(qdir):
        for _root, _dirs, files in os.walk(qdir):
            count += sum(1 for f in files if f != "digest.md")
    return count


def run_synthesis_report(brain_dir, dry_run):
    """Run the cluster detector and park its report for the health check.

    REPORT-ONLY by construction: `synth_detect` only reads, this function only
    writes `hub/synthesis-report.json` (a gitignored report artifact, never brain
    content), and the merge does nothing with the result but print it. Filing a
    proposal as a `knowledge/` page is the post-calibration step and is not
    implemented anywhere yet — see design-wiki-parity §2.

    Never raises: a detector that crashes must not take the nightly merge with it.
    """
    try:
        result = synth_detect.detect_clusters(brain_dir)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "clusters": [], "warnings": []}

    if not dry_run:
        payload = dict(result)
        payload["generated"] = datetime.now().isoformat(timespec="seconds")
        try:
            with open(os.path.join(brain_dir, SYNTHESIS_REPORT_FILE), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
        except OSError as e:
            result = dict(result)
            result["error"] = f"could not write {SYNTHESIS_REPORT_FILE}: {e}"
    return result


def format_synthesis_lines(synthesis):
    """Digest lines for the synthesis section. Says REPORT-ONLY on every run so a
    reader never mistakes a proposal for something that was filed."""
    if not synthesis:
        return ["- Synthesis proposals (REPORT-ONLY, none filed): detector did not run"]
    if synthesis.get("error"):
        return [f"- Synthesis proposals (REPORT-ONLY, none filed): detector error — {synthesis['error']}"]

    clusters = synthesis.get("clusters") or []
    warnings = synthesis.get("warnings") or []
    lines = [
        f"- Synthesis proposals (REPORT-ONLY, none filed): {len(clusters)} "
        f"from {synthesis.get('item_count', 0)} items"
    ]
    for cluster in clusters:
        lines.append(
            f"    - {cluster['topic']} [{cluster['signal']}] "
            f"members: {', '.join(cluster['members'])}"
        )
    if warnings:
        lines.append(f"- Detector health: {len(warnings)} warning(s)")
        for warning in warnings:
            lines.append(
                f"    - {warning['reason']}: topic={warning['topic']} "
                f"({len(warning['members'])} members, {warning['signal']})"
            )
    return lines


def write_digest(brain_dir, today, report):
    """Write hub/digest-<date>.md from the merge report (REVIEW.md #15) --
    every cycle's outcome as a real file, not just stdout prose nobody reads.
    hub/digest-*.md is gitignored (like quarantine/logs) since it's a local
    report artifact, not brain content, and must survive an abort's
    `git reset --hard` untouched."""
    lines = [f"# Hub digest — {today}", ""]
    lines.append(f"- Backup tag: {report.get('backup_tag') or 'none'}")
    lines.append(f"- Merged: {', '.join(report['merged']) if report['merged'] else 'none'} -> main")
    lines.append(f"- Renamed (add/add conflicts): {report['renamed'] if report['renamed'] else 'none'}")
    lines.append(
        f"- PROFILE conflicts (human review required): "
        f"{report['profile_conflicts'] if report['profile_conflicts'] else 'none'}"
    )
    provenance_violations = report.get("provenance_violations") or []
    lines.append(
        f"- Provenance violations (untrusted commit touched a path it doesn't own, reverted): "
        f"{provenance_violations if provenance_violations else 'none'}"
    )
    lines.append(f"- Near-dupes flagged: {report['near_dupes'] if report['near_dupes'] else 'none'}")
    lines.append(f"- Secret-scrub: {report['scrub']}")
    scrub_warnings = report.get("scrub_warnings") or []
    lines.append(
        f"- Scrub warnings (local-visibility hits; merge continued): "
        f"{scrub_warnings if scrub_warnings else 'none'}"
    )
    human_violations = report.get("human_region_violations") or []
    lines.append(
        f"- Human-region violations (incoming update quarantined, main kept): "
        f"{human_violations if human_violations else 'none'}"
    )
    lines.extend(format_synthesis_lines(report.get("synthesis")))
    lines.append(f"- Quarantine items pending review: {count_quarantine_items(brain_dir)}")
    m, k, s = report["index_counts"]
    lines.append(f"- INDEX rebuilt: {m + k + s} items ({m} memories, {k} knowledge, {s} skills)")
    lines.append(f"- Fast-forwarded: {', '.join(report['fast_forwarded']) if report['fast_forwarded'] else 'none'}")
    if report.get("ff_skipped"):
        lines.append(f"- Fast-forward skipped (CAS): {'; '.join(report['ff_skipped'])}")
    digest_path = os.path.join(brain_dir, "hub", f"digest-{today}.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    with open(digest_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return digest_path


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


def resolve_conflicted_file(brain_dir, branch, f, report, tag_name):
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
            f"from {branch} (recover at {tag_name})"
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
            f"modify/delete on {f}: {branch} deleted, ours modified -- deletion wins "
            f"(recover at {tag_name})"
        )
        git(brain_dir, "rm", "-f", f, check=False)
    elif has_base and has_theirs and not has_ours:
        report["conflict_notes"].append(
            f"modify/delete on {f}: main deleted, {branch} modified -- deletion wins "
            f"(recover at {tag_name})"
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


# --- provenance gate (merge-side) -------------------------------------------
#
# Fixes: a cloud-credentialed provider branch could previously write (or
# delete) ANY path by name -- including a `visibility: local` item it never
# captured, or a `source:`-owned item belonging to a different provider -- and
# an ordinary clean (non-conflicting) merge would take it with no check at
# all. Scope: only `memories/` and `knowledge/` paths carry `source:` /
# `visibility:` frontmatter per format-spec.md §1, so only those two
# directories are gated here. `PROFILE.md` and `INDEX.md` are deliberately
# excluded -- they carry no `source:`/`visibility:` concept, and
# `_visibility_from_text` fails closed to "local" for text with no
# frontmatter at all, which would misclassify them if fed through it (see
# that function's docstring/caveat). `skills/` packages carry no item
# frontmatter either (§6), so there is no `source:`/`visibility:` to check
# there yet -- out of scope for this gate, same as it already is for the
# secret-scrub's visibility split.

ITEM_PROVENANCE_DIRS = ("memories/", "knowledge/")

# Matches the "Trust: local" / "Trust: cloud" trailer inbox_ingest.py's
# commit_block() writes into every capture commit message, same line-style as
# the existing "Provider:" / "Action:" trailers.
TRUST_TRAILER_RE = re.compile(r"(?m)^Trust:\s*(.*?)\s*$")


def _branch_provider_name(branch):
    """`provider/chatgpt` -> `chatgpt`. Falls back to the whole string for an
    unexpected branch name rather than raising."""
    return branch.split("/", 1)[1] if "/" in branch else branch


def _commit_trust(message):
    """Return the lowercased `Trust:` trailer value from a commit message, or
    None if the trailer is absent or unparseable. Deliberately does NOT
    default to a trust level -- the caller treats None as untrusted (fail
    closed), which is what catches a commit that bypassed
    inbox_ingest.py's commit_block() entirely (e.g. a direct `git push`)."""
    m = TRUST_TRAILER_RE.search(message)
    if not m:
        return None
    return m.group(1).strip().lower()


def _read_ref_path(brain_dir, ref, path):
    """Return the text content of `path` at git ref `ref`, or None if the
    path does not exist there. Any other git failure (timeout, corrupt repo)
    propagates as an exception via `git()`, same fail-closed contract as the
    rest of this file's plumbing -- a provenance check that can't actually
    read the prior state must never be silently treated as "no prior state"."""
    r = git(brain_dir, "show", f"{ref}:{path}", check=False)
    if r.returncode != 0:
        return None
    return r.stdout


def collect_provenance_violations(brain_dir, orig_head, pre_merge_shas):
    """Enumerate every commit on each provider branch not yet on `main` (as of
    `orig_head`, i.e. before this run's merges) and flag any UNTRUSTED
    commit's touched `memories/`/`knowledge/` path that main already owns
    under a different provider or as `visibility: local`.

    UNTRUSTED = the commit's `Trust:` trailer says `cloud`, or is missing
    entirely (fail closed).

    A touched path is a VIOLATION when the `orig_head` version of that path
    has `visibility: local` (via `_visibility_from_text`), OR has a
    `source:` that is not this branch's own provider name (including a
    missing `source:` field, fail closed). A path that did not exist at
    `orig_head` is always allowed -- new-item creation is never a violation.

    Returns a list of (path, branch, sha, reason) tuples, one per violating
    path (first offending commit wins per path -- remediation is the same
    "restore to orig_head" regardless of how many untrusted commits touched
    it).
    """
    violations = {}
    for branch in PROVIDER_ORDER:
        if branch not in pre_merge_shas:
            continue
        provider = _branch_provider_name(branch)
        log = git(brain_dir, "log", "--format=%H", f"{orig_head}..{pre_merge_shas[branch]}", check=False)
        shas = [line for line in log.stdout.splitlines() if line]
        for sha in shas:
            msg = git(brain_dir, "log", "-1", "--format=%B", sha, check=False).stdout
            trust = _commit_trust(msg)
            if trust == "local":
                continue  # trusted capture -- no provenance check needed

            paths_r = git(brain_dir, "show", "--name-only", "--format=", sha, check=False)
            for path in paths_r.stdout.splitlines():
                if not path or not path.startswith(ITEM_PROVENANCE_DIRS):
                    continue
                if path in violations:
                    continue  # already flagged via an earlier untrusted commit
                prior = _read_ref_path(brain_dir, orig_head, path)
                if prior is None:
                    continue  # new path at orig_head -- always allowed

                visibility = _visibility_from_text(prior)
                fm, _ = parse_frontmatter(prior)
                source = fm.get("source") if fm else None

                if visibility == "local":
                    reason = (
                        f"{path}: visibility:local item touched by untrusted commit "
                        f"{sha[:8]} on {branch} (Trust={trust or 'MISSING'})"
                    )
                elif source != provider:
                    reason = (
                        f"{path}: source:{source or 'MISSING'} item touched by untrusted "
                        f"commit {sha[:8]} on {branch} claiming provider={provider} "
                        f"(Trust={trust or 'MISSING'})"
                    )
                else:
                    continue  # untrusted commit, but it's this path's own provider
                              # touching its own shared item -- not a violation

                violations[path] = (branch, sha, reason)
    return [(path, branch, sha, reason) for path, (branch, sha, reason) in violations.items()]


def apply_provenance_restore(brain_dir, orig_head, violations, report):
    """Revert every violating path (from `collect_provenance_violations`) to
    its `orig_head` content and make one follow-up commit describing what was
    reverted. Never aborts the merge -- rejects only the individual
    offending paths, everything else that already landed stays landed."""
    if not violations:
        return
    for path, _branch, _sha, reason in violations:
        prior = _read_ref_path(brain_dir, orig_head, path)
        if prior is None:
            # Defensive only -- collect_provenance_violations() never flags a
            # path that didn't already exist at orig_head.
            git(brain_dir, "rm", "-f", "--quiet", path, check=False)
        else:
            git(brain_dir, "checkout", orig_head, "--", path, check=False)
            git(brain_dir, "add", path, check=False)
        report["provenance_violations"].append(reason)

    if has_staged_changes(brain_dir):
        msg_lines = ["brain(merge): revert provenance-gate violations", ""]
        msg_lines.extend(f"- {reason}" for _p, _b, _s, reason in violations)
        git(brain_dir, "commit", "-m", "\n".join(msg_lines), check=False)


# --- the merge ----------------------------------------------------------------

def do_merge(brain_dir, dry_run):
    today = date.today().isoformat()
    report = {
        "merged": [],
        "renamed": [],
        "profile_conflicts": [],
        "near_dupes": [],
        "scrub": "PASS",
        "scrub_warnings": [],
        "index_counts": (0, 0, 0),
        "fast_forwarded": [],
        "ff_skipped": [],
        "conflict_notes": [],
        "provenance_violations": [],
        "human_region_violations": [],
        "backup_tag": None,
    }

    # The whole mutating sequence — including the --dry-run branch, which
    # still mutates before resetting — runs under one exclusive repo lock so
    # a capture (inbox_ingest.py) can never interleave with a merge
    # (REVIEW.md #5).
    with brain_lock(brain_dir):
        git(brain_dir, "checkout", "main")
        orig_head = git(brain_dir, "rev-parse", "HEAD").stdout.strip()

        # Unique-per-run, never force-moved (bug fix: the old `-f` tag was
        # silently re-pointed on every run, so "recover at pre-merge/<date>"
        # stopped being true after a second run the same day). Sortable by
        # construction (date then time); on the vanishingly unlikely chance
        # two runs start in the same second, `git tag` (no `-f`) refuses the
        # collision and the loop below picks the next free `-N` suffix rather
        # than silently overwriting the earlier run's backup.
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

        # NO-OP DETECTION. This runs daily from a timer, and on most days nobody
        # captured anything. Without this check every one of those days still
        # produced two commits — "drop INDEX.md" and "rebuild INDEX.md" — plus a
        # backup tag, for byte-identical content. That is not just untidy: it
        # buries the days something REAL happened under a wall of noise, so the
        # log stops being scannable exactly when you need to scan it, and it
        # makes `git log INDEX.md` useless for answering "when did the catalog
        # actually change?".
        #
        # A run is a no-op only when BOTH are true: nothing is left to merge
        # (every provider branch is already an ancestor of main), and the
        # committed indexes already equal what a rebuild would produce. The
        # second half matters — an index can be stale from an interrupted run
        # even when no branch has moved, and skipping the rebuild then would
        # leave it wrong forever.
        nothing_to_merge = all(is_ancestor(brain_dir, sha, orig_head)
                               for sha in pre_merge_shas.values())
        noop = not dry_run and nothing_to_merge and indexes_are_current(brain_dir)
        report["noop"] = noop

        tag_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        tag_name = f"pre-merge/{tag_stamp}"
        if not dry_run and not noop:
            attempt = 0
            while True:
                candidate = tag_name if attempt == 0 else f"{tag_name}-{attempt}"
                tag_res = git(brain_dir, "tag", candidate, check=False)
                if tag_res.returncode == 0:
                    tag_name = candidate
                    break
                attempt += 1
                if attempt > 1000:
                    # Not a same-second collision -- something is actually
                    # wrong (permissions, disk, corrupt refs). Fail loudly
                    # rather than spin forever or silently skip the backup.
                    raise RuntimeError(
                        f"could not create backup tag {tag_name!r} after "
                        f"{attempt} attempts: {tag_res.stderr.strip()}"
                    )
            report["backup_tag"] = tag_name

        # Provenance gate (bug fix): figure out, from each provider branch's
        # not-yet-on-main commits and their Trust: trailers, which
        # memories/knowledge paths an untrusted commit is not allowed to have
        # touched. Computed here (read-only, before any merging) against the
        # fixed `orig_head`/`pre_merge_shas` baseline so it's unaffected by
        # whatever order the branches get merged in below.
        provenance_violations = collect_provenance_violations(brain_dir, orig_head, pre_merge_shas)

        try:
            # INDEX.md is never a merge input: remove it once, up front, as
            # its own committed change. (A bare, uncommitted `git rm` here
            # would make every subsequent `git merge` fail outright — git
            # refuses to merge over uncommitted local changes — so the
            # removal must land in its own commit before any branch merge is
            # attempted.) It is regenerated wholesale in step 6, after every
            # provider branch has been merged in.
            # INDEX-ARCHIVE.md is the same kind of artifact and gets the same
            # treatment — it is rebuilt from the items' `expires` dates, so a
            # hand-merged version of it could only ever be wrong.
            # Only when a merge is actually going to happen. Dropping the
            # indexes exists to keep them out of merge resolution; with nothing
            # to merge it would spend a delete commit and a restore commit to
            # arrive back at the same bytes — the churn this guard removes.
            derived = [p for p in ("INDEX.md", "INDEX-ARCHIVE.md")
                       if os.path.exists(os.path.join(brain_dir, p))]
            if derived and not dry_run and not nothing_to_merge:
                git(brain_dir, "rm", "-f", "--quiet", *derived, check=False)
                if has_staged_changes(brain_dir):
                    git(brain_dir, "commit", "-m", "brain(merge): drop INDEX.md (derived artifact)", check=False)

            for branch in PROVIDER_ORDER:
                if noop or not branch_exists(brain_dir, branch):
                    continue

                head_before_branch = git(brain_dir, "rev-parse", "HEAD").stdout.strip()

                r = git(brain_dir, "merge", "--no-commit", "--no-ff", branch, check=False)

                if r.returncode != 0 and not merge_in_progress(brain_dir):
                    # The merge never actually started (e.g. dirty working
                    # tree) — this is an operator error, not a content
                    # conflict. Abort loudly rather than silently skip the
                    # branch's contributions.
                    report["scrub"] = f"ABORT: merge of {branch} failed to start: {r.stderr.strip()}"
                    print_report(today, report)
                    if not dry_run:
                        write_digest(brain_dir, today, report)
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
                        elif base in ("INDEX.md", "INDEX-ARCHIVE.md"):
                            git(brain_dir, "rm", "-f", f, check=False)
                        else:
                            resolve_conflicted_file(brain_dir, branch, f, report, tag_name)

                if has_staged_changes(brain_dir) or merge_in_progress(brain_dir):
                    git(brain_dir, "commit", "--no-edit", "-m", f"brain(merge): {branch} -> main", check=False)
                apply_human_region_guard(brain_dir, head_before_branch, branch, report)
                report["merged"].append(branch)

            # Provenance gate: revert any path an untrusted commit touched
            # that main didn't already own (visibility:local, or a different
            # provider's source:). Rejects only the offending paths -- every
            # other change from this run stays merged.
            apply_provenance_restore(brain_dir, orig_head, provenance_violations, report)

            # Consolidation-lite: mechanical dedup flags only (semantics -> consolidate.md).
            report["near_dupes"] = find_dupes(brain_dir)

            # Secret-scrub gate — SHARED/PROFILE/skills hits fail closed (abort);
            # LOCAL-visibility hits warn and the merge continues (see
            # scan_brain_for_secrets' docstring for the rationale).
            fail_closed_hit, scrub_warnings = scan_brain_for_secrets(brain_dir)
            report["scrub_warnings"] = [f"{rel}: {masked}" for rel, masked in scrub_warnings]
            if fail_closed_hit:
                report["scrub"] = f"ABORT: {fail_closed_hit[0]}: {fail_closed_hit[1]} blocked"
                print_report(today, report)
                if not dry_run:
                    write_digest(brain_dir, today, report)
                # Roll back to the pre-merge state — the tag if this was a real
                # run, the recorded HEAD either way (equivalent commit; the tag
                # may not exist yet in --dry-run mode, where no commit is meant
                # to persist regardless).
                git(brain_dir, "reset", "--hard", orig_head, check=False)
                sys.exit(1)
            elif report["scrub_warnings"]:
                report["scrub"] = "PASS (local-visibility warnings — see scrub_warnings)"

            # Synthesis detection (design-wiki-parity §2) — REPORT-ONLY during the
            # 2-3 week calibration window. The detector is a pure read: it proposes
            # topics into the digest and files nothing, and nothing downstream of
            # here may create a knowledge/ page from its output. It also must never
            # be able to fail a merge, so a detector bug degrades to a digest note.
            report["synthesis"] = run_synthesis_report(brain_dir, dry_run)

            # Deterministic INDEX rebuild.
            index_bytes, m, k, s = build_index_bytes(brain_dir)
            archive_bytes, archived_n = build_archive_index_bytes(brain_dir)
            report["index_counts"] = (m, k, s)
            report["archived_count"] = archived_n
            if not dry_run and not noop:
                with open(os.path.join(brain_dir, "INDEX.md"), "wb") as fh:
                    fh.write(index_bytes)
                git(brain_dir, "add", "INDEX.md", check=False)

                # The cold shelf only exists once something is actually on it.
                # A brain that has never expired an item gets no empty
                # INDEX-ARCHIVE.md, and one whose last archived item was
                # revived loses the file again rather than keeping a
                # misleading empty catalog around.
                archive_path = os.path.join(brain_dir, "INDEX-ARCHIVE.md")
                if archived_n:
                    with open(archive_path, "wb") as fh:
                        fh.write(archive_bytes)
                    git(brain_dir, "add", "INDEX-ARCHIVE.md", check=False)
                elif os.path.isfile(archive_path):
                    git(brain_dir, "rm", "-f", "--quiet", "INDEX-ARCHIVE.md", check=False)

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

    # --dry-run stays a pure no-op: it plans + prints the report but writes no
    # digest file and always exits 0, exactly like before this phase. Only a
    # real run's digest/exit-code reflects what actually landed on `main`.
    if dry_run:
        return report

    write_digest(brain_dir, today, report)

    # Nonzero exit whenever something needs a human's attention (REVIEW.md
    # #15): PROFILE conflicts, add/add renames, scrub warnings, or
    # provenance-gate violations. This runs AFTER the lock is released and
    # outside the try/except above -- it's a reporting decision on an
    # already-successful merge, never confused with the fail-closed abort
    # path (which exits 1 from inside the lock, above).
    if (
        report["profile_conflicts"]
        or report["renamed"]
        or report["scrub_warnings"]
        or report["provenance_violations"]
        or report["human_region_violations"]
    ):
        sys.exit(1)

    return report


def print_report(today, r):
    print(f"=== brain_merge report {today} ===")
    if r.get("noop"):
        # Say so explicitly. A silent "nothing happened" run is indistinguishable
        # from a broken one, and this path is the common case on a quiet day.
        print("No-op: nothing to merge and the indexes are already current — "
              "no tag, no commits.")
        print("Backup tag: none (no-op — nothing to back up)")
    else:
        print(f"Backup tag: {r.get('backup_tag') or 'none (--dry-run)'}")
    print(f"Merged: {', '.join(r['merged']) if r['merged'] else 'none'} -> main")
    print(f"Conflicts renamed: {r['renamed'] if r['renamed'] else 'none'}")
    print(f"PROFILE conflicts (human review required): {r['profile_conflicts'] if r['profile_conflicts'] else 'none'}")
    if r.get("conflict_notes"):
        print("Conflict notes:")
        for note in r["conflict_notes"]:
            print(f"  - {note}")
    if r.get("provenance_violations"):
        print(f"Provenance violations (untrusted commit touched a path it doesn't own, reverted): {r['provenance_violations']}")
    else:
        print("Provenance violations: none")
    if r.get("human_region_violations"):
        print(f"Human-region violations (incoming update quarantined, main kept): {r['human_region_violations']}")
    else:
        print("Human-region violations: none")
    print(f"Near-dupes flagged: {r['near_dupes'] if r['near_dupes'] else 'none'}")
    for line in format_synthesis_lines(r.get("synthesis")):
        print(line.lstrip("- "))
    print(f"Secret-scrub: {r['scrub']}")
    if r.get("scrub_warnings"):
        print(f"Scrub warnings (local-visibility hits, merge continued): {r['scrub_warnings']}")
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
