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

EXPLICIT_SAVE_RE = re.compile(
    r"(?i)(?:^|\s)(?:please\s+)?(?:remember|save|don'?t\s+forget|keep\s+in\s+mind|"
    r"make\s+sure\s+(?:you\s+)?remember|note\s+that)\s+(?:this|that|:|\b)",
)

DECISION_RE = re.compile(
    r"(?i)(?:^|\s)(?:we\s+decided|ruling\s*:|decision\s*:)",
)

CORRECTION_RE = re.compile(
    r"(?i)(?:that'?s\s+(?:wrong|incorrect|not\s+(?:right|true|correct))|"
    r"(?:^|\s)no\s*[,.—-]\s*actually|"
    r"(?:^|\s)actually\s*[,.—-]|"
    r"\bincorrect\b.*[\"'“”`]|"
    r"[\"'“”`].*(?:is\s+wrong|was\s+wrong|not\s+correct))",
)

META_SKIP_RE = re.compile(
    r"(?i)<local-command-caveat>|^<permissions\s+instructions>|^<recommended_plugins>",
)


def redact_secrets(text):
    out = text
    for pat in SECRET_PATTERNS:
        out = re.sub(pat, "[REDACTED]", out)
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


def classify_user_text(text, prior_assistant=""):
    if META_SKIP_RE.search(text[:200]):
        return None
    if len(text.strip()) < 12:
        return None
    if DECISION_RE.search(text):
        return "decision", text.strip()
    if EXPLICIT_SAVE_RE.search(text):
        return "explicit_save", text.strip()
    if CORRECTION_RE.search(text) and (
        re.search(r"[\"'“”`]", text) or prior_assistant.strip()
    ):
        return "correction", text.strip()
    return None


def build_emit_block(kind, body, provider, captured_date, slug=None):
    slug = slug or slug_from_text(body, prefix=kind.replace("_", "-"))
    typ = infer_type(kind, body)
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
    fp = content_fingerprint(body)
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
