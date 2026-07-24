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
  5. Commit   — on pass: checkout provider/<name>, write the file, commit with
                structured metadata.
  6. Quarantine — on any failure: copy to hub/quarantine/<provider>/<date>-<file>,
                  append a digest entry, exit nonzero. Never commit to any branch.

CLI:
    python3 hub/inbox_ingest.py <provider> <block-file> [--brain-dir PATH]
    # provider: one of "chatgpt", "claude", "openclaw"
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

_FALLBACK_PROVIDERS = ("chatgpt", "claude", "openclaw")


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
ITEM_TYPES = {"user", "feedback", "project", "reference", "knowledge"}
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
INDEX_LINE_RE = re.compile(r"^INDEX:\s*(.*)$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


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


def parse_block(raw_text):
    text = strip_fence(raw_text)
    m = MEMORY_RE.search(text)
    if not m:
        return None, "parse-error: no <MEMORY file=\"…\" action=\"…\">…</MEMORY> block found"
    body = m.group("body")
    if body.startswith("\n"):
        body = body[1:]
    idx_m = INDEX_LINE_RE.search(text)
    if not idx_m:
        return None, "parse-error: no trailing INDEX: line found"
    block = {
        "file": m.group("file"),
        "action": m.group("action"),
        "body": body,
        "index_line": idx_m.group(0),
        "raw": text,
    }
    return block, None


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

    return None


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


def commit_block(brain_dir, provider, block):
    """Commit `block` to provider/<provider>. Returns "committed" or
    "skipped: no change" (identical content re-captured — not a failure).

    Wrapped in an exclusive repo lock (REVIEW.md #5) so this can never
    interleave with brain_merge.py's git mutations. On ANY git failure
    (including a timeout raised by git()) the working tree is restored to
    clean (`git checkout -- .` + `git reset`) before the exception
    propagates, so a failed capture never poisons the next capture or the
    nightly merge with a half-staged change (REVIEW.md #13). Either way, the
    branch is restored to `main` on exit so the shared working tree is never
    left parked on a provider branch (REVIEW.md #3 partial)."""
    with brain_lock(brain_dir):
        branch = f"provider/{provider}"
        try:
            git(brain_dir, "checkout", branch)
            rel_path = block["file"]
            abs_path = os.path.join(brain_dir, rel_path)

            if block["action"] == "delete":
                name = os.path.splitext(os.path.basename(rel_path))[0]
                if not os.path.exists(abs_path):
                    raise RuntimeError(
                        f"delete target does not exist under brain_dir: {rel_path}"
                    )
                git(brain_dir, "rm", "-f", rel_path)
            else:
                os.makedirs(os.path.dirname(abs_path) or brain_dir, exist_ok=True)
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
                f"Action: {block['action']}\n"
                f"File: {rel_path}\n"
                f"Ingested-At: {ts}\n"
            )
            git(brain_dir, "commit", "-m", msg)
            return "committed"
        except Exception:
            # Leave the tree clean rather than poisoning the next
            # capture/merge with a half-staged change, then let the caller
            # map this to a real quarantine (not a silent loss).
            git(brain_dir, "checkout", "--", ".", check=False)
            git(brain_dir, "reset", check=False)
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
    args = parser.parse_args()

    brain_dir = args.brain_dir or default_brain_dir()

    with open(args.block_file, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    block, err = parse_block(raw)
    if err:
        quarantine(brain_dir, args.provider, args.block_file, "parse-error", err)
        sys.exit(1)

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
        result = commit_block(brain_dir, args.provider, block)
    except Exception as e:
        quarantine(brain_dir, args.provider, args.block_file, "git-error", str(e))
        sys.exit(1)

    if result == "skipped: no change":
        print(f"SKIPPED: {block['file']} ({block['action']}) -> provider/{args.provider} (no change)")
    else:
        print(f"COMMITTED: {block['file']} ({block['action']}) -> provider/{args.provider}")


if __name__ == "__main__":
    main()
