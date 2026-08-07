#!/usr/bin/env python3
"""
hub/corpus_prep.py — knowledge-grab Pass 0: normalize session logs into per-conversation
records containing ONLY genuine user turns.

This is the input stage of the knowledge-grab pipeline. It is deliberately *high recall*
and exercises no judgment: it keeps everything the human actually typed and drops
everything they did not. Deciding what is worth remembering is Pass 1's job (a model) and
Pass 2's (aggregation). Mixing those concerns here is how a corpus stage quietly becomes a
second, worse extractor.

Contrast with hub/sweep_extract.py, whose parsing this module reuses: the sweep is
high-*precision* (deterministic patterns, "explicit save"/"decision" only) because it
commits unattended. Pass 0 keeps far more, because a human reviews what comes out the far
end and the intermediate artifact never reaches the brain.

WHY THE TURN FILTER IS THE WHOLE POINT
--------------------------------------
Session logs are not a transcript of the user. They interleave:
  - tool results wearing role=user (Claude Code puts `toolUseResult` on the turn)
  - harness injections wearing role=user (skill bodies, slash-command payloads, cron
    prompts, hook and task notifications, "[System] ..." gateway notices)
  - subagent traffic (isSidechain) and harness notices (isMeta)
  - assistant turns
A naive reader attributes all of it to the human. That is not hypothetical: a
2026-08-06 full-history run promoted "[System] Your previous turn was interrupted by a
gateway restart..." into a candidate memory *about the user*. Everything that is not a
genuine typed user turn must be gone before a model ever sees it, because downstream the
model has no way to tell the difference.

OUTPUT
------
JSONL, one record per conversation:
  {"conversation_id": <sha1 of log path>, "source": "claude"|"codex"|"openclaw",
   "date": "YYYY-MM-DD", "log": <path>, "turn_count": N, "turns": [{"ts":…, "text":…}, …]}

Secrets are redacted in place (same pattern set as inbox_ingest / sweep_extract). A record
whose turns are all filtered away is dropped entirely rather than emitted empty.

Nothing here writes to the brain, touches git, or contacts a model.

CLI:
    python3 hub/corpus_prep.py --out PATH [--source claude|codex|openclaw]...
                              [--since EPOCH] [--window-days N] [--extra-log PATH]...
                              [--stats-only]
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse the sweep's parsing and scrubbing rather than reimplementing them. A second
# implementation of "which turns are real" would drift from the one the nightly sweep
# uses, and the whole point of this stage is that the filter is trustworthy.
import sweep_extract as sx  # noqa: E402

SOURCES = ("claude", "codex", "openclaw")

# Turns shorter than this carry no extractable content ("ok", "yes", "thanks") and
# inflate the corpus without adding signal. Deliberately far below sweep_extract's
# threshold: Pass 0 keeps context the sweep would discard.
MIN_TURN_CHARS = 4

# Upper bound, defence-in-depth. sweep_extract caps candidates at 1200 chars, which is why
# the sweep never saw the payloads described below — Pass 0 has no such cap by design
# (it keeps long genuine turns), so it needs its own ceiling. A human does not type
# 42,000 characters. Generous enough to keep a pasted spec or error log, which is real
# user content; far below the injected-payload range actually observed (15KB and 42KB).
MAX_TURN_CHARS = 20000


def is_injected(text):
    """True for harness-injected text, scanning the WHOLE turn.

    `sweep_extract.is_synthetic_turn` only inspects the first 400 characters. That is
    correct for the sweep, which also caps candidates at 1200 chars, so a marker can never
    hide beyond its window. Pass 0 keeps long turns, so the head-only scan leaks: a real
    2026-08-07 run over the live corpus emitted two `/security-review` command payloads
    (14,996 and 42,399 chars) whose `<system-reminder>` and `<command-name>` markers sat at
    character 2378 and 4024 — well past the head.

    Trade-off, accepted deliberately: scanning the whole turn will also drop a genuine
    turn in which the user *discusses* a marker by name (this user does discuss harness
    internals). That is the right side to err on — losing one meta-discussion costs a
    little recall, whereas injecting a 42KB command payload puts text the human never
    wrote in front of a model that cannot tell the difference, which is the exact
    attribution failure this pass exists to prevent.
    """
    low = text.lower()
    if low[:400].startswith(sx.DISPATCH_PREFIXES):  # positional by nature
        return True
    return any(marker in low for marker in sx.SYNTHETIC_MARKERS)


def _iso_date(ts):
    """Best-effort YYYY-MM-DD from whatever timestamp shape the log carried."""
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(ts)
    return text[:10] if len(text) >= 10 and text[4] == "-" else None


def _discover(sources):
    """(source, path) for every session log of the requested sources."""
    found = []
    if "claude" in sources:
        found += [("claude", p) for p in sx.discover_claude_logs()]
    if "codex" in sources:
        found += [("codex", p) for p in sx.discover_codex_logs()]
    if "openclaw" in sources:
        found += [("openclaw", p) for p in sx.discover_openclaw_logs()]
    return found


ITERATORS = {
    "claude": lambda p: sx.iter_claude_turns(p),
    "codex": lambda p: sx.iter_codex_turns(p),
    "openclaw": lambda p: sx.iter_openclaw_turns(p),
}


def user_turns_from_log(source, path):
    """Yield (ts, cleaned_text) for genuine user turns only.

    Four filters, in order. Each drops a distinct class of not-the-human text; see the
    module docstring for why each exists.
    """
    iterator = ITERATORS.get(source)
    if iterator is None:
        return
    for role, text, ts, _log in iterator(path):
        # 1. Assistant turns. (The per-source iterators already drop tool results,
        #    isSidechain subagent traffic and isMeta harness notices.)
        if role != "user":
            continue
        body = (text or "").strip()
        # 2. Empty / trivially short, or far longer than a human types (see MAX_TURN_CHARS).
        if not (MIN_TURN_CHARS <= len(body) <= MAX_TURN_CHARS):
            continue
        # 3. Harness injections wearing role=user — skill bodies, slash-command payloads,
        #    cron prompts, task notifications, "[System] …" gateway notices, agent
        #    dispatch briefs. This is the attribution-error defence. Scans the WHOLE turn,
        #    not just the head — see is_injected().
        if is_injected(body):
            continue
        # 4. Redact secrets before the text can reach a model or a disk artifact.
        yield ts, sx.redact_secrets(body)


# --- structural template detection -------------------------------------------
#
# Marker lists rot. They only catch injections someone already noticed, and every new
# harness feature adds a payload shape nobody has added to the list yet — the definition
# of a filter that decays. A 2026-08-07 sample of the live artifact showed marker-based
# filtering still leaving it dominated by `/security-review` payloads and Codex
# review-continuation templates, because those instances carried no marker at all.
#
# The structural signal is repetition across conversations. A human does not type the
# same 2,000-character message in fifty different sessions; a template does. So: a LONG
# turn whose opening recurs across several distinct conversations is injected, whatever
# words it happens to use. This needs no per-payload maintenance and catches shapes that
# do not exist yet.
#
# Length-gated on purpose: short repeats ("try now", "continue", "yes please") are genuine
# human habit and must survive. Only a long, verbatim-recurring opening is a template.
TEMPLATE_MIN_CHARS = 200        # below this, repetition is human habit, not a template
TEMPLATE_MIN_CONVERSATIONS = 3  # seen opening this many distinct conversations -> template
# Opening window, tuned empirically 2026-08-07 against the live corpus. 200 was too wide:
# `/security-review` payloads embed the changed-file list inside the first 200 chars, so
# every instance fingerprinted differently and all of them survived. The stable prefix is
# ~115 chars. 80/100/120 all eliminate them; 120 does it while retaining the most genuine
# content, so it is the least aggressive setting that works.
TEMPLATE_OPENING_CHARS = 120


def _opening_fingerprint(text):
    return sx.content_fingerprint(text[:TEMPLATE_OPENING_CHARS])


def find_template_openings(pairs):
    """Openings that recur across >= TEMPLATE_MIN_CONVERSATIONS distinct conversations.

    `pairs` is an iterable of (conversation_key, turn_text).
    """
    convos_per_opening = {}
    for convo_key, text in pairs:
        if len(text) < TEMPLATE_MIN_CHARS:
            continue
        convos_per_opening.setdefault(_opening_fingerprint(text), set()).add(convo_key)
    return {fp for fp, convos in convos_per_opening.items()
            if len(convos) >= TEMPLATE_MIN_CONVERSATIONS}


def is_template_turn(text, template_openings):
    return (len(text) >= TEMPLATE_MIN_CHARS
            and _opening_fingerprint(text) in template_openings)


def build_records(sources, since_ts=None, extra_logs=None):
    """One record per conversation that still has genuine user turns after filtering."""
    logs = _discover(sources)
    for extra in extra_logs or []:
        # Source inferred from path shape; default claude, matching the sweep's layout.
        guessed = next((s for s in SOURCES if f"/{s}" in extra or f".{s}" in extra), "claude")
        logs.append((guessed, extra))

    # PASS A — read every log once, applying the per-turn filters.
    per_log = []
    for source, path in logs:
        try:
            turns = list(user_turns_from_log(source, path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue  # an unreadable or truncated log must not abort the whole pass
        if since_ts is not None:
            turns = [(ts, t) for ts, t in turns
                     if not isinstance(ts, (int, float)) or ts >= since_ts]
        if turns:
            per_log.append((source, path, turns))

    # PASS B — template detection is inherently cross-conversation: a recurring opening
    # can only be recognised by looking at the whole corpus at once, so it cannot live in
    # the per-turn filter above.
    template_openings = find_template_openings(
        (path, text) for _s, path, turns in per_log for _ts, text in turns)

    records = []
    seen_fingerprints = set()
    for source, path, turns in per_log:
        turns = [(ts, t) for ts, t in turns if not is_template_turn(t, template_openings)]
        if not turns:
            continue  # never emit an empty conversation

        # Dedupe identical turns within the conversation (retries, resends).
        deduped = []
        local_seen = set()
        for ts, text in turns:
            fp = sx.content_fingerprint(text)
            if fp in local_seen:
                continue
            local_seen.add(fp)
            deduped.append({"ts": ts, "text": text})
        if not deduped:
            continue

        # Drop a whole conversation that duplicates one already emitted (a copied or
        # resumed log file). Fingerprint over the joined bodies, order-sensitive.
        convo_fp = sx.content_fingerprint("\n".join(t["text"] for t in deduped))
        if convo_fp in seen_fingerprints:
            continue
        seen_fingerprints.add(convo_fp)

        dates = [d for d in (_iso_date(t["ts"]) for t in deduped) if d]
        records.append({
            "conversation_id": hashlib.sha1(path.encode("utf-8")).hexdigest()[:16],
            "source": source,
            "date": min(dates) if dates else None,
            "log": path,
            "turn_count": len(deduped),
            "turns": deduped,
        })
    return records


def main():
    parser = argparse.ArgumentParser(
        description="Knowledge-grab Pass 0 — normalize session logs to user-only records.")
    parser.add_argument("--out", help="JSONL output path (omit with --stats-only)")
    parser.add_argument("--source", action="append", choices=SOURCES,
                        help="Restrict to a source; repeatable. Default: all.")
    parser.add_argument("--since", type=float, default=None,
                        help="Only turns at or after this epoch")
    parser.add_argument("--window-days", type=int, default=None,
                        help="Rolling window from now, in days")
    parser.add_argument("--extra-log", action="append", default=[],
                        help="Additional log path (testing); repeatable")
    parser.add_argument("--stats-only", action="store_true",
                        help="Print the summary and write nothing")
    args = parser.parse_args()

    if not args.out and not args.stats_only:
        parser.error("--out is required unless --stats-only is given")

    since = args.since
    if args.window_days is not None:
        since = datetime.now(timezone.utc).timestamp() - args.window_days * 86400

    sources = tuple(args.source) if args.source else SOURCES
    records = build_records(sources, since_ts=since, extra_logs=args.extra_log)

    total_turns = sum(r["turn_count"] for r in records)
    by_source = {}
    for r in records:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1

    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "conversations": len(records),
        "user_turns": total_turns,
        "by_source": by_source,
        "out": args.out if args.out else None,
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
