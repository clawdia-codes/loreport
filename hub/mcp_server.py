#!/usr/bin/env python3
"""
hub/mcp_server.py — bridge-A MCP server (design.md §D15 bridge A, §D17;
modules.md M13e).

Single-file, Python-3-stdlib-only. Exposes eight MCP tools over the shared brain,
all named with the `loreport_<verb>_<noun>` scheme (docs/visibility-design.md §4):

  - loreport_save_memory(block, provider) -> routes the block through the SAME
    scan-before-commit gate as hub/inbox_ingest.py (invoked as a subprocess —
    never re-implemented inline), committing to the calling provider's branch.
    The credential's `trust` (never caller-supplied) is forwarded to that
    subprocess's own --trust flag, which enforces ownership there: a
    cloud-trust caller may always create a new path, but may not update or
    delete a path whose CURRENT version on `main` is `visibility: local` or
    whose `source:` belongs to a different provider; a local-trust caller is
    unrestricted (the cross-provider reconcile flow depends on this).
    Returns {"status": "committed" | "quarantined", "detail": "..."}.
  - loreport_read_memory(name) -> reads memories/<name>.md, knowledge/<name>.md,
    or skills/<name>/SKILL.md from the latest `main` checkout. Refused for a
    cloud-trust caller if the item's `visibility` is `local`.
  - loreport_search_memories(query) -> case-insensitive substring scan over
    INDEX.md on `main`. Each hit is annotated with its capture source (the
    item's `source:` frontmatter, e.g. "chatgpt"; "unknown" if absent/unresolvable,
    "—" for skill entries, which carry no item frontmatter) so a calling
    provider can tell its own captures ("echoes") apart from cross-provider
    ones. A cloud-trust caller never sees `local` items in the results.
  - loreport_load_context() -> returns hub/published/packet.md (the current
    pinned bootstrap+PROFILE+INDEX packet; already excludes `local` items).
  - loreport_view_memory_settings() -> lists every item with its visibility.
    A cloud-trust caller sees `local` items as existence-only (hidden).
  - loreport_change_memory_settings(name, visibility) -> flips one item's
    `visibility` field. A cloud-trust caller may only change items it authored.
  - loreport_status() -> tooling version (from VERSION/git in the framework
    repo root, sibling of hub/, NOT the brain) plus brain freshness in one
    call: entry counts and shared/local split (read from `main`), and whether
    the published packet's stamped `main@<sha>` still matches `main`'s
    current sha — reported as STALE, prominently, the moment it doesn't. A
    missing packet is reported as missing, never as "current". Read-only.
  - loreport_whats_changed(days=14) -> two halves: condensed CHANGELOG.md
    entries (software), and per-provider capture activity on `main` over the
    window (brain) — entries touched, last-touched, and which entries. EVERY
    configured provider is listed even with zero activity in the window, so a
    silently-idle provider is visible rather than indistinguishable from a
    quiet one. Counts/dates are identical for cloud and local callers; only
    `local`-visibility entry NAMES are redacted for a cloud-trust caller
    (rolled into an "N private" count instead). Read-only.

Security invariants (§D17, §D-visibility):
  - Localhost bind ONLY — the HTTP transport binds 127.0.0.1, never the
    all-interfaces wildcard address and never an empty host string.
  - Credential -> branch mapping: each connection carries a provider identity
    (an HTTP header, or a --credential value on stdio); loreport_save_memory
    always uses THAT identity to pick the provider/* branch — a caller-supplied
    `provider` argument can never override it, so a stolen ChatGPT credential
    cannot write provider/claude or main. If the connection carries no
    recognized credential at all, loreport_save_memory fails closed
    (quarantined) rather than falling back to a caller-supplied `provider`
    argument.
  - Credential -> trust mapping: each connection also carries a trust tier
    ("local" or "cloud", from CREDENTIAL_TRUST_MAP; an unrecognized or absent
    credential defaults to "cloud" — least privilege). `local`-visibility
    items are refused to cloud-trust readers and hidden from cloud-trust
    search/settings-list results (docs/visibility-design.md §3).
  - Tools expose brain items only, never arbitrary filesystem paths.

Transports:
    python3 hub/mcp_server.py --transport stdio [--credential TOKEN]
    python3 hub/mcp_server.py --transport http  [--port 8765]
"""

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# One credential <=> one provider/* branch. In a real deployment these tokens are
# injected by the tunnel client / connector per connection (env vars named in
# hub/config/providers.json); a connection never gets to choose its own provider
# identity.
#
# PROVIDER_BRANCHES, CREDENTIAL_PROVIDER_MAP, and CREDENTIAL_TRUST_MAP are all
# derived from hub/config/providers.json below (falling back to these same
# hardcoded values if the config is missing or unparseable, so a broken config
# file can never crash the server).
_FALLBACK_PROVIDER_BRANCHES = {
    "chatgpt": "provider/chatgpt",
    "claude": "provider/claude",
    "codex": "provider/codex",
    "openclaw": "provider/openclaw",
}

_FALLBACK_CREDENTIAL_PROVIDER_MAP = {
    os.environ.get("MPB_CHATGPT_TOKEN", "chatgpt-local-dev-token"): "chatgpt",
    os.environ.get("MPB_CLAUDE_LOCAL_TOKEN", "claude-local-dev-token"): "claude",
    os.environ.get("MPB_CODEX_TOKEN", "codex-local-dev-token"): "codex",
    os.environ.get("MPB_OPENCLAW_TOKEN", "openclaw-local-dev-token"): "openclaw",
}

_FALLBACK_CREDENTIAL_TRUST_MAP = {
    os.environ.get("MPB_CHATGPT_TOKEN", "chatgpt-local-dev-token"): "cloud",
    os.environ.get("MPB_CLAUDE_LOCAL_TOKEN", "claude-local-dev-token"): "local",
    os.environ.get("MPB_CODEX_TOKEN", "codex-local-dev-token"): "local",
    os.environ.get("MPB_OPENCLAW_TOKEN", "openclaw-local-dev-token"): "local",
}


def _load_providers_config():
    """Read hub/config/providers.json (path relative to this script's own dir) and
    build (provider_branches, credential_provider_map, credential_trust_map).

    For each entry in `credentials`, the token value is
    os.environ.get(<env_key>, <default>); if that value is non-null it is added
    to both maps (token -> provider, token -> trust). credential_provider_map
    stays a plain token->provider dict (dispatch() is unchanged this phase);
    credential_trust_map (token->trust) is exposed for a later enforcement phase.

    Falls back to the hardcoded defaults above if providers.json is missing or
    unparseable, so a broken/absent config file never crashes the server."""
    config_path = os.path.join(HERE, "config", "providers.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        branches = {name: info["branch"] for name, info in cfg["providers"].items()}
        cred_map = {}
        trust_map = {}
        for env_key, info in cfg["credentials"].items():
            value = os.environ.get(env_key, info.get("default"))
            if value is not None:
                cred_map[value] = info["provider"]
                trust_map[value] = info["trust"]
        return branches, cred_map, trust_map
    except (OSError, ValueError, KeyError, TypeError):
        return (
            dict(_FALLBACK_PROVIDER_BRANCHES),
            dict(_FALLBACK_CREDENTIAL_PROVIDER_MAP),
            dict(_FALLBACK_CREDENTIAL_TRUST_MAP),
        )


PROVIDER_BRANCHES, CREDENTIAL_PROVIDER_MAP, CREDENTIAL_TRUST_MAP = _load_providers_config()

TOOLS = {
    "loreport_save_memory": {
        "description": "Commit an emit-grammar v1 block through the scan-before-commit "
                       "gate to the calling provider's branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "description": "the emit-grammar v1 block text"},
                "provider": {"type": "string", "description": "chatgpt | claude | codex | openclaw "
                            "(ignored if the connection already carries a credential)"},
            },
            "required": ["block"],
        },
    },
    "loreport_read_memory": {
        "description": "Read one brain item (memory, knowledge page, or skill) by name "
                       "from the latest main snapshot. A `local`-visibility item is "
                       "refused for a cloud-trust caller.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    "loreport_search_memories": {
        "description": "Case-insensitive substring search over INDEX.md on main. Each hit "
                       "is annotated with its capture source (the item's `source:` "
                       "frontmatter, e.g. 'chatgpt', or 'unknown'/'—' if not applicable) so "
                       "a caller can tell its own captures apart from cross-provider ones. "
                       "A cloud-trust caller never sees `local`-visibility items in results.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    "loreport_load_context": {
        "description": "Return the current pinned publish packet (bootstrap + PROFILE + "
                       "INDEX); `local`-visibility items are already excluded.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "loreport_view_memory_settings": {
        "description": "List every brain item (memories/knowledge/skills) with its "
                       "visibility setting. A cloud-trust caller sees `local` items as "
                       "existence-only (hidden), never their type or content.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "loreport_change_memory_settings": {
        "description": "Change one item's visibility (shared|local). A cloud-trust "
                       "caller may only change items it authored (source == its own "
                       "provider); a local-trust caller may change any item.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "visibility": {"type": "string", "description": "shared | local"},
            },
            "required": ["name", "visibility"],
        },
    },
    "loreport_status": {
        "description": "Tooling version AND data freshness in one call: loreport version, "
                       "brain entry counts (memories/skills/knowledge, shared vs private), "
                       "and whether the published packet is current with `main` — reported "
                       "as STALE, prominently, when it is not. Also reports `report`, the "
                       "address of the browsable HTML view of the brain. If that reads "
                       "NEEDS_TO_BE_SETUP, the report exists but is not reachable from "
                       "outside the user's machine yet: offer to walk them through "
                       "publishing it rather than staying silent about it. Read-only.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "loreport_whats_changed": {
        "description": "What changed lately, in two halves: condensed CHANGELOG.md entries "
                       "(software), and per-provider brain capture activity on `main` over "
                       "the window (entries touched, last-touched, which entries). Every "
                       "configured provider is listed even with zero activity. Counts/dates "
                       "are identical for cloud and local callers; `local`-visibility entry "
                       "names are redacted for a cloud-trust caller. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "lookback window in days (default 14)"},
            },
        },
    },
}


# --- git-from-main helpers ------------------------------------------------
#
# All reads (loreport_read_memory, loreport_search_memories,
# loreport_view_memory_settings, and the visibility check used by all of
# them) go through `git show main:<path>` / `git ls-tree main`, never the
# checked-out working tree — the shared tree can be parked on any
# provider/* branch at any moment (a capture in flight), so reading it
# directly would silently serve un-merged, un-scrubbed, possibly stale
# content (REVIEW.md #6/H1). Every one of these subprocess calls carries a
# 30s timeout (REVIEW.md #19); a stuck git process (e.g. index.lock) raises
# GitTimeout instead of hanging the request forever.

class GitTimeout(Exception):
    """Raised when a git subprocess used for a main-read exceeds its timeout."""


# Same repo-wide lock as inbox_ingest.py / brain_merge.py (identical relative
# path), so loreport_change_memory_settings' own checkout-main+edit+commit
# writes can never interleave with a capture or a merge (Phase E; the Phase C
# flock only covered inbox_ingest.py and brain_merge.py, not this tool's
# separate git-write path). Single-file/stdlib-only means this is duplicated
# rather than imported, same as SECRET_PATTERNS-style constants elsewhere.
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


def _run_git(brain_dir, *args, timeout=30):
    try:
        return subprocess.run(
            ["git", "-C", brain_dir] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitTimeout(f"git {' '.join(args)} timed out")


def _read_from_main(brain_dir, relpath):
    """Return the content of `relpath` as committed on `main`, or None if it
    doesn't exist there. Raises GitTimeout on a stuck git process."""
    r = _run_git(brain_dir, "show", f"main:{relpath}")
    if r.returncode != 0:
        return None
    return r.stdout


def _candidate_relpaths(name):
    return [
        (f"memories/{name}.md", "memory"),
        (f"knowledge/{name}.md", "knowledge"),
        (f"skills/{name}/SKILL.md", "skill"),
    ]


def _locate_item_on_main(brain_dir, name):
    """Return (relpath, item_type, content) for the first candidate
    (memories/<name>.md | knowledge/<name>.md | skills/<name>/SKILL.md) that
    exists ON MAIN, or (None, None, None) if the name is invalid or nothing
    matches there — existence is decided by `main`, never by the working
    tree. Raises GitTimeout."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None, None, None
    for relpath, typ in _candidate_relpaths(name):
        content = _read_from_main(brain_dir, relpath)
        if content is not None:
            return relpath, typ, content
    return None, None, None


def _iter_all_items_from_main(brain_dir):
    """Yield (name, item_type, relpath) for every brain item tracked on
    `main` (memories/*.md, knowledge/*.md, skills/*/SKILL.md), enumerated
    via `git ls-tree -r --name-only main` — never a working-tree directory
    listing. Raises GitTimeout."""
    r = _run_git(brain_dir, "ls-tree", "-r", "--name-only", "main")
    if r.returncode != 0:
        return
    for relpath in sorted(r.stdout.splitlines()):
        if relpath.startswith("memories/") and relpath.endswith(".md"):
            yield os.path.splitext(os.path.basename(relpath))[0], "memory", relpath
        elif relpath.startswith("knowledge/") and relpath.endswith(".md"):
            yield os.path.splitext(os.path.basename(relpath))[0], "knowledge", relpath
        elif relpath.startswith("skills/") and relpath.endswith("/SKILL.md"):
            parts = relpath.split("/")
            if len(parts) == 3:
                yield parts[1], "skill", relpath


# --- brain item / frontmatter helpers -----------------------------------------
#
# Simple line-scan frontmatter parsing (same style as inbox_ingest.py's
# parse_simple_yaml_scalars — duplicated on purpose: every hub/*.py file is
# single-file and stdlib-only, nothing is shared by import between them).

def _parse_frontmatter_scalars(text):
    """Return the scalar key: value pairs from a leading `---`/`---` frontmatter
    block, or {} if there is no such block (or it's empty/malformed)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line or line[0] in " \t-":
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v:
            result[k] = v
    return result


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
    default silently buys that relaxation for every unclassified item. Three
    callers need the distinction: the publish gate in snapshot_publish.py
    (which refuses while any item is unclassified), the secret-scrub split
    in brain_merge.py (which may only demote a hit to a warning for an item a
    human actually marked local), and the skills-are-shared carve-out in the
    egress resolvers, which exists to supply a default for a key skills do not
    carry and must not OVERRIDE one a human wrote.

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


def _visibility_of(typ, content):
    """Return "local" or "shared" for an item whose TYPE is already known.

    Every read path in this file must go through here rather than calling
    _visibility_from_text directly, because `visibility:` is required on items
    but does not exist for skills at all: a skill is a package, not an item
    (docs/format-spec.md §1). The parser fails closed on an absent key, so a
    skill run through it bare would come back `local` and vanish from every
    cloud-trust read — which is why the carve-out lives at the resolvers, the
    only layer that knows a path is a skill. This is not new policy: the
    status counter below and hub/brain_merge.py's secret scrub have always
    treated skills as shared/egress-critical.

    But the carve-out is a DEFAULT, not an override. Unconditional, it made
    loreport_change_memory_settings(visibility="local") on a skill a control
    that reports success and does nothing: the line was written to main, and
    the skill was still served at cloud trust, still in the packet, still in
    the paste surfaces, and still reported back as `shared` by
    loreport_view_memory_settings. An EXPLICIT `visibility:` therefore wins."""
    if typ == "skill" and not _has_explicit_visibility(content):
        return "shared"
    return _visibility_from_text(content)


def _item_visibility(brain_dir, name):
    """Return "local" or "shared" for the named brain item, read FROM MAIN
    (fail closed: shared ONLY on an explicit `visibility: shared`; skills are
    always shared — see _visibility_of), or None if the item doesn't exist on
    main. Raises GitTimeout."""
    _relpath, typ, content = _locate_item_on_main(brain_dir, name)
    if content is None:
        return None
    return _visibility_of(typ, content)


def _set_visibility_field(text, visibility):
    """Return `text` with its frontmatter `visibility:` line set to `visibility`,
    inserting the line if absent. Leaves the rest of the file byte-for-byte
    unchanged. If `text` has no `---`/`---` frontmatter block at all (shouldn't
    happen for a well-formed item — inbox_ingest.py requires one), a minimal
    block is prepended defensively rather than corrupting the file."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return f"---\nvisibility: {visibility}\n---\n" + text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return text  # malformed frontmatter (no closing ---); leave unchanged

    replaced = False
    new_fm_lines = []
    for line in lines[1:end_idx]:
        key = line.split(":", 1)[0].strip()
        if key == "visibility":
            new_fm_lines.append(f"visibility: {visibility}\n")
            replaced = True
        else:
            new_fm_lines.append(line)
    if not replaced:
        new_fm_lines.append(f"visibility: {visibility}\n")
    return lines[0] + "".join(new_fm_lines) + "".join(lines[end_idx:])


INDEX_ITEM_RE = re.compile(r"\[\[([^\]]+)\]\]")

# --- loreport_status / loreport_whats_changed helpers -------------------------
#
# The framework/tooling repo (VERSION, CHANGELOG.md) is REPO_ROOT — the sibling
# of hub/ that this script itself lives in — which is a DIFFERENT repo from
# `brain_dir` (the brain being served, e.g. a user's own loreport-<name> repo).
# `brain_dir`'s `main` is still read exclusively via `git show`/`git ls-tree`
# (never the working tree), same as every other read tool above.

# The packet's freshness stamp, written by snapshot_publish.py's build_packet_text
# (`f"<!-- loreport packet: main@{main_sha} {today} -->\n"`, main_sha itself from
# `git rev-parse --short main` — the exact same command used below to fetch the
# brain's current main sha, so the two shorthand forms are always comparable).
PACKET_STAMP_RE = re.compile(r"^<!-- loreport packet: main@([0-9a-f]+) (\d{4}-\d{2}-\d{2}) -->$")


def _packet_is_current(brain_dir, packet_sha, main_sha):
    """A packet is current if its stamped sha IS `main`'s current tip, OR if
    every path that changed between the stamp and the tip lives under
    hub/published/ — snapshot_publish.py itself commits packet.md (and the
    dated archive copy) onto `main` right after stamping it, which nudges
    `main` one commit past its own stamp on every single publish (confirmed:
    two consecutive days of `brain(publish): packet <date>` commits, each
    touching only hub/published/*). A bare sha== check would call that
    everyday, no-brain-content-changed state "STALE" — a false positive that
    would cry wolf on a healthy brain. Any change OUTSIDE hub/published/ in
    that gap means real content moved past what the packet reflects: that IS
    stale. Raises GitTimeout."""
    if packet_sha == main_sha:
        return True
    r = _run_git(brain_dir, "diff", "--name-only", packet_sha, main_sha)
    if r.returncode != 0:
        return False  # unresolvable diff (e.g. unknown sha) -> fail closed to STALE
    changed = [p for p in r.stdout.splitlines() if p.strip()]
    if not changed:
        return True
    return all(p.startswith("hub/published/") for p in changed)

# A capture commit's subject, per hub/inbox_ingest.py: "brain(capture): <entry> via <provider> [inbox]".
CAPTURE_SUBJECT_RE = re.compile(r"^brain\(capture\): (.+) via (\S+) \[inbox\]$")

CHANGELOG_VERSION_RE = re.compile(r"^## \[([^\]]+)\]\s*[—-]\s*(\d{4}-\d{2}-\d{2})\s*$")
CHANGELOG_CATEGORY_RE = re.compile(r"^### (\w+)")
CHANGELOG_CATEGORY_ORDER = ("Added", "Fixed", "Changed", "Removed", "Security", "Deprecated")


def _configured_provider_trust_pairs():
    """Return [(provider, trust), ...] for every credential entry configured in
    hub/config/providers.json, in the file's own order — regardless of whether
    that credential's env var happens to be set on THIS process (a provider
    with no live token right now is still a "configured provider" for status/
    activity reporting purposes). Falls back to one local-trust entry per
    hardcoded provider if providers.json is missing/unparseable, mirroring
    _load_providers_config's own fallback so a broken config never crashes
    either tool."""
    config_path = os.path.join(HERE, "config", "providers.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return [(info["provider"], info["trust"]) for info in cfg["credentials"].values()]
    except (OSError, ValueError, KeyError, TypeError):
        return [(p, "local") for p in _FALLBACK_PROVIDER_BRANCHES]


def _configured_providers_summary():
    """Render the configured providers as e.g. "openclaw, claude(local),
    claude(cloud), chatgpt" — a provider with only one configured trust level
    is shown bare; a provider configured at more than one trust level (e.g.
    claude has both a local-dev and a cloud/web credential) is shown once per
    trust level so the two are distinguishable."""
    trusts_by_provider = {}
    order = []
    for provider, trust in _configured_provider_trust_pairs():
        if provider not in trusts_by_provider:
            trusts_by_provider[provider] = []
            order.append(provider)
        if trust not in trusts_by_provider[provider]:
            trusts_by_provider[provider].append(trust)
    parts = []
    for provider in order:
        trusts = trusts_by_provider[provider]
        if len(trusts) == 1:
            parts.append(provider)
        else:
            parts.extend(f"{provider}({t})" for t in trusts)
    return ", ".join(parts)


def _parse_changelog_sections(text):
    """Yield (version, date_str, {category: [bullet, ...]}) for every dated
    `## [x.y.z] — YYYY-MM-DD` section in CHANGELOG.md, in the file's own
    (newest-first) order. `## [Unreleased]` is skipped — it carries no date
    to key freshness/window-filtering on."""
    lines = text.splitlines()
    sections = []
    i, n = 0, len(lines)
    while i < n:
        m = CHANGELOG_VERSION_RE.match(lines[i].rstrip())
        if not m:
            i += 1
            continue
        version, date_str = m.group(1), m.group(2)
        i += 1
        categories = {}
        current_cat = None
        while i < n and not lines[i].startswith("## ["):
            stripped = lines[i].strip()
            cm = CHANGELOG_CATEGORY_RE.match(stripped)
            if cm:
                current_cat = cm.group(1)
                categories.setdefault(current_cat, [])
            elif stripped.startswith("- ") and current_cat:
                categories[current_cat].append(stripped[2:])
            elif stripped and current_cat and categories.get(current_cat):
                # A soft-wrapped continuation line of the previous bullet —
                # this file hand-wraps prose bullets across multiple indented
                # lines with no leading "- ". Appending it onto the last
                # bullet (instead of silently dropping it) matters: dropping
                # it truncated every multi-line bullet mid-sentence, right at
                # the wrap point, before condensing ever got a real sentence
                # to cut at.
                categories[current_cat][-1] += " " + stripped
            i += 1
        sections.append((version, date_str, categories))
    return sections


def _condense_bullet(bullet):
    """Strip markdown emphasis/code markers and cut to the first sentence, so
    a paragraph-length changelog bullet condenses to one short clause instead
    of being echoed verbatim (the point of loreport_whats_changed is a quick
    narrative, not a re-paste of CHANGELOG.md)."""
    text = bullet.replace("**", "").replace("`", "").strip()
    idx = text.find(". ")
    if idx != -1:
        return text[:idx].strip()
    return text.rstrip(".")


def _format_software_summary(repo_root, days):
    changelog_path = os.path.join(repo_root, "CHANGELOG.md")
    try:
        with open(changelog_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return "software   CHANGELOG.md not found"

    sections = _parse_changelog_sections(text)
    if not sections:
        return "software   no dated CHANGELOG.md entries found"

    cutoff_date = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    in_window = [s for s in sections if s[1] >= cutoff_date]
    chosen = in_window if in_window else sections[:1]  # always show at least the latest

    lines = ["software"]
    for version, date_str, categories in chosen:
        lines.append(f"  v{version} ({date_str}):")
        seen_cats = set()
        for cat in CHANGELOG_CATEGORY_ORDER + tuple(categories.keys()):
            if cat in seen_cats or not categories.get(cat):
                continue
            seen_cats.add(cat)
            condensed = "; ".join(_condense_bullet(b) for b in categories[cat])
            lines.append(f"    {cat}: {condensed}")
    return "\n".join(lines)


def _iter_capture_commits(brain_dir):
    """Yield (commit_date_iso, entry_name, provider) for every
    `brain(capture): <entry> via <provider> [inbox]` commit on `main`,
    newest-first (git log's default order). Raises GitTimeout."""
    r = _run_git(brain_dir, "log", "--format=%cI%x00%s", "main")
    if r.returncode != 0:
        return
    for line in r.stdout.split("\n"):
        if not line:
            continue
        date_str, sep, subject = line.partition("\x00")
        if not sep:
            continue
        m = CAPTURE_SUBJECT_RE.match(subject.strip())
        if m:
            yield date_str, m.group(1), m.group(2)


def _format_provider_names(brain_dir, names, trust):
    """Render the entries a provider touched, redacting `local`-visibility
    entry NAMES for a cloud-trust caller (rolled into an "N private" count
    instead) — the count of touched entries itself is unaffected and reported
    identically to both trust levels by the caller. An entry we fail to
    resolve (deleted since, or a git hiccup on that one lookup) is treated as
    `local` and redacted for a cloud caller — fail closed, same convention as
    _visibility_from_text. A local-trust caller skips the lookup entirely and
    always sees every name."""
    if not names:
        return ""
    if trust == "local":
        return ", ".join(names)
    shown, redacted = [], 0
    for n in names:
        try:
            vis = _item_visibility(brain_dir, n)
        except GitTimeout:
            vis = None
        if vis == "shared":
            shown.append(n)
        else:
            redacted += 1
    text = ", ".join(shown)
    if redacted:
        text = f"{text}, {redacted} private" if text else f"{redacted} private"
    return text


def _format_brain_activity(brain_dir, days, trust):
    """Return the "brain" half of loreport_whats_changed, or None on a git
    timeout (the caller turns that into a proper tool error)."""
    now = datetime.datetime.now().astimezone()
    cutoff = now - datetime.timedelta(days=days)
    per_provider = {}
    try:
        for date_str, entry_name, provider in _iter_capture_commits(brain_dir):
            try:
                dt = datetime.datetime.fromisoformat(date_str)
            except ValueError:
                continue
            info = per_provider.setdefault(provider, {"names": [], "last": None})
            if info["last"] is None:
                info["last"] = dt  # git log is newest-first: first hit per provider = most recent
            if dt >= cutoff:
                info["names"].append(entry_name)
    except GitTimeout:
        return None

    # Every provider CONFIGURED in providers.json is listed, even one with zero
    # activity in-window (or ever) — a silently-idle provider must be visible,
    # not indistinguishable from one that's simply quiet this window.
    providers_order = []
    for provider, _trust in _configured_provider_trust_pairs():
        if provider not in providers_order:
            providers_order.append(provider)
    for provider in per_provider:
        if provider not in providers_order:
            providers_order.append(provider)

    lines = ["brain"]
    for provider in providers_order:
        info = per_provider.get(provider, {"names": [], "last": None})
        unique_names = list(dict.fromkeys(info["names"]))
        last = info["last"]
        if last is None:
            last_str = "no captures yet"
        else:
            delta_days = (now - last).days
            last_str = "today" if delta_days <= 0 else f"{delta_days}d ago"
        line = f"  {provider:<10s} {len(unique_names)} entries · {last_str}"
        try:
            names_display = _format_provider_names(brain_dir, unique_names, trust)
        except GitTimeout:
            return None
        if names_display:
            line += f" · {names_display}"
        lines.append(line)
    return "\n".join(lines)


# --- tool implementations -----------------------------------------------------

def tool_loreport_save_memory(brain_dir, provider, block, trust):
    if provider not in PROVIDER_BRANCHES:
        return {"status": "quarantined", "detail": f"unknown or unauthorized provider '{provider}'"}
    fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="mpb-capture-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(block)
        script = os.path.join(HERE, "inbox_ingest.py")
        try:
            # `trust` (from CREDENTIAL_TRUST_MAP, never caller-supplied) is
            # forwarded to inbox_ingest.py's own --trust flag so its
            # ownership check (own it, or it doesn't exist on main yet) is
            # enforced there — this was previously computed here and
            # discarded, which was the root cause of a cloud credential being
            # able to overwrite/delete any item regardless of ownership.
            r = subprocess.run(
                [sys.executable, script, provider, tmp_path, "--brain-dir", brain_dir,
                 "--trust", trust],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {"status": "quarantined", "detail": "git timeout"}
        detail = (r.stdout + r.stderr).strip()
        return {"status": "committed" if r.returncode == 0 else "quarantined", "detail": detail}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def tool_loreport_read_memory(brain_dir, name, trust):
    if not name or "/" in name or "\\" in name or ".." in name:
        return {"error": "invalid item name"}
    try:
        _relpath, typ, content = _locate_item_on_main(brain_dir, name)
        if content is None:
            return {"error": "not found"}
        vis = _visibility_of(typ, content)
    except GitTimeout:
        return {"error": "git timeout"}
    if vis == "local" and trust != "local":
        return {"error": "local item — not available to this caller"}
    return {"content": content}


def tool_loreport_search_memories(brain_dir, query, trust):
    try:
        index_content = _read_from_main(brain_dir, "INDEX.md")
        if index_content is None:
            return {"error": "INDEX.md not found on main"}
        q = (query or "").lower()
        raw_matches = [line for line in index_content.splitlines() if q in line.lower()]

        # Annotate every surviving hit with its capture source ("source:"
        # frontmatter, e.g. "chatgpt") so a calling provider can tell its own
        # captures ("echoes") apart from cross-provider ones — no filtering
        # here beyond the existing visibility/trust check below, which stays
        # unchanged: a cloud-trust caller still never sees a `local` item.
        #
        # Visibility check and source lookup share the SAME _locate_item_on_main
        # read (one `git show` per hit) instead of two, so annotating doesn't
        # double the git calls the old filter-only loop already made.
        matches = []
        for line in raw_matches:
            m = INDEX_ITEM_RE.search(line)
            name = m.group(1) if m else None

            if line.rstrip().endswith("(skill)"):
                # Skills have no item frontmatter to read a `source:` from —
                # annotate without a file read rather than pretending one exists.
                matches.append(line + "  [source: —]")
                continue

            source = "unknown"
            if name:
                _relpath, typ, content = _locate_item_on_main(brain_dir, name)
                if content is not None:
                    vis = _visibility_of(typ, content)
                    if vis == "local" and trust != "local":
                        continue  # unchanged: hidden from cloud-trust search results
                    fm_source = _parse_frontmatter_scalars(content).get("source")
                    if fm_source:
                        source = fm_source
            matches.append(line + f"  [source: {source}]")
    except GitTimeout:
        return {"error": "git timeout"}
    return {"matches": matches}


def tool_loreport_load_context(brain_dir):
    packet_path = os.path.join(brain_dir, "hub", "published", "packet.md")
    if not os.path.isfile(packet_path):
        return {"error": "no published packet yet — run snapshot_publish.py"}
    with open(packet_path, "r", encoding="utf-8") as fh:
        return {"content": fh.read()}


def tool_loreport_view_memory_settings(brain_dir, trust):
    items = []
    try:
        for name, typ, relpath in _iter_all_items_from_main(brain_dir):
            content = _read_from_main(brain_dir, relpath)
            if content is None:
                continue  # raced with a concurrent write between ls-tree and show; skip
            vis = _visibility_of(typ, content)
            if vis == "local" and trust != "local":
                items.append({"name": name, "visibility": "local", "hidden": True})
            else:
                items.append({"name": name, "type": typ, "visibility": vis})
    except GitTimeout:
        return {"error": "git timeout"}
    return {"items": items}


def tool_loreport_change_memory_settings(brain_dir, name, visibility, trust, provider):
    if visibility not in ("shared", "local"):
        return {"error": "visibility must be 'shared' or 'local'"}

    try:
        # The read (_locate_item_on_main), the ownership check derived from
        # it, and the checkout-main+edit+commit write are ALL inside the same
        # exclusive lock — same one inbox_ingest.py's capture and
        # brain_merge.py's merge use — so a concurrent merge can never land
        # between the read and the write (TOCTOU: previously the read
        # happened before the lock was taken, so a merge racing in between
        # could be silently clobbered by a write derived from stale content
        # even though the tool reported success).
        with brain_lock(brain_dir):
            relpath, _typ, text = _locate_item_on_main(brain_dir, name)
            if relpath is None:
                return {"error": "not found"}
            source = _parse_frontmatter_scalars(text).get("source")

            if trust == "local":
                pass  # a local-trust caller may change any item
            elif trust == "cloud" and provider is not None and source == provider:
                pass  # a cloud-trust caller may change only the items it authored
            else:
                return {"error": "not permitted: a cloud caller may only change memories it authored"}

            new_text = _set_visibility_field(text, visibility)
            path = os.path.join(brain_dir, relpath)

            _run_git(brain_dir, "checkout", "main")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            _run_git(brain_dir, "add", relpath)
            actor = provider if provider else "local"
            _run_git(brain_dir, "commit", "-m", f"brain(settings): {name} visibility -> {visibility} via {actor}")
    except GitTimeout:
        return {"status": "quarantined", "detail": "git timeout"}

    return {"status": "changed", "name": name, "visibility": visibility}


UNCONFIGURED_REPORT_URL = "NEEDS_TO_BE_SETUP"


def _report_url():
    """The browsable HTML report's URL, from hub/config/providers.json.

    Deliberately returned to CLOUD callers as well: they cannot reach a tailnet
    address, and security here rests on tailscale's authentication rather than on
    the URL being unguessable. Anything that depends on a URL staying secret is
    already broken. If the report is ever moved to public serving, this must be
    revisited -- at that moment handing out the URL becomes handing out every
    private entry."""
    env = os.environ.get("LOREPORT_REPORT_URL")
    if env:
        return env
    try:
        with open(os.path.join(HERE, "config", "providers.json"), "r", encoding="utf-8") as fh:
            configured = json.load(fh).get("report_url")
    except (OSError, ValueError):
        configured = None
    # Never return an empty string: an absent line reads as "no such feature",
    # whereas the literal sentinel is an actionable instruction the assistant can
    # act on. Hosting is genuinely optional, so not-set-up is a normal state, not
    # an error -- it just should not be a silent one.
    return configured or UNCONFIGURED_REPORT_URL


def tool_loreport_status(brain_dir):
    """loreport version + brain freshness in one call — the gate that matters
    is packet-vs-main: a version string alone stays green while a provider
    quietly reads a stale packet, so that comparison is never skipped."""
    version_path = os.path.join(REPO_ROOT, "VERSION")
    try:
        with open(version_path, "r", encoding="utf-8") as fh:
            version = fh.read().strip()
    except OSError:
        version = "unknown"

    try:
        r = _run_git(REPO_ROOT, "rev-parse", "--short", "HEAD")
        tooling_sha = r.stdout.strip() if r.returncode == 0 else "unknown"
        r = _run_git(REPO_ROOT, "log", "-1", "--format=%cd", "--date=short", "HEAD")
        tooling_date = r.stdout.strip() if r.returncode == 0 else "unknown"
    except GitTimeout:
        return {"error": "git timeout (framework repo)"}

    try:
        memory_count = knowledge_count = skill_count = 0
        shared_count = local_count = 0
        for _name, typ, relpath in _iter_all_items_from_main(brain_dir):
            content = _read_from_main(brain_dir, relpath)
            if content is None:
                continue  # raced with a concurrent write between ls-tree and show; skip
            if typ == "memory":
                memory_count += 1
            elif typ == "knowledge":
                knowledge_count += 1
            elif typ == "skill":
                skill_count += 1
            # The shared/local split is a privacy axis over CONTENT (memories,
            # knowledge pages) — it deliberately excludes skills. A skill's
            # frontmatter carries no `visibility:` field at all (it's a
            # procedure, not personal data), and _visibility_of therefore
            # reports every skill as "shared"; folding skills into this split
            # would inflate the shared count by every skill in the brain
            # rather than reflecting how many actual memories are shareable.
            if typ == "skill":
                continue
            if _visibility_of(typ, content) == "shared":
                shared_count += 1
            else:
                local_count += 1

        r = _run_git(brain_dir, "rev-parse", "--short", "main")
        main_sha = r.stdout.strip() if r.returncode == 0 else None
    except GitTimeout:
        return {"error": "git timeout (brain repo)"}

    if main_sha is None:
        return {"error": "brain repo has no `main` branch"}

    packet_path = os.path.join(brain_dir, "hub", "published", "packet.md")
    if not os.path.isfile(packet_path):
        packet_line = "packet     MISSING — no published packet yet (run snapshot_publish.py)"
    else:
        with open(packet_path, "r", encoding="utf-8") as fh:
            packet_lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        last_line = packet_lines[-1].strip() if packet_lines else ""
        m = PACKET_STAMP_RE.match(last_line)
        if not m:
            packet_line = "packet     published, but its freshness stamp is unreadable"
        else:
            packet_sha, packet_date = m.group(1), m.group(2)
            # THE GATE THAT MATTERS: has any BRAIN CONTENT moved past what the
            # packet reflects? Not "current" => STALE, called out prominently —
            # never silently folded into a plain "current" status.
            try:
                fresh = _packet_is_current(brain_dir, packet_sha, main_sha)
            except GitTimeout:
                return {"error": "git timeout (brain repo)"}
            if fresh:
                packet_line = f"packet     built {packet_date} from main @ {packet_sha}   ✓ current"
            else:
                packet_line = (f"packet     built {packet_date} from main @ {packet_sha}   "
                                f"✗ STALE — main is now @ {main_sha}")

    text = (
        f"loreport   {version} ({tooling_sha}, {tooling_date})\n"
        f"brain      {memory_count} memories · {skill_count} skills · "
        f"{knowledge_count} knowledge pages · main @ {main_sha}\n"
        f"           {shared_count} shareable · {local_count} stay on this machine\n"
        f"{packet_line}\n"
        f"providers  {_configured_providers_summary()}"
        + f"\nreport     {_report_url()}"
    )
    return {"content": text}


def tool_loreport_whats_changed(brain_dir, days, trust):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 14
    if days <= 0:
        days = 14

    software_text = _format_software_summary(REPO_ROOT, days)
    brain_text = _format_brain_activity(brain_dir, days, trust)
    if brain_text is None:
        return {"error": "git timeout (brain repo)"}
    return {"content": software_text + "\n\n" + brain_text}


def dispatch(brain_dir, credential, name, arguments):
    arguments = arguments or {}
    provider_from_credential = CREDENTIAL_PROVIDER_MAP.get(credential) if credential else None
    trust = CREDENTIAL_TRUST_MAP.get(credential, "cloud") if credential else "cloud"

    if name == "loreport_save_memory":
        # The credential's mapped provider always wins over any caller-supplied
        # `provider` argument — a stolen credential cannot pick another branch.
        # If there is no recognized credential at all, fail closed: never fall
        # back to a caller-supplied `provider` argument for routing.
        if provider_from_credential is None:
            return {"status": "quarantined",
                    "detail": "no recognized credential; refusing to route capture"}
        # `block` is declared required in the tool schema, but this server is
        # the only thing enforcing it: defaulting a missing argument to "" wrote
        # a 0-byte temp file, ran the whole ingest gate on it, and filed a
        # quarantine entry reading "no <MEMORY> block found" — which describes a
        # malformed block, not a caller that sent no block at all. Two such
        # 0-byte quarantine artifacts (2026-08-07 21:43, 19s apart) were
        # unexplainable from the digest for three days because of it. Refuse
        # here instead, with isError set so the caller retries.
        block = arguments.get("block")
        if not isinstance(block, str) or not block.strip():
            return {"error": "loreport_save_memory requires a non-empty `block` argument "
                             "(the emit-grammar v1 block text); nothing was captured"}
        return tool_loreport_save_memory(brain_dir, provider_from_credential, block, trust)
    if name == "loreport_read_memory":
        return tool_loreport_read_memory(brain_dir, arguments.get("name", ""), trust)
    if name == "loreport_search_memories":
        return tool_loreport_search_memories(brain_dir, arguments.get("query", ""), trust)
    if name == "loreport_load_context":
        return tool_loreport_load_context(brain_dir)
    if name == "loreport_view_memory_settings":
        return tool_loreport_view_memory_settings(brain_dir, trust)
    if name == "loreport_change_memory_settings":
        return tool_loreport_change_memory_settings(
            brain_dir, arguments.get("name", ""), arguments.get("visibility", ""),
            trust, provider_from_credential)
    if name == "loreport_status":
        return tool_loreport_status(brain_dir)
    if name == "loreport_whats_changed":
        return tool_loreport_whats_changed(brain_dir, arguments.get("days", 14), trust)
    return {"error": f"unknown tool '{name}'"}


# --- JSON-RPC 2.0 / MCP framing -----------------------------------------------

def handle_request(brain_dir, credential, req):
    # JSON-RPC 2.0 notifications (no "id" key) MUST NOT get a response — e.g.
    # MCP's "notifications/initialized". Replying anyway desyncs the client's
    # request/response pairing (it starts matching the wrong response to the
    # wrong pending request), which reads as "invalid request" / a dead
    # session on the tunnel-client side even though the server is fine. This
    # check must apply to every transport (stdio AND http), not just stdio.
    if "id" not in req:
        return None

    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "loreport-hub", "version": "1.0"},
            "capabilities": {"tools": {}},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                for n, t in TOOLS.items()
            ]
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        raw = dispatch(brain_dir, credential, tool_name, params.get("arguments"))
        result = _as_tool_result(raw)
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _as_tool_result(raw):
    """Wrap a tool's return dict in the MCP tools/call result shape: a `content`
    array of typed blocks (+ isError). MCP clients (e.g. the ChatGPT connector)
    reject a bare dict — `result.content` MUST be an array of {type,text} blocks.
    Without this, brain_read/brain_surface returned `{"content": "<str>"}` (content
    as a string, not an array) and failed the client's schema validation, so the
    model got nothing back even though the server read the item correctly."""
    is_error = isinstance(raw, dict) and "error" in raw
    if isinstance(raw, dict) and isinstance(raw.get("content"), str):
        text = raw["content"]
    elif isinstance(raw, dict) and "matches" in raw:
        matches = raw.get("matches") or []
        text = "\n".join(matches) if matches else "(no matches)"
    else:
        text = json.dumps(raw)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _write_response(payload):
    """Write one JSON-RPC response line. Returns False if the pipe is gone (the
    caller should stop reading stdin) instead of letting BrokenPipeError/OSError
    crash the process — a torn-down pipe from the host's side is normal during
    reconnects and must not kill the whole stdio session."""
    try:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def run_stdio(brain_dir, credential):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            if not _write_response({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}):
                break
            continue
        resp = handle_request(brain_dir, credential, req)
        if resp is None:
            continue
        if not _write_response(resp):
            break


def make_http_handler(brain_dir):
    class MCPHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                req = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            # Per-connection credential identifies exactly one provider — the
            # tunnel/connector sets this header per authenticated connection.
            credential = self.headers.get("X-MPB-Credential")
            resp = handle_request(brain_dir, credential, req)
            if resp is None:
                # A JSON-RPC notification (no "id") gets no body — only an
                # Accepted status, same "no response" semantics as stdio.
                self.send_response(202)
                self.end_headers()
                return
            body = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # quiet; the daily digest is the audit surface, not stderr noise

    return MCPHandler


def run_http(brain_dir, host, port):
    # SECURITY: bind localhost ONLY. Never the all-interfaces wildcard address,
    # never an empty host string. The tunnel client is the only thing allowed to
    # reach this port from outside the machine (hub/config/tunnel-client.json).
    if host != "127.0.0.1":
        raise ValueError("refusing to bind to a non-localhost address")
    handler_cls = make_http_handler(brain_dir)
    server = HTTPServer((host, port), handler_cls)
    print(f"hub mcp_server listening on http://{host}:{port} (localhost only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# --- CLI -----------------------------------------------------------------

def default_brain_dir():
    return REPO_ROOT


def main():
    parser = argparse.ArgumentParser(description="Loreport hub MCP server (bridge A).")
    parser.add_argument("--brain-dir", default=None,
                        help="Brain repo root (default: inferred from this script's location)")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=8765, help="HTTP transport port (localhost only)")
    # Prefer the environment. A credential passed in argv is world-readable via
    # `ps aux` to every local user, and HUB.md tells operators to generate a real
    # random token per connection -- so argv would leak an actual secret. The flag
    # stays for compatibility with existing units and manual invocations.
    parser.add_argument("--credential", default=os.environ.get("LOREPORT_CREDENTIAL"),
                        help="stdio-mode credential identifying the calling provider. "
                             "Prefer LOREPORT_CREDENTIAL in the environment; a value "
                             "passed here is visible in the process list.")
    args = parser.parse_args()

    brain_dir = args.brain_dir or default_brain_dir()

    if args.transport == "stdio":
        run_stdio(brain_dir, args.credential)
    else:
        run_http(brain_dir, "127.0.0.1", args.port)


if __name__ == "__main__":
    main()
