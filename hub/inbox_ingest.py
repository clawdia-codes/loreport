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
  4. Imperative-scan (provenance rule, mechanical) — a standing instruction aimed
     at the assistant, not attributed to a source; any hit -> quarantine.
  5. Commit   — on pass: checkout provider/<name>, write the file, commit with
                structured metadata.
  6. Quarantine — on any failure: copy to hub/quarantine/<provider>/<date>-<file>,
                  append a digest entry, exit nonzero. Never commit to any branch.

CLI:
    python3 hub/inbox_ingest.py <provider> <block-file> [--brain-dir PATH]
    # provider: one of "chatgpt", "claude", "openclaw"
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date, datetime

PROVIDERS = ("chatgpt", "claude", "openclaw")
ITEM_TYPES = {"user", "feedback", "project", "reference", "knowledge"}
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Identical secret-regex set to brain_merge.py / snapshot_publish.py (duplicated on
# purpose: every hub/*.py file is single-file and stdlib-only; nothing is shared by
# import between them).
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9-]{20,}",
    r"ghp_[A-Za-z0-9]{36}",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
]

# The provenance rule, enforced mechanically: a line addressing the assistant with
# a standing directive ("remember", "always", "never", "from now on", "ignore",
# "disregard"), unless the line is explicitly attributed to a source (quoting what
# the source says, rather than obeying it).
IMPERATIVE_TRIGGER_RE = re.compile(r"(?i)\b(remember|always|never|from\s+now\s+on|ignore|disregard)\b")
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
    return None


def scan_secrets(text):
    for pat in SECRET_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return None


def scan_imperative(body):
    for line in body.splitlines():
        if ATTRIBUTED_PREFIX_RE.match(line):
            continue
        if IMPERATIVE_TRIGGER_RE.search(line):
            return line.strip()
    return None


# --- git plumbing --------------------------------------------------------------

def git(brain_dir, *args, check=True):
    result = subprocess.run(["git", "-C", brain_dir] + list(args), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


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
    branch = f"provider/{provider}"
    git(brain_dir, "checkout", branch)
    rel_path = block["file"]
    abs_path = os.path.join(brain_dir, rel_path)

    if block["action"] == "delete":
        name = os.path.splitext(os.path.basename(rel_path))[0]
        if os.path.exists(abs_path):
            git(brain_dir, "rm", "-f", rel_path)
        else:
            os.makedirs(os.path.dirname(abs_path) or brain_dir, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(abs_path) or brain_dir, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(block["body"])
        git(brain_dir, "add", rel_path)
        fm, _ = parse_frontmatter(block["body"])
        name = fm.get("name") if fm else os.path.splitext(os.path.basename(rel_path))[0]

    ts = datetime.now().isoformat(timespec="seconds")
    msg = (
        f"brain(capture): {name} via {provider} [inbox]\n\n"
        f"Provider: {provider}\n"
        f"Action: {block['action']}\n"
        f"File: {rel_path}\n"
        f"Ingested-At: {ts}\n"
    )
    git(brain_dir, "commit", "-m", msg)


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

    imp_hit = scan_imperative(block["body"])
    if imp_hit:
        quarantine(brain_dir, args.provider, args.block_file, "imperative-scan",
                   f"unattributed standing instruction: \"{imp_hit}\"")
        sys.exit(1)

    commit_block(brain_dir, args.provider, block)
    print(f"COMMITTED: {block['file']} ({block['action']}) -> provider/{args.provider}")


if __name__ == "__main__":
    main()
