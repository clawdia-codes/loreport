#!/usr/bin/env python3
"""
hub/observe_extract.py — knowledge-grab Pass 1: observation extraction.

Reads Pass 0's per-conversation records (hub/corpus_prep.py) and asks a cheap model to
propose *observations*: candidate durable facts, trait signals, and explicit directives,
each anchored to a verbatim quote from the source.

This writes NOTHING into the brain. Its output is a private working artifact that a later
pass aggregates and a human reviews. High recall is fine and expected here; precision is
Pass 2's job (aggregation and stability analysis) and Pass 3's (adversarial screen).

THE QUOTE GATE — the reason this pass can be trusted at all
-----------------------------------------------------------
Every observation must carry `verbatim_quote`, and this module **mechanically verifies
that the quote actually occurs in the source turns** (whitespace-normalized substring
match). An observation whose quote cannot be found is dropped and counted as
`quote_not_found`.

That single check is worth more than any amount of prompt discipline. It is cheap,
deterministic, non-gameable by a persuasive model, and it converts "the model asserted
this" into "the user demonstrably said this". Without it a model will happily produce
fluent, plausible, entirely invented claims about a person — the Barnum failure the
knowledge-grab design names — and nothing downstream could tell the difference.

MODEL
-----
Defaults to a LOCAL ollama model: this is a mechanical map over hundreds of
conversations, so it should not consume a metered/subscription budget. `think` is
disabled explicitly — qwen-family reasoning defaults have previously blown idle watchdogs
on this host.

RESUMABILITY
------------
Output is appended per conversation and completed `conversation_id`s are skipped on a
re-run, so an interrupted long run resumes without redoing work or duplicating rows.

CLI:
    python3 hub/observe_extract.py --in pass0.jsonl --out observations.jsonl
                                   [--model NAME] [--limit N] [--timeout SEC]
                                   [--endpoint URL] [--dry-run]
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:latest"

KINDS = ("fact", "trait-signal", "meta-statement")

# A quote shorter than this is not evidence — "yes", "ok", "do it" match almost anything
# and would let the gate pass on noise.
MIN_QUOTE_CHARS = 12

PROMPT = """You are extracting durable observations about ONE person from things THEY typed.

Below are numbered messages written by the user (nobody else — no assistant text, no tool
output). Extract only what is worth remembering months from now.

KEEP:
- fact: a concrete durable fact (a decision made and why, a hard-won gotcha, a stable
  detail about their setup, people, or projects)
- meta-statement: they explicitly state how they want to be worked with
  ("from now on...", "always...", "never...", "I prefer...")
- trait-signal: evidence of a stable working preference, inferred from how they write

DROP: chit-chat, one-off debugging that got resolved, status true only that week,
restatements of what code already says, praise, and anything you cannot quote.

DROP ESPECIALLY — instructions the user gave to a machine, not preferences about
themselves. This person operates AI agents, so their messages are full of task briefs,
output-format specs and file-path rules aimed at some agent for one job. Those read like
preferences and are NOT. Test each candidate: does it describe THE PERSON — what they
want, decided, believe, own, or repeatedly do — or does it describe HOW ONE TASK SHOULD BE
DONE? Keep only the first.
  DROP: "Write ONLY under .maestro/docs/preparation/"        (task spec)
  DROP: "Output ONLY a comma-separated list, no preamble"    (output format for one job)
  DROP: "You are the implementer for phase 2"               (role assignment)
  KEEP: "From now on always give me the bottom line first"  (how THEY want to be treated)
  KEEP: "We chose Postgres over SQLite because ..."         (a decision THEY made, with why)

RULES:
- Every observation MUST include "verbatim_quote": an EXACT substring copied from the
  messages below. Do not paraphrase, correct, or shorten mid-word. If you cannot quote it
  exactly, do not report it.
- "context_scope": "global" if it applies generally, otherwise the specific project/repo.
- Return between 0 and 6 observations. Zero is a perfectly good answer.

Return ONLY a JSON array, no prose, no markdown fence:
[{{"kind":"fact|trait-signal|meta-statement","claim":"<one sentence>",
   "verbatim_quote":"<exact substring>","context_scope":"global|<name>"}}]

MESSAGES:
{messages}
"""


def normalize(text):
    """Whitespace-insensitive form for substring matching."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def call_ollama(prompt, model, endpoint, timeout):
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Reasoning defaults have blown idle watchdogs on this host; keep it off.
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 1024},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")).get("response", "")


def parse_observations(raw):
    """Pull a JSON array out of a model response that may be wrapped in prose or fences."""
    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [o for o in parsed if isinstance(o, dict)]


def verify_and_clean(observations, turns):
    """Keep only observations whose verbatim_quote genuinely occurs in `turns`.

    Returns (kept, rejection_counts). This is the gate described in the module docstring:
    it turns a model assertion into demonstrable evidence, and it is the one thing here a
    fluent-but-wrong model cannot talk its way past.
    """
    haystacks = [normalize(t) for t in turns]
    kept, rejects = [], {"quote_not_found": 0, "quote_too_short": 0, "bad_shape": 0}

    for obs in observations:
        kind = obs.get("kind")
        claim = (obs.get("claim") or "").strip()
        quote = (obs.get("verbatim_quote") or "").strip()
        if kind not in KINDS or not claim or not quote:
            rejects["bad_shape"] += 1
            continue
        if len(quote) < MIN_QUOTE_CHARS:
            rejects["quote_too_short"] += 1
            continue
        needle = normalize(quote)
        if not any(needle in hay for hay in haystacks):
            rejects["quote_not_found"] += 1
            continue
        kept.append({
            "kind": kind,
            "claim": claim,
            "verbatim_quote": quote,
            "context_scope": (obs.get("context_scope") or "global").strip() or "global",
        })
    return kept, rejects


def already_done(out_path):
    done = set()
    if not os.path.exists(out_path):
        return done
    with open(out_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                done.add(json.loads(line)["conversation_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main():
    ap = argparse.ArgumentParser(description="Knowledge-grab Pass 1 — observation extraction.")
    ap.add_argument("--in", dest="inp", required=True, help="Pass 0 JSONL")
    ap.add_argument("--out", required=True, help="observations JSONL (appended; resumable)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--limit", type=int, default=None, help="Only N conversations (testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the prompt for the first conversation and exit")
    args = ap.parse_args()

    with open(args.inp, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]

    done = already_done(args.out)
    pending = [r for r in records if r["conversation_id"] not in done]
    if args.limit:
        pending = pending[:args.limit]

    if args.dry_run:
        if pending:
            turns = [t["text"] for t in pending[0]["turns"]]
            msgs = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(turns))
            print(PROMPT.format(messages=msgs[:4000]))
        return

    totals = {"conversations": 0, "observations": 0, "model_errors": 0,
              "quote_not_found": 0, "quote_too_short": 0, "bad_shape": 0}

    out = open(args.out, "a", encoding="utf-8")
    try:
        for rec in pending:
            turns = [t["text"] for t in rec["turns"]]
            msgs = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(turns))
            # Keep the prompt bounded; long conversations are truncated rather than skipped.
            prompt = PROMPT.format(messages=msgs[:12000])
            try:
                raw = call_ollama(prompt, args.model, args.endpoint, args.timeout)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                totals["model_errors"] += 1
                continue

            kept, rejects = verify_and_clean(parse_observations(raw), turns)
            for key in ("quote_not_found", "quote_too_short", "bad_shape"):
                totals[key] += rejects[key]

            out.write(json.dumps({
                "conversation_id": rec["conversation_id"],
                "source": rec["source"],
                "date": rec.get("date"),
                "observations": kept,
            }, ensure_ascii=False) + "\n")
            out.flush()  # resumable: a kill mid-run loses at most one conversation
            totals["conversations"] += 1
            totals["observations"] += len(kept)
    finally:
        out.close()

    totals["model"] = args.model
    print(json.dumps(totals, sort_keys=True))


if __name__ == "__main__":
    main()
