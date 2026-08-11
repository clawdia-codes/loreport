#!/usr/bin/env python3
"""
hub/inbox_ingest.py — capture-inbox scan-before-commit ingress (design.md §D17,
modules.md M13c).

Single-file, Python-3-stdlib-only. Validates one `emit-grammar v1` block (docs/
format-spec.md §5) and either commits it to the named provider's branch or
quarantines it. Raw, unscanned content never enters git.

Validation pipeline:
  1. Parse    — extract the <MEMORY file="…" action="…"> wrapper + INDEX: line.
  2. Schema   — frontmatter has name/description/type; name is a kebab-slug;
                type is in the enum; filename stem == name.
  3. Secret-scan   — same regex patterns as brain_merge.py; any hit -> quarantine.
  4. Imperative-scan (provenance rule, mechanical) — an assistant-directed,
     injection-shaped instruction, not attributed to a source; any hit -> quarantine.
     Scoped to injection *shapes* (not any first-person "always"/"never" statement)
     so a legitimate memory like "User always prefers dark mode" is never
     false-positived; runs over the full raw block (frontmatter + body + INDEX
     line), not just the body, so an injection hidden in e.g. the description
     field is still caught. When in doubt this scan PASSes — the secret scan
     above is the fail-closed gate; this is a lighter guard.
  5. Ownership  — for a `cloud`-trust caller only (a `local`-trust caller is
     unrestricted — the cross-provider reconcile flow depends on this): may
     always CREATE a path that doesn't exist on `main`; may NOT update or
     delete a path whose version ON MAIN has `visibility: local`, or whose
     `source:` is a provider other than the caller's own. Read from `main`
     server-side via git — never from the caller-submitted body, which is
     unvalidated. Any refusal -> quarantine, never a silent drop.
  6. Commit   — on pass: checkout provider/<name>, write the file, commit with
                structured metadata (including a `Trust:` trailer consumed by
                brain_merge.py's merge-side gate; a missing trailer is treated
                as `cloud`, fail closed).
  7. Quarantine — on any failure: copy to hub/quarantine/<provider>/<date>-<file>,
                  append a digest entry, exit nonzero. Never commit to any branch.

CLI:
    python3 hub/inbox_ingest.py <provider> <block-file> [--brain-dir PATH] [--trust local|cloud]
    # provider: one of "chatgpt", "claude", "codex", "openclaw"
    # --trust: trust tier of the calling credential; defaults to "cloud" if
    #          omitted (fail closed — an omitted trust must never be treated
    #          as the unrestricted "local" tier).
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

HERE = os.path.dirname(os.path.abspath(__file__))

_FALLBACK_PROVIDERS = ("chatgpt", "claude", "codex", "openclaw")


def _load_providers():
    """Derive the list of provider names from hub/config/providers.json (path
    relative to this script's own dir). Falls back to the hardcoded default
    tuple above if the file is missing or unparseable, so a broken/absent
    config can never crash ingest."""
    config_path = os.path.join(HERE, "config", "providers.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return tuple(cfg["providers"].keys())
    except (OSError, ValueError, KeyError, TypeError):
        return _FALLBACK_PROVIDERS


PROVIDERS = _load_providers()
ITEM_TYPES = {"user", "feedback", "project", "reference", "knowledge", "person", "decision"}
# Lifecycle + work/private axis (docs/taxonomy-lifecycle-design.md). All three
# fields below are OPTIONAL; absent `lifespan` means `permanent`, and absent
# `domain` means unclassified. Kept byte-identical to brain_merge.py's copies —
# every hub/*.py file is single-file and stdlib-only, nothing is imported between them.
LIFESPANS = {"permanent", "active", "temporary"}
DOMAINS = {"work", "personal", "both"}
EXPIRES_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Identical secret-regex set to brain_merge.py / snapshot_publish.py (duplicated on
# purpose: every hub/*.py file is single-file and stdlib-only; nothing is shared by
# import between them).
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

# The provenance rule, enforced mechanically: a line that is *injection-shaped* —
# it addresses the assistant directly and tries to override its instructions or
# context — is quarantined, unless the line is explicitly attributed to a source
# (quoting what the source says, rather than obeying it). Deliberately narrow: a
# bare "always"/"never" is NOT enough (that would false-positive ordinary
# first-person memories like "User always prefers dark mode"); the shape must
# look like an instruction aimed at the assistant, not a fact about the user.
INJECTION_PATTERNS = [
    # "ignore/disregard/forget" + a reference to prior instructions/context.
    re.compile(
        r"(?i)\b(ignore|disregard|forget)\b[^.\n]*"
        r"\b(previous|prior|above|earlier|these|the)\b[^.\n]*"
        r"\b(instruction|prompt|rule|context|message)s?\b"
    ),
    # A direct command to the assistant ("you must/should/shall/are to/...").
    re.compile(r"(?i)\byou\s+(must|should|shall|are\s+to|need\s+to|have\s+to)\b"),
    # A leading imperative aimed at the assistant ("Always ignore…", "Never reveal…").
    re.compile(r"(?i)^\s*(always|never)\s+(ignore|disregard|reveal|send|execute|run|delete|forget)\b"),
]
ATTRIBUTED_PREFIX_RE = re.compile(r"(?i)^\s*(source\s+says|according\s+to|the\s+(article|document|source|email)\s+(says|states|claims))\s*:")

MEMORY_RE = re.compile(
    r'<MEMORY\s+file="(?P<file>[^"]+)"\s+action="(?P<action>[^"]+)"\s*>(?P<body>.*?)</MEMORY>',
    re.DOTALL,
)
# Fallback for the ONE malformation that produced six of the eleven observed
# quarantines: a `<MEMORY>` opening tag with the file=/action= attributes absent
# (or only one of them present). The content is intact and the block carries a
# valid `name:` in its own frontmatter, so the attributes are recoverable —
# see _infer_attrs, which refuses rather than guessing when they are not.
MEMORY_ANY_RE = re.compile(r"<MEMORY(?P<attrs>[^>]*)>(?P<body>.*?)</MEMORY>", re.DOTALL)
ATTR_RE = re.compile(r'(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"(?P<val>[^"]*)"')
INDEX_LINE_RE = re.compile(r"^INDEX:\s*(.*)$", re.MULTILINE)
EMPTY_BLOCK_DETAIL = ("empty-block: the capture block was empty (0 bytes or whitespace only) — "
                      "the caller emitted nothing to ingest; the block never reached this gate")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
# Used ONLY by _repair_headless_frontmatter, to decide whether the lines at the
# top of a body are a frontmatter block that lost its opening `---`. The key set
# is the schema's own vocabulary (validate_schema below): an unknown key means
# "this is prose, leave it alone", which is what keeps a line like
# `Continuation doc: …` from being swallowed as metadata.
FM_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s+(?=\S)")
FM_KEYS = {"name", "description", "type", "visibility", "source", "captured",
           "domain", "lifespan", "expires"}


# --- parsing helpers ---------------------------------------------------------

def parse_simple_yaml_scalars(text):
    result = {}
    for line in text.splitlines():
        if not line or line[0] in " \t-":
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v:
            result[k] = v
    return result


def parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return parse_simple_yaml_scalars(m.group(1)), text[m.end():]


def strip_fence(text):
    """Tolerate a block-file that still has its outer ``` fence (as pasted from a
    chat UI's emit-grammar block) as well as bare, fence-free content."""
    lines = text.strip("\n").splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines) + "\n"


def _repair_headless_frontmatter(body):
    """Restore a frontmatter block whose OPENING `---` the emitter dropped.
    Returns (body, repaired).

    Every one of the four archived bare-`<MEMORY>` captures has this shape too —
    the tag's attributes and the opening delimiter went missing together, in the
    same emit. Three end their key lines with `---`; one has no delimiter at all
    and runs into prose after a blank line. Inferring the path but committing
    the body as-is would trade a quarantine for something worse: a committed
    file whose frontmatter no parser in hub/ can read, so it lands in no INDEX
    and is invisible to every visibility check.

    This only ever ADDS delimiters. It never edits, reorders or supplies a key,
    and it refuses (returning the body untouched, for the schema gate to reject
    as before) unless the leading lines are unambiguously frontmatter: every
    line up to the terminator is `<known-key>: <value>`, and `name:` and `type:`
    are both among them. A body starting with prose, or with one unrecognised
    key, is left alone rather than guessed at."""
    if FRONTMATTER_RE.match(body):
        return body, False
    lines = body.split("\n")
    keys, i = [], 0
    while i < len(lines) and lines[i].strip() not in ("---", ""):
        m = FM_KEY_RE.match(lines[i])
        if not m or m.group(1) not in FM_KEYS:
            return body, False
        keys.append(m.group(1))
        i += 1
    if i >= len(lines) or "name" not in keys or "type" not in keys:
        return body, False
    head = "\n".join(lines[:i])
    # `---` terminator: consume it (it is the frontmatter's closing delimiter).
    # Blank-line terminator: keep the blank line, it separates body from
    # frontmatter once the closing `---` is inserted above it.
    rest = "\n".join(lines[i + 1:] if lines[i].strip() == "---" else lines[i:])
    return f"---\n{head}\n---\n{rest}", True


def _infer_attrs(attrs, body):
    """Fill in a `<MEMORY>` tag's missing `file=`/`action=` from the block's OWN
    frontmatter. Returns (file, action, err).

    FAIL CLOSED: this may only ever RE-READ a name the block already states. It
    never falls back to the block file's name (a temp file called
    `mpb-capture-9x7u7u37.txt` would become `memories/mpb-capture-9x7u7u37.md`),
    never to a slug derived from the description, and never to a folder for a
    `type:` the schema doesn't know. Anything it cannot read from the
    frontmatter is a refusal, i.e. the same quarantine as before — the point of
    the inference is to stop losing recoverable captures, not to accept more.

    `action` defaults to "new" and NOT to "delete": an absent attribute must
    never resolve to the one action that destroys an item. "new" vs "update" is
    safe to guess because ownership is enforced per PATH, not per action
    (check_ownership takes no action argument) and commit_block writes the file
    identically for both; the action reaches only the commit trailer.

    The folder comes from `type:` per docs/format-spec.md ("user/feedback/
    project/reference live in memories/; knowledge in knowledge/"). Skills are
    deliberately not inferrable — they are a directory with a meta.yaml, not a
    single item file."""
    file_attr = attrs.get("file")
    action = attrs.get("action") or "new"
    if file_attr:
        return file_attr, action, None

    missing = 'parse-error: <MEMORY> tag has no file="…" attribute'
    fm, _ = parse_frontmatter(body)
    if not fm:
        return None, None, f"{missing} and the block has no frontmatter to infer one from"
    name = fm.get("name")
    if not name:
        return None, None, f"{missing} and the block's frontmatter has no `name:` to infer one from"
    if not KEBAB_RE.match(name):
        return None, None, (f"{missing} and its frontmatter `name: {name[:60]}` is not a "
                            "kebab-case slug, so it cannot be turned into a path")
    typ = fm.get("type")
    if typ not in ITEM_TYPES:
        return None, None, (f"{missing} and its frontmatter `type: {str(typ)[:40]}` is not a "
                            "known item type, so the target folder is unknown")
    folder = "knowledge" if typ == "knowledge" else "memories"
    return f"{folder}/{name}.md", action, None


def parse_block(raw_text):
    """Parse one emit-grammar block. Returns (block, err); `err` is the
    quarantine DETAIL, and quarantine_reason() maps it to the reason."""
    # An empty input is its own condition, not a malformed block. Every 0-byte
    # quarantine artifact ever written here came from this path (quarantine()
    # copies the input verbatim, so an empty input yields an empty artifact),
    # and it was reported with the same "no <MEMORY> block found" wording as a
    # genuinely malformed block — one message covering two conditions, which
    # left two 2026-08-07 events unexplainable from the digest alone.
    if not raw_text.strip():
        return None, EMPTY_BLOCK_DETAIL
    text = strip_fence(raw_text)
    m = MEMORY_RE.search(text)
    if m:
        # A well-formed tag stays on the strict path, byte for byte as before:
        # the recovery below is for an emitter that demonstrably lost the
        # grammar, not a licence to repair blocks that kept it.
        file_attr, action, body = m.group("file"), m.group("action"), m.group("body")
        body = body[1:] if body.startswith("\n") else body
        inferred = []
    else:
        bare = MEMORY_ANY_RE.search(text)
        if not bare:
            return None, "parse-error: no <MEMORY …>…</MEMORY> block found"
        body = bare.group("body")
        body = body[1:] if body.startswith("\n") else body
        attrs = dict(ATTR_RE.findall(bare.group("attrs")))
        body, repaired = _repair_headless_frontmatter(body)
        file_attr, action, err = _infer_attrs(attrs, body)
        if err:
            return None, err
        inferred = [k for k in ("file", "action") if k not in attrs]
        if repaired:
            inferred.append("frontmatter delimiters")
    # A missing trailing `INDEX:` line is NOT an error. Nothing reads it: the
    # parsed value has no consumer anywhere in hub/, and INDEX.md is rebuilt
    # deterministically from item frontmatter by brain_merge.build_index_bytes.
    # It cannot even serve as a truncation check — the item's content is
    # already bounded by the `</MEMORY>` tag that MEMORY_ANY_RE requires. So
    # discarding a whole capture over it lost real content for no signal.
    idx_m = INDEX_LINE_RE.search(text)
    block = {
        "file": file_attr,
        "action": action,
        "body": body,
        "index_line": idx_m.group(0) if idx_m else None,
        "inferred": inferred,
        "raw": text,
    }
    return block, None


def quarantine_reason(err):
    """The quarantine REASON for a parse_block error detail. An empty capture
    gets its own reason: "parse-error" says the emitter sent something this
    gate could not read, which is a different diagnosis (and a different fix)
    from the emitter having sent nothing at all."""
    return "empty-block" if err == EMPTY_BLOCK_DETAIL else "parse-error"


def validate_schema(block):
    file_path, action, body = block["file"], block["action"], block["body"]
    if action not in ("new", "update", "delete"):
        return f"schema-invalid: unknown action '{action}'"

    # Path/action allowlist (runs for EVERY action, before any filesystem/git
    # operation): the emit block's `file="…"` attribute must resolve to a brain
    # item path, never an arbitrary filesystem location. This blocks traversal
    # ("../../"), absolute paths, and writes/deletes outside memories/knowledge/
    # skills — e.g. overwriting prompts/bootstrap.md or deleting PROFILE.md or
    # hub code.
    if os.path.isabs(file_path):
        return f"schema-invalid: absolute path '{file_path}' is not allowed"
    normalized = os.path.normpath(file_path)
    if os.path.isabs(normalized) or ".." in normalized.split(os.sep):
        return f"schema-invalid: path '{file_path}' escapes the brain directory"
    if not normalized.startswith(("memories/", "knowledge/", "skills/")):
        return f"schema-invalid: path '{file_path}' is outside the memories/knowledge/skills allowlist"
    if not normalized.endswith(".md"):
        return f"schema-invalid: path '{file_path}' does not end in .md"

    stem = os.path.splitext(os.path.basename(file_path))[0]
    if action == "delete":
        return None  # no frontmatter required for a delete block
    fm, _rest = parse_frontmatter(body)
    if not fm:
        return "schema-invalid: missing or malformed frontmatter"
    name, description, typ = fm.get("name"), fm.get("description"), fm.get("type")
    if not name or not KEBAB_RE.match(name):
        return f"schema-invalid: name '{name}' is not a kebab-slug"
    if not description:
        return "schema-invalid: missing description"
    if typ not in ITEM_TYPES:
        return f"schema-invalid: type '{typ}' not in enum {sorted(ITEM_TYPES)}"
    if name != stem:
        return f"schema-invalid: name '{name}' != filename stem '{stem}'"

    # Optional `visibility` field (docs/format-spec.md §1): if the author included
    # it, it must be exactly "shared" or "local"; if absent, that's fine — it's
    # treated as shared downstream and is never injected here. commit_block()
    # writes block["body"] verbatim, so a visibility field the author wrote is
    # preserved automatically.
    visibility = fm.get("visibility")
    if visibility is not None and visibility not in ("shared", "local"):
        return f"schema-invalid: visibility '{visibility}' must be 'shared' or 'local'"

    # Optional lifecycle fields (docs/taxonomy-lifecycle-design.md Phase 2).
    lifespan = fm.get("lifespan")
    if lifespan is not None and lifespan not in LIFESPANS:
        return f"schema-invalid: lifespan '{lifespan}' not in enum {sorted(LIFESPANS)}"

    expires = fm.get("expires")
    if expires is not None:
        # Format is enforced HERE and only here. Downstream (brain_merge.is_expired)
        # treats an unparseable date as "not expired", because once an item is on
        # disk the safe reading of a broken date is to leave it in the hot index —
        # so capture time is the last moment a bad date can still be rejected while
        # the author is present to fix it.
        if not EXPIRES_RE.match(expires) or not _is_real_date(expires):
            return f"schema-invalid: expires '{expires}' must be a real YYYY-MM-DD date"
        # A stated contradiction, not an inference: `expires` is the archival
        # trigger for temporary context, so an item that declares itself
        # permanent or active cannot also declare an expiry date. An item that
        # omits `lifespan` entirely is NOT rejected — capture models routinely
        # emit an expiry without the lifespan, and quarantining a well-formed
        # memory over a missing default would lose real content.
        if lifespan in ("permanent", "active"):
            return f"schema-invalid: lifespan '{lifespan}' cannot carry an expires date"

    # Optional `domain` — the work-vs-private axis. This is NOT cloud exposure:
    # that is `visibility`, and the two are independent (a work note may be local,
    # a personal note may be shared). Nothing here may be used to infer visibility.
    domain = fm.get("domain")
    if domain is not None and domain not in DOMAINS:
        return f"schema-invalid: domain '{domain}' not in enum {sorted(DOMAINS)}"

    return None


def _is_real_date(value):
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


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


def scan_secrets(text):
    for pat in SECRET_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return None


def scan_imperative(raw_text):
    """Injection-shaped-content scan over the FULL raw block (frontmatter +
    body + INDEX line) — a hidden directive in e.g. the description field is
    caught the same as one in the body. Quarantines only assistant-directed
    injection shapes (see INJECTION_PATTERNS); first-person / subject-led
    statements ("I always…", "User always…", "**How to apply:** Always…")
    never match and PASS through, since they aren't instructions aimed at the
    assistant. The attributed-prefix rescue still applies line-by-line."""
    for line in raw_text.splitlines():
        if ATTRIBUTED_PREFIX_RE.match(line):
            continue
        for pat in INJECTION_PATTERNS:
            if pat.search(line):
                return line.strip()
    return None


# --- git plumbing --------------------------------------------------------------

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


class OwnershipError(RuntimeError):
    """Raised when a cloud-trust caller attempts to write a path whose CURRENT
    version on `main` it does not own (wrong `source:`) or that is
    `visibility: local`. Never raised for a local-trust caller (unrestricted)
    or for a path that doesn't exist on `main` yet (create, always allowed)."""


def _read_from_main(brain_dir, relpath):
    """Return `relpath`'s content as committed on `main`, or None if it
    doesn't exist there. Reads via `git show main:<path>` — NEVER the
    caller-submitted block body (unvalidated: a caller can submit any
    `source:`/`visibility:` it likes) and never the working tree (which may
    be parked on any provider/* branch mid-capture)."""
    r = git(brain_dir, "show", f"main:{relpath}", check=False)
    if r.returncode != 0:
        return None
    return r.stdout


def check_ownership(brain_dir, provider, trust, rel_path):
    """Enforce per-credential trust for a write (update or delete) to
    `rel_path`. Returns None if the write is allowed, else a human-readable
    refusal reason.

    - `local`-trust: unrestricted (the cross-provider reconcile flow — Claude
      Code updating an item captured by another provider — depends on this).
    - `cloud`-trust: may always CREATE a path with no version on `main` yet.
      May NOT update/delete a path whose `main` version is `visibility:
      local`, or whose `source:` is a provider other than its own. Visibility
      is decided via the same fail-closed parser as the read path
      (_visibility_from_text) so a hand-edited/malformed `visibility: local`
      field can't be used to bypass this check the way it could bypass a
      naive `== "local"` string compare. Since that parser now reads an
      ABSENT `visibility:` as `local` too, an unclassified item on main is
      update-protected as well: a cloud provider's write to one is refused
      and quarantined with this reason attached, rather than applied.
    """
    if trust == "local":
        return None
    main_text = _read_from_main(brain_dir, rel_path)
    if main_text is None:
        return None  # nothing on main yet -> CREATE, always allowed
    main_visibility = _visibility_from_text(main_text)
    fm, _rest = parse_frontmatter(main_text)
    main_source = (fm or {}).get("source")
    if main_visibility == "local":
        return (f"cloud-trust caller (provider={provider}) may not update/delete "
                f"'{rel_path}': its main version is visibility: local")
    if main_source != provider:
        return (f"cloud-trust caller (provider={provider}) may not update/delete "
                f"'{rel_path}': its main version has source: {main_source!r}, not "
                f"'{provider}'")
    return None


# One repo-wide lock, shared with brain_merge.py (same relative path), so a
# capture and a nightly merge can never interleave their git mutations
# (REVIEW.md #5). Single-file/stdlib-only means this is duplicated rather
# than imported, same as SECRET_PATTERNS etc.
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


def is_noop_commit(brain_dir):
    """True if nothing is staged (`git diff --cached --quiet` exit 0) — a
    re-capture of an identical `update` stages nothing, and `git commit`
    would fail with "nothing to commit"; that's a clean no-op, not a
    failure."""
    r = git(brain_dir, "diff", "--cached", "--quiet", check=False)
    return r.returncode == 0


# --- quarantine / commit --------------------------------------------------------

def quarantine(brain_dir, provider, block_file, reason, detail):
    qdir = os.path.join(brain_dir, "hub", "quarantine", provider)
    os.makedirs(qdir, exist_ok=True)
    today = date.today().isoformat()
    base = os.path.basename(block_file)
    dest = os.path.join(qdir, f"{today}-{base}")
    n = 1
    root_dest = dest
    while os.path.exists(dest):
        n += 1
        dest = f"{root_dest}.{n}"
    with open(block_file, "r", encoding="utf-8", errors="replace") as src:
        raw = src.read()
    with open(dest, "w", encoding="utf-8") as dst:
        dst.write(raw)

    digest_path = os.path.join(brain_dir, "hub", "quarantine", "digest.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    is_new = not os.path.isfile(digest_path)
    ts = datetime.now().isoformat(timespec="seconds")
    with open(digest_path, "a", encoding="utf-8") as fh:
        if is_new:
            fh.write("# Quarantine digest\n\n"
                     "Every block that failed the inbox_ingest.py scan-before-commit\n"
                     "gate is logged here — nothing is silently dropped.\n\n")
        fh.write(f"## {ts} — QUARANTINE ({provider})\n")
        fh.write(f"- file: {os.path.relpath(dest, brain_dir)}\n")
        fh.write(f"- reason: {reason}\n")
        fh.write(f"- detail: {detail}\n\n")

    print(f"QUARANTINED: {reason} — {detail}")
    print(f"Quarantine file: {dest}")


class CleanupError(RuntimeError):
    """Raised when the scoped post-failure cleanup cannot put the capture's own
    path back the way it found it. Never swallowed and never widened into a
    tree-wide wipe: an operator resolving one half-staged path by hand is
    strictly better than silently discarding every other uncommitted change in
    the shared working tree."""


def _path_in_head(brain_dir, rel_path):
    """True if `rel_path` is committed on the currently checked-out branch."""
    return git(brain_dir, "cat-file", "-e", f"HEAD:{rel_path}", check=False).returncode == 0


def _restore_capture_path(brain_dir, rel_path, in_head):
    """Undo THIS capture's index/worktree change to `rel_path` — and nothing
    else (REVIEW.md #13, live data loss measured 2026-08-07/08).

    The predecessor of this function was `git checkout -- .` + a pathless
    `git reset`, which discarded every uncommitted change in the shared
    working tree, not just the paths this capture wrote. Because a dirty tree
    is exactly what makes `git checkout provider/<host>` fail, that handler
    destroyed the very hand-edit that caused the failure.

    `in_head` must be sampled BEFORE the capture mutates anything — after a
    `git rm` the path is still in HEAD, but after a failed create it never
    was, and the two need opposite repairs (restore vs. delete the untracked
    leftover, which the old handler left behind for `finally`'s
    `git checkout main` to carry onto main).

    Raises CleanupError if the path is not clean afterwards. The postcondition
    is asserted against `git status`, not inferred from the individual return
    codes, so a git that "succeeds" without restoring is still caught.
    """
    abs_path = os.path.join(brain_dir, rel_path)
    git(brain_dir, "reset", "-q", "HEAD", "--", rel_path, check=False)
    if in_head:
        git(brain_dir, "checkout", "HEAD", "--", rel_path, check=False)
    elif os.path.exists(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass  # reported by the postcondition check below

    status = git(brain_dir, "status", "--porcelain", "--", rel_path, check=False)
    leftover = (not in_head) and os.path.exists(abs_path)
    if status.returncode != 0 or status.stdout.strip() or leftover:
        raise CleanupError(
            f"scoped cleanup did not restore {rel_path}: "
            f"git status --porcelain -> {status.stdout.strip()!r} "
            f"(rc={status.returncode}, exists_on_disk={os.path.exists(abs_path)})"
        )


def commit_block(brain_dir, provider, block, trust):
    """Commit `block` to provider/<provider>. Returns "committed" or
    "skipped: no change" (identical content re-captured — not a failure).
    Raises OwnershipError (never touching the working tree) if `trust` is
    "cloud" and the write fails the ownership check against `main`.

    Wrapped in an exclusive repo lock (REVIEW.md #5) so this can never
    interleave with brain_merge.py's git mutations — the ownership check
    itself also runs inside this lock, so it can't race a concurrent merge
    that changes what's on `main` between the check and the write. On ANY
    git failure (including a timeout raised by git()) the ONE path this
    capture wrote is restored to its committed state before the exception
    propagates, so a failed capture never poisons the next capture or the
    nightly merge with a half-staged change (REVIEW.md #13) — and no other
    uncommitted change in the shared working tree is touched. If the capture
    failed before mutating anything (e.g. `git checkout provider/<host>`
    refused because the tree was dirty) nothing is restored at all: repairing
    a path this capture never wrote would destroy the hand-edit that caused
    the failure. Either way, the branch is restored to `main` on exit so the
    shared working tree is never left parked on a provider branch
    (REVIEW.md #3 partial)."""
    with brain_lock(brain_dir):
        rel_path = block["file"]
        denial = check_ownership(brain_dir, provider, trust, rel_path)
        if denial:
            raise OwnershipError(denial)

        branch = f"provider/{provider}"
        # Set the instant before the first mutation, and only then: the
        # cleanup below must be a no-op for a capture that never wrote
        # anything, otherwise it repairs a path it did not dirty.
        touched = False
        in_head = False
        try:
            git(brain_dir, "checkout", branch)
            abs_path = os.path.join(brain_dir, rel_path)
            in_head = _path_in_head(brain_dir, rel_path)

            if block["action"] == "delete":
                name = os.path.splitext(os.path.basename(rel_path))[0]
                if not os.path.exists(abs_path):
                    raise RuntimeError(
                        f"delete target does not exist under brain_dir: {rel_path}"
                    )
                # AFTER the rm, not before: `git rm` fails at pathspec-match
                # (mutating nothing) when the target exists on disk but is
                # untracked — a session's hand-created file. Flagging it as
                # touched would send the cleanup down the not-in-HEAD branch
                # and delete that file, which the old handler never did.
                git(brain_dir, "rm", "-f", rel_path)
                touched = True
            else:
                os.makedirs(os.path.dirname(abs_path) or brain_dir, exist_ok=True)
                # BEFORE the open, which truncates on entry.
                touched = True
                with open(abs_path, "w", encoding="utf-8") as fh:
                    fh.write(block["body"])
                git(brain_dir, "add", rel_path)
                fm, _ = parse_frontmatter(block["body"])
                name = fm.get("name") if fm else os.path.splitext(os.path.basename(rel_path))[0]

            if is_noop_commit(brain_dir):
                return "skipped: no change"

            ts = datetime.now().isoformat(timespec="seconds")
            msg = (
                f"brain(capture): {name} via {provider} [inbox]\n\n"
                f"Provider: {provider}\n"
                f"Trust: {trust}\n"
                f"Action: {block['action']}\n"
                f"File: {rel_path}\n"
                f"Ingested-At: {ts}\n"
            )
            git(brain_dir, "commit", "-m", msg)
            return "committed"
        except Exception as exc:
            # Leave OUR path clean rather than poisoning the next
            # capture/merge with a half-staged change, then let the caller
            # map this to a real quarantine (not a silent loss). Everything
            # else in the shared working tree is none of our business.
            if touched:
                try:
                    _restore_capture_path(brain_dir, rel_path, in_head)
                except Exception as cleanup_exc:
                    raise CleanupError(
                        f"capture of {rel_path} failed ({exc}) AND the scoped "
                        f"cleanup failed ({cleanup_exc}); that path may still "
                        f"hold a half-staged change. Refusing to fall back to "
                        f"wiping the working tree — resolve {rel_path} by hand."
                    ) from exc
            raise
        finally:
            # Never leave the shared working tree parked on a provider
            # branch (REVIEW.md #3 partial).
            git(brain_dir, "checkout", "main", check=False)


# --- CLI -----------------------------------------------------------------------

def default_brain_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="Scan-before-commit capture-inbox ingest.")
    parser.add_argument("provider", choices=PROVIDERS)
    parser.add_argument("block_file")
    parser.add_argument("--brain-dir", default=None,
                         help="Brain repo root (default: inferred from this script's location)")
    parser.add_argument("--trust", choices=("local", "cloud"), default="cloud",
                         help="Trust tier of the calling credential (docs/visibility-design.md). "
                              "Defaults to 'cloud' if omitted -- fail closed, never silently "
                              "grant the unrestricted 'local' tier.")
    args = parser.parse_args()

    brain_dir = args.brain_dir or default_brain_dir()

    with open(args.block_file, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    block, err = parse_block(raw)
    if err:
        quarantine(brain_dir, args.provider, args.block_file, quarantine_reason(err), err)
        sys.exit(1)

    if block["inferred"]:
        # Say so on stdout: an inferred path is a decision the emitter did not
        # make, and the MCP tool relays this text back to the caller.
        print(f"INFERRED {', '.join(block['inferred'])} from frontmatter: "
              f"file=\"{block['file']}\" action=\"{block['action']}\"")

    schema_err = validate_schema(block)
    if schema_err:
        quarantine(brain_dir, args.provider, args.block_file, "schema-invalid", schema_err)
        sys.exit(1)

    secret_hit = scan_secrets(block["raw"])
    if secret_hit:
        masked = secret_hit[:6] + "…" if len(secret_hit) > 6 else secret_hit
        quarantine(brain_dir, args.provider, args.block_file, "secret-scan",
                   f"matched a secret pattern: {masked}")
        sys.exit(1)

    imp_hit = scan_imperative(block["raw"])
    if imp_hit:
        quarantine(brain_dir, args.provider, args.block_file, "imperative-scan",
                   f"unattributed standing instruction: \"{imp_hit}\"")
        sys.exit(1)

    try:
        result = commit_block(brain_dir, args.provider, block, args.trust)
    except OwnershipError as e:
        quarantine(brain_dir, args.provider, args.block_file, "ownership-denied", str(e))
        sys.exit(1)
    except Exception as e:
        quarantine(brain_dir, args.provider, args.block_file, "git-error", str(e))
        sys.exit(1)

    if result == "skipped: no change":
        print(f"SKIPPED: {block['file']} ({block['action']}) -> provider/{args.provider} (no change)")
    else:
        print(f"COMMITTED: {block['file']} ({block['action']}) -> provider/{args.provider}")


if __name__ == "__main__":
    main()
