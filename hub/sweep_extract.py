#!/usr/bin/env python3
"""
hub/sweep_extract.py — deterministic session-log capture candidates (S3 mechanical).

Parses assistant+user turns from provider session logs and emits emit-grammar v1
<MEMORY> blocks for high-precision patterns only (precision over recall).

Session log locations (discovered on this host):
  - Claude Code: ~/.claude/projects/**/*.jsonl (assistant + user turns; skips subagents/)
  - Codex: ~/.codex/sessions/**/*.jsonl (rollout session logs; ~/.codex/history.jsonl
    is a lightweight index, not full turns — skipped)
  - OpenClaw: ~/.openclaw/agents/*/sessions/*.jsonl (plain session files; *.trajectory.jsonl
    are auxiliary traces and are skipped)

Output: emit-grammar v1 blocks to stdout, then one JSON summary line on stderr.
No git operations, no writes anywhere.

CLI:
    python3 hub/sweep_extract.py [--since EPOCH] [--window-days N]
"""

import argparse
import glob
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")

CLAUDE_ROOT = os.path.join(HOME, ".claude", "projects")
CODEX_ROOT = os.path.join(HOME, ".codex", "sessions")
OPENCLAW_ROOT = os.path.join(HOME, ".openclaw", "agents")

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Same secret-regex set as inbox_ingest.py / brain_merge.py (duplicated on purpose).
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9-]{20,}",
    r"ghp_[A-Za-z0-9]{36}",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"gh[oprsu]_[A-Za-z0-9]{36,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"AIza[0-9A-Za-z_\-]{35}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
    r"(postgres|mysql|mongodb(\+srv)?|redis|amqp)://[^\s]+:[^\s]+@",
]

# The verb alone is not the signal — "save any outstanding work" and "worth saving"
# are ordinary task talk. An explicit save names what to keep: a demonstrative object
# or a colon. Without that object the pattern matched every message containing the
# word "save" (21 of 31 candidates in a 14-day live run).
_SAVE_VERB = r"(?:remember|save|note|keep\s+in\s+mind|don'?t\s+forget)"
EXPLICIT_SAVE_RE = re.compile(
    r"(?i)(?:^|\s)(?:please\s+)?(?:make\s+sure\s+(?:you\s+)?)?"
    rf"{_SAVE_VERB}\s*(?::|(?:this|that|the\s+following)\b)",
)

DECISION_RE = re.compile(
    r"(?i)(?:^|\s)(?:we\s+decided|ruling\s*:|decision\s*:)",
)

CORRECTION_RE = re.compile(
    r"(?i)(?:that'?s\s+(?:wrong|incorrect|not\s+(?:right|true|correct))|"
    r"(?:^|\s)no\s*[,.—-]\s*actually|"
    r"\bincorrect\b|"
    r"(?:is\s+wrong|was\s+wrong|not\s+correct))",
)

# A quoted *span*, not any quote character — apostrophes in "that's wrong" would
# otherwise satisfy the "user quoted what was wrong" requirement on their own.
QUOTE_RE = re.compile(r"[\"“”][^\"“”\n]{2,}[\"“”]")

META_SKIP_RE = re.compile(
    r"(?i)<local-command-caveat>|^<permissions\s+instructions>|^<recommended_plugins>",
)

# Harness-injected turns arrive in the log with role=user but were never typed by the
# human: skill bodies, slash-command payloads, cron prompts, hook and task notifications.
# They are long documents full of words like "remember" and "decision", so without this
# filter they dominate the output — a 3-day live run produced 15 candidates, all of them
# injected text and none of them a real user statement.
SYNTHETIC_MARKERS = (
    "base directory for this skill:",
    "<system-reminder>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "<task-notification>",
    "<user-prompt-submit-hook>",
    "<permissions instructions>",
    "<recommended_plugins>",
    "caveat: the messages below were generated",
    "[cron:",
    "this session is being continued from a previous",
    "stop hook feedback:",
    "write a dream diary entry",
    # OpenClaw injects gateway/system notices as role=user prompts prefixed "[System] ".
    # Caught in the wild 2026-08-06: a full-history dry run promoted
    # "[System] Your previous turn was interrupted by a gateway restart..." into a
    # candidate memory ABOUT THE USER. Match the generic prefix rather than that one
    # sentence, so the whole class is filtered instead of a single instance.
    "[system]",
)

# Agent-to-agent dispatch briefs land in openclaw session logs as role=user and open
# by assigning a role. A human never starts a memory statement that way.
DISPATCH_PREFIXES = ("you are ", "you're ", "your task is")

# A typed "remember this…" is short. Anything longer is a pasted document or an
# injected payload; dropping it costs recall we do not want and buys precision we do.
MAX_CANDIDATE_CHARS = 1200


# Every pattern above is anchored to a KNOWN shape — a vendor prefix (`sk-`, `ghp_`,
# `AKIA`) or one of four keywords before a colon. A credential wearing neither slips
# through, and one did: a 43-character setup code pasted after the words "send me this on
# telegram:" survived into three corpus files during the 2026-08-13 knowledge grab, and
# from there into a session transcript.
#
# Adding "telegram" to the keyword list would fix that one sentence and nothing else. The
# general shape is what matters: a long, high-entropy, mixed-alphabet token is not
# something a human types in prose.
#
# The guards below exist to keep this from becoming a false-positive machine, because a
# redactor that mangles ordinary text gets switched off:
#   - length >= 28, so ordinary words and short identifiers are untouched
#   - must mix lower, upper AND digit, which excludes git SHAs and UUIDs (lower+digit
#     only) — those are not secrets and redacting them would corrupt real content
#   - Shannon entropy >= 3.5 bits/char, which excludes long repetitive strings
_HIGH_ENTROPY_CANDIDATE = re.compile(r"\b[A-Za-z0-9_\-]{28,}\b")


def _shannon_bits_per_char(s):
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_like_secret(token):
    """True for a token whose shape says 'machine-generated credential', not 'word'."""
    if len(token) < 28:
        return False
    if not (any(c.islower() for c in token)
            and any(c.isupper() for c in token)
            and any(c.isdigit() for c in token)):
        return False
    return _shannon_bits_per_char(token) >= 3.5


def redact_secrets(text):
    out = text
    for pat in SECRET_PATTERNS:
        out = re.sub(pat, "[REDACTED]", out)
    out = _HIGH_ENTROPY_CANDIDATE.sub(
        lambda m: "[REDACTED]" if looks_like_secret(m.group(0)) else m.group(0), out)
    return out


def normalize_body(text):
    safe = text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
    return re.sub(r"\s+", " ", safe.strip().lower())


def content_fingerprint(body):
    return hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()


def slug_from_text(text, prefix="sweep"):
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = "-".join(words[:6])[:48].strip("-")
    if not slug or not KEBAB_RE.match(slug):
        slug = prefix
    return slug


def infer_type(kind, text):
    if kind == "decision":
        return "decision"
    if kind == "correction":
        return "feedback"
    return "user"


def parse_iso_ts(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def extract_text_claude(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def extract_text_openclaw(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
        return "\n".join(parts)
    return ""


def extract_text_codex(payload):
    content = payload.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for item in content or []:
        if isinstance(item, dict):
            if item.get("type") in ("input_text", "output_text", "text"):
                parts.append(item.get("text", ""))
    return "\n".join(parts)


def discover_claude_logs():
    pattern = os.path.join(CLAUDE_ROOT, "**", "*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        if "/subagents/" in path.replace("\\", "/"):
            continue
        yield path


def discover_codex_logs():
    if not os.path.isdir(CODEX_ROOT):
        return
    pattern = os.path.join(CODEX_ROOT, "**", "*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        yield path


def discover_openclaw_logs():
    pattern = os.path.join(OPENCLAW_ROOT, "*", "sessions", "*.jsonl")
    for path in glob.glob(pattern):
        if ".trajectory." in path:
            continue
        yield path


def iter_claude_turns(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Sidechain turns are subagent traffic, isMeta turns are harness notices,
            # and a turn carrying toolUseResult is a tool result wearing role=user.
            if obj.get("isSidechain") or obj.get("isMeta") or obj.get("toolUseResult"):
                continue
            role = None
            text = ""
            ts = parse_iso_ts(obj.get("timestamp"))
            if obj.get("type") == "user":
                role = "user"
                text = extract_text_claude(obj.get("message", {}))
            elif obj.get("type") == "assistant":
                role = "assistant"
                text = extract_text_claude(obj.get("message", {}))
            if role and text:
                yield role, text, ts, path


def iter_codex_turns(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload", {})
            if payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = extract_text_codex(payload)
            ts = parse_iso_ts(obj.get("timestamp"))
            if text:
                yield role, text, ts, path


def iter_openclaw_turns(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "message":
                continue
            message = obj.get("message", {})
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue
            text = extract_text_openclaw(message)
            ts = parse_iso_ts(obj.get("timestamp") or message.get("timestamp"))
            if text:
                yield role, text, ts, path


def provider_for_path(path):
    norm = path.replace("\\", "/").lower()
    if "/.claude/" in norm or "/sweep/claude" in norm:
        return "claude"
    if "/.codex/" in norm or "/sweep/codex" in norm:
        return "codex"
    if "/.openclaw/" in norm or "/sweep/openclaw" in norm:
        return "openclaw"
    return "unknown"


def is_synthetic_turn(text):
    """True for harness-injected role=user turns the human never typed."""
    head = text[:400].lower()
    if head.startswith(DISPATCH_PREFIXES):
        return True
    return any(marker in head for marker in SYNTHETIC_MARKERS)


def classify_user_text(text, prior_assistant=""):
    if META_SKIP_RE.search(text[:200]):
        return None
    body = text.strip()
    if len(body) < 12:
        return None
    if len(body) > MAX_CANDIDATE_CHARS:
        return None
    if is_synthetic_turn(body):
        return None
    if DECISION_RE.search(body):
        return "decision", body
    if EXPLICIT_SAVE_RE.search(body):
        return "explicit_save", body
    # Hard limit: corrections count only when the user quotes what was wrong.
    if CORRECTION_RE.search(body) and QUOTE_RE.search(body) and prior_assistant.strip():
        return "correction", body
    return None


def build_emit_block(kind, body, provider, captured_date, slug=None):
    typ = infer_type(kind, body)
    fp = content_fingerprint(body)
    # Two different candidates that open with the same words used to produce the same
    # slug — and therefore the same memories/<slug>.md path — so the later block
    # silently clobbered the earlier one. The fingerprint suffix makes the name unique
    # per candidate and keeps a re-run of the same content on the same filename.
    slug = slug or f"{typ}-{slug_from_text(body, prefix=kind.replace('_', '-'))}-{fp[:6]}"
    description = body.split("\n", 1)[0][:120].strip()
    if len(description) > 117:
        description = description[:117] + "..."
    fm_lines = [
        "---",
        f"name: {slug}",
        f"description: {description}",
        f"type: {typ}",
        f"source: {provider}",
        f"captured: {captured_date}",
        "---",
        body,
    ]
    if typ in ("feedback", "project"):
        if "**Why:**" not in body:
            fm_lines.append("")
            fm_lines.append("**Why:** Captured by deterministic sweep.")
            fm_lines.append("**How to apply:** Review and fold into the right memory.")
    memory_body = "\n".join(fm_lines)
    block = (
        f"<MEMORY file=\"memories/{slug}.md\" action=\"new\">\n"
        f"{memory_body}\n"
        f"</MEMORY>\n"
        f"INDEX: - [[{slug}]] — {description}  ({typ})"
    )
    return block, fp, slug


def scan_logs(since_ts, window_days, extra_paths=None, paths_only=False):
    now = datetime.now(timezone.utc).timestamp()
    if window_days is not None:
        since_ts = max(since_ts or 0, now - window_days * 86400)
    sources = list(extra_paths or [])
    if not paths_only:
        for discover in (discover_claude_logs, discover_codex_logs, discover_openclaw_logs):
            for path in discover():
                sources.append(path)

    candidates = []
    seen_fp = set()
    prior_assistant = ""

    for path in sources:
        provider = provider_for_path(path)
        if provider == "claude":
            turns = iter_claude_turns(path)
        elif provider == "codex":
            turns = iter_codex_turns(path)
        elif provider == "openclaw":
            turns = iter_openclaw_turns(path)
        else:
            continue

        for role, text, ts, log_path in turns:
            if since_ts and ts and ts < since_ts:
                prior_assistant = ""
                continue
            text = redact_secrets(text)
            if role == "assistant":
                prior_assistant = text
                continue
            classified = classify_user_text(text, prior_assistant)
            prior_assistant = ""
            if not classified:
                continue
            kind, body = classified
            body = redact_secrets(body)
            if scan_secrets_raw(body):
                continue
            captured = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                if ts
                else datetime.now(timezone.utc).strftime("%Y-%m-%d")
            )
            block, fp, slug = build_emit_block(kind, body, provider, captured)
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            candidates.append(
                {
                    "kind": kind,
                    "provider": provider,
                    "slug": slug,
                    "fingerprint": fp,
                    "log": log_path,
                    "block": block,
                }
            )
    return candidates


def scan_secrets_raw(text):
    for pat in SECRET_PATTERNS:
        if re.search(pat, text):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Deterministic session-log capture sweep")
    parser.add_argument("--since", type=float, default=None, help="Only turns at or after EPOCH seconds")
    parser.add_argument("--window-days", type=int, default=None, help="Rolling window from now (days)")
    args = parser.parse_args()

    candidates = scan_logs(args.since, args.window_days)
    for c in candidates:
        print(c["block"])
        print()

    summary = {
        "candidates": len(candidates),
        "by_kind": {},
        "by_provider": {},
        "fingerprints": [c["fingerprint"] for c in candidates],
    }
    for c in candidates:
        summary["by_kind"][c["kind"]] = summary["by_kind"].get(c["kind"], 0) + 1
        summary["by_provider"][c["provider"]] = summary["by_provider"].get(c["provider"], 0) + 1
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
