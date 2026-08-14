#!/usr/bin/env python3
"""
hub/export_readers.py — Pass 0 input adapters for provider *exports*.

`hub/corpus_prep.py` builds Pass 0 records from LOCAL session logs (claude, codex,
openclaw). ChatGPT and Claude.ai have no local logs — they are only reachable through a
downloaded export archive. This module reads those archives and emits the SAME record
shape, so Pass 1 (`observe_extract.py`) and everything downstream consume them unchanged.

    {"conversation_id": …, "source": "chatgpt"|"claude-ai", "date": "YYYY-MM-DD",
     "log": <archive-relative path>, "turn_count": N, "turns": [{"ts":…, "text":…}, …]}

WHY THIS IS NOT AN ADDITION TO sweep_extract.py
------------------------------------------------
KNOWLEDGE-GRAB-PLAN §4c-6b says to add these providers to `sweep_extract.py` and
`SWEEP_PROVIDERS`. That text predates the three-pass funnel. The sweep is deliberately
high-precision and deterministic — it matches explicit `<MEMORY>` blocks and decision
phrasing — and a provider export contains none of those, because that syntax is a Loreport
convention used in Claude Code and openclaw. Routing exports through the sweep would
extract approximately nothing, which is consistent with the plan's own finding that the
nightly sweep "has been running since S3 producing ~nothing". Exports feed Pass 0.

THE ALLOWLIST IS THE SAFETY PROPERTY
-------------------------------------
The OpenAI download is a *Privacy Center* archive, not a ChatGPT export. Alongside the
conversations it contains `Financial/Charge History.csv`, `Financial/Payments
Invoices.csv`, `User Profile/Payments Customer Profile.csv`, `Contact Info/Contact
Info.csv`, `User Online Activity/Emails sent.csv`, a 196 MB `Files__*.zip` of uploaded
files, and an `Ads__*.zip`.

None of that may enter the pipeline. The user's standing boundary is that finances are
`visibility: local` and never reach the shared tier.

So this reader **names the one member it opens** and cannot see any other. That is an
allowlist, not a filter: a filter is a claim you must keep true as the input changes, and
a new `Financial/` filename in next year's export would silently slip past it. Anything
not explicitly named here is excluded by construction. `tests/test_export_readers.py`
asserts a financial CSV never reaches a record.

Nothing here writes to the brain, touches git, or contacts a model.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse the sweep's scrubbing and fingerprinting rather than reimplementing them. A second
# redaction implementation would drift from the one every other ingress path uses.
import sweep_extract as sx  # noqa: E402

SOURCES = ("chatgpt", "claude-ai")

# --- the allowlist -----------------------------------------------------------------
# The ONLY outer members this module will ever open. Everything else in the Privacy
# Center archive — Financial/, Contact Info/, User Profile/, Files__*, Ads__* — is
# unreachable because it is not named here.
CHATGPT_OUTER_PREFIX = "User Online Activity/Conversations__"
CHATGPT_OUTER_SUFFIX = ".zip"
CHATGPT_SHARD_RE = re.compile(r"^conversations-\d+\.json$")
CLAUDE_MEMBER = "conversations.json"


def chatgpt_allowed_outer_members(names):
    """The outer-archive members this reader may open. Exactly the conversation archives.

    Split out as a named function so the test can assert the *policy* directly rather
    than inferring it from behaviour.
    """
    return [n for n in names
            if n.startswith(CHATGPT_OUTER_PREFIX) and n.endswith(CHATGPT_OUTER_SUFFIX)]


def _iso_date_from_epoch(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _iso_date_from_string(s):
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s))
    return m.group(1) if m else None


# --- conversation-level exclusion policy --------------------------------------------

def should_skip_conversation(meta):
    """Return (True, reason) to drop a whole conversation before any turn is extracted.

    `meta` is a normalized dict describing one conversation, identical in shape for both
    providers so the policy reads the same either way:

        {"source":      "chatgpt" | "claude-ai",
         "title":       str,            # conversation title/name, may be ""
         "date":        "YYYY-MM-DD" | None,
         "turn_count":  int,            # genuine user turns found
         "do_not_remember": bool,       # ChatGPT's per-chat flag; always False for claude-ai
         "archived":    bool,           # always False for claude-ai
         "starred":     bool}           # always False for claude-ai

    Measured against the real corpus (2026-08-13): 1,108 ChatGPT conversations, of which
    0 have do_not_remember set, 5 are archived; 68 Claude.ai conversations. Date span is
    2023-04-12 to 2026-08-10, so this also decides whether three-year-old material counts.

    Returning (False, None) keeps the conversation.
    """
    # Honour the user's own instruction, and nothing else.
    #
    # `do_not_remember` is not a judgement about whether a conversation is *valuable* —
    # it is the user having told the provider not to remember it. Carrying that intent
    # across into a permanent memory store is the whole reason this gate exists, and it
    # costs one branch. It is currently set on 0 of 1,108 conversations; the check is
    # here so a future export cannot slip one past.
    if meta.get("do_not_remember"):
        return True, "do_not_remember"

    # Deliberately NOT filtered here: short conversations, old conversations, archived
    # ones. corpus_prep.py's doctrine is that Pass 0 is high-recall and "exercises no
    # judgment ... Mixing those concerns here is how a corpus stage quietly becomes a
    # second, worse extractor." A turn-count floor or a date cutoff would be exactly that
    # — a value judgement made blind, before any model has read the text, and invisible
    # afterwards. Stability and staleness are Pass 2's job, where they can be measured
    # against the whole corpus instead of guessed at one conversation at a time.
    return False, None


# --- ChatGPT ------------------------------------------------------------------------

CONTEXT_CHARS = 4000


def _msg_text(msg):
    content = msg.get("content") or {}
    if content.get("content_type") not in ("text", "multimodal_text"):
        return ""
    return "\n".join(p for p in (content.get("parts") or []) if isinstance(p, str)).strip()


def _chatgpt_preceding_assistant(mapping, node_id):
    """Text of the nearest assistant turn ABOVE this user turn in the reply tree.

    Walks `parent` rather than scanning by timestamp, because the mapping is a tree: with
    edits and regenerations the chronologically previous assistant message is often on a
    different branch and is not what the user was replying to.
    """
    seen = set()
    cur = (mapping.get(node_id) or {}).get("parent")
    while cur and cur not in seen:
        seen.add(cur)
        node = mapping.get(cur) or {}
        msg = node.get("message") or {}
        if (msg.get("author") or {}).get("role") == "assistant":
            text = _msg_text(msg)
            if text:
                return text[:CONTEXT_CHARS]
        cur = node.get("parent")
    return ""


def _chatgpt_user_turns(conv, include_context=False):
    """Genuine typed user turns from one ChatGPT conversation's mapping tree.

    Kept: author.role == "user", content_type in {"text", "multimodal_text"}, string
    parts only. `multimodal_text` matters — 1,659 messages in this corpus are multimodal,
    and the text the user typed beside an image lives in that message's string parts.
    Dropping it would silently lose every image-bearing turn.

    Excluded: `metadata.is_user_system_message` (custom instructions, not a typed turn)
    and non-string parts (image asset pointers).

    `include_context` additionally attaches, per turn, the assistant text that turn was
    replying to. It stays OFF by default and the attached text is NEVER treated as
    something the user said — see the module docstring's note on approval-anchored
    extraction. Measured on this corpus: the assistant wrote 9x more than the user, and
    34% of user turns are 25 characters or fewer ("yes do that"), so for a third of the
    corpus the user's own words carry a verdict and no content at all.
    """
    mapping = conv.get("mapping") or {}
    turns = []
    for node_id, node in mapping.items():
        msg = node.get("message")
        if not msg:
            continue
        if (msg.get("author") or {}).get("role") != "user":
            continue
        if (msg.get("metadata") or {}).get("is_user_system_message"):
            continue
        text = _msg_text(msg)
        if not text:
            continue
        turn = {"ts": msg.get("create_time"), "text": sx.redact_secrets(text)}
        if include_context:
            ctx = _chatgpt_preceding_assistant(mapping, node_id)
            turn["context"] = sx.redact_secrets(ctx) if ctx else ""
        turns.append(turn)
    turns.sort(key=lambda t: (t["ts"] is None, t["ts"]))
    return turns


def read_chatgpt_export(zip_path, limit=None, include_context=False):
    records, skipped = [], {}
    with zipfile.ZipFile(zip_path) as outer:
        allowed = chatgpt_allowed_outer_members(outer.namelist())
        if not allowed:
            raise ValueError(
                f"no conversation archive in {zip_path}: expected a member starting "
                f"{CHATGPT_OUTER_PREFIX!r}"
            )
        for member in allowed:
            with zipfile.ZipFile(outer.open(member)) as inner:
                shards = sorted(n for n in inner.namelist() if CHATGPT_SHARD_RE.match(n))
                for shard in shards:
                    for conv in json.loads(inner.read(shard)):
                        turns = _chatgpt_user_turns(conv, include_context)
                        if not turns:
                            skipped["no_user_turns"] = skipped.get("no_user_turns", 0) + 1
                            continue
                        date = _iso_date_from_epoch(conv.get("create_time"))
                        skip, reason = should_skip_conversation({
                            "source": "chatgpt",
                            "title": conv.get("title") or "",
                            "date": date,
                            "turn_count": len(turns),
                            "do_not_remember": bool(conv.get("is_do_not_remember")),
                            "archived": bool(conv.get("is_archived")),
                            "starred": bool(conv.get("is_starred")),
                        })
                        if skip:
                            skipped[reason or "policy"] = skipped.get(reason or "policy", 0) + 1
                            continue
                        cid = conv.get("conversation_id") or conv.get("id") or shard
                        records.append({
                            "conversation_id": hashlib.sha1(
                                str(cid).encode("utf-8")).hexdigest()[:16],
                            "source": "chatgpt",
                            "date": date,
                            "log": f"{member}!{shard}",
                            "turn_count": len(turns),
                            "turns": turns,
                        })
                        if limit and len(records) >= limit:
                            return records, skipped
    return records, skipped


# --- Claude.ai ----------------------------------------------------------------------

def _claude_user_turns(conv, include_context=False):
    """Genuine typed human turns from one Claude.ai conversation.

    Kept: sender == "human". Text is taken from `.text`, falling back to the `text`-typed
    blocks of `.content[]`.

    Excluded: `thinking`, `tool_use` and `tool_result` content blocks, and `attachments` /
    `files`. Those are not things the human typed — and a tool result wearing a user turn
    is exactly the attribution error that once promoted a gateway restart notice into a
    candidate memory about the user.
    """
    turns = []
    pending_context = ""
    for msg in conv.get("chat_messages") or []:
        if msg.get("sender") != "human":
            # Remember the most recent assistant text so the next human turn can carry
            # what it was replying to. chat_messages is a flat ordered list, so the
            # previous assistant message IS the one being answered.
            atext = (msg.get("text") or "").strip() or "\n".join(
                b.get("text", "") for b in (msg.get("content") or [])
                if b.get("type") == "text").strip()
            if atext:
                pending_context = atext[:CONTEXT_CHARS]
            continue
        text = (msg.get("text") or "").strip()
        if not text:
            text = "\n".join(
                b.get("text", "") for b in (msg.get("content") or [])
                if b.get("type") == "text"
            ).strip()
        if not text:
            continue
        turn = {
            "ts": msg.get("created_at"),
            "text": sx.redact_secrets(text),
        }
        if include_context:
            turn["context"] = sx.redact_secrets(pending_context) if pending_context else ""
        turns.append(turn)
    turns.sort(key=lambda t: (t["ts"] is None, str(t["ts"])))
    return turns


def read_claude_ai_export(zip_path, limit=None, include_context=False):
    records, skipped = [], {}
    with zipfile.ZipFile(zip_path) as z:
        if CLAUDE_MEMBER not in z.namelist():
            raise ValueError(f"{zip_path} has no {CLAUDE_MEMBER}")
        convos = json.loads(z.read(CLAUDE_MEMBER))
    for conv in convos:
        turns = _claude_user_turns(conv, include_context)
        if not turns:
            skipped["no_user_turns"] = skipped.get("no_user_turns", 0) + 1
            continue
        date = _iso_date_from_string(conv.get("created_at"))
        skip, reason = should_skip_conversation({
            "source": "claude-ai",
            "title": conv.get("name") or "",
            "date": date,
            "turn_count": len(turns),
            "do_not_remember": False,
            "archived": False,
            "starred": False,
        })
        if skip:
            skipped[reason or "policy"] = skipped.get(reason or "policy", 0) + 1
            continue
        records.append({
            "conversation_id": hashlib.sha1(
                str(conv.get("uuid") or conv.get("name")).encode("utf-8")).hexdigest()[:16],
            "source": "claude-ai",
            "date": date,
            "log": CLAUDE_MEMBER,
            "turn_count": len(turns),
            "turns": turns,
        })
        if limit and len(records) >= limit:
            break
    return records, skipped


READERS = {"chatgpt": read_chatgpt_export, "claude-ai": read_claude_ai_export}


def main():
    ap = argparse.ArgumentParser(
        description="Knowledge-grab Pass 0 — provider export readers.")
    ap.add_argument("--export", required=True, help="path to the export .zip")
    ap.add_argument("--provider", required=True, choices=SOURCES)
    ap.add_argument("--out", help="JSONL output path (omit with --stats-only)")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N conversations (testing)")
    ap.add_argument("--stats-only", action="store_true",
                    help="report counts without writing records")
    ap.add_argument("--with-agent-context", action="store_true",
                    help="attach the assistant text each user turn was replying to. "
                         "OFF by default; the attached text is context, never a quote.")
    args = ap.parse_args()

    if not args.stats_only and not args.out:
        ap.error("--out is required unless --stats-only")

    records, skipped = READERS[args.provider](
        args.export, limit=args.limit, include_context=args.with_agent_context)

    turns = sum(r["turn_count"] for r in records)
    chars = sum(len(t["text"]) for r in records for t in r["turns"])
    print(f"provider:      {args.provider}")
    print(f"conversations: {len(records):,}")
    print(f"user turns:    {turns:,}")
    print(f"user chars:    {chars:,}")
    if skipped:
        print(f"skipped:       {dict(sorted(skipped.items()))}")
    dates = sorted(r["date"] for r in records if r["date"])
    if dates:
        print(f"date range:    {dates[0]} .. {dates[-1]}")

    if args.stats_only:
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote:         {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
