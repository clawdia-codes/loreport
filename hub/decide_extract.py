#!/usr/bin/env python3
"""
hub/decide_extract.py — Pass 1b: extraction anchored on the user's verdicts.

`observe_extract.py` (Pass 1a) reads only what the user typed. That is correct for
preferences and it is the reason attribution never drifts — but on an agentic corpus it
throws away where the content actually lives.

MEASURED ON THIS CORPUS, 2026-08-14
------------------------------------
    the assistant wrote            5,714,840 characters
    the user wrote                   632,012 characters      (the assistant wrote 9x more)
    user turns <= 25 chars               34%                 ("yes do that")
    user turns <= 10 chars               24%                 ("good")
    median user turn                 41 characters

For a third of this corpus the user's own words carry a **verdict and no content**. Pass
1a sees "yes do that", finds nothing quotable that supports any claim, and either returns
nothing — 339 conversations yielded zero — or invents a trait to cover the gap, which is
where the Barnum drafts came from. Two failure modes, one cause: the substance was never
in the room.

THE DESIGN, AND WHY IT DOES NOT REOPEN THE ATTRIBUTION HOLE
------------------------------------------------------------
The naive fix — feed the assistant's text in as more source material — recreates the
failure this whole pipeline was built around, where a gateway-restart notice became a
"memory about the user".

So the roles stay strictly separated:

  * The user's turn is the ANCHOR. `verbatim_quote` must come from it and is verified
    against it, exactly as in Pass 1a. Nothing the assistant wrote can ever be quoted.
  * The assistant's turn is CONTEXT. It supplies what was proposed, never who wanted it.
  * Every claim is framed as a decision *about* that proposal — "he approved X", "he
    rejected Y", "he corrected Z" — never as "he said X".

A short verdict is therefore evidence of a decision, with the decision's substance
sourced correctly and labelled honestly.

Corrections are worth more than approvals. "No, actually…" states what he does *not*
want, and nothing in the brain currently captures that at all.

Nothing here writes to the brain, touches git, or contacts a paid model.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import observe_extract as oe  # noqa: E402

DEFAULT_MODEL = oe.DEFAULT_MODEL
DEFAULT_ENDPOINT = oe.DEFAULT_ENDPOINT

# A turn this short is a verdict, not a statement. Above it the user is usually saying
# something in their own right and Pass 1a already handles that well.
SHORT_TURN_CHARS = 200

DECIDE_PROMPT = """Below is one exchange from a person's conversation with an AI assistant.

The ASSISTANT proposed something. The PERSON responded. Your job is to work out what the
PERSON decided, and to state it as a durable fact about them.

WHAT THE ASSISTANT PROPOSED (context only — the person did NOT write this):
{context}

WHAT THE PERSON REPLIED (their own words):
{reply}

Extract 0-3 durable facts. For each one:

- "kind": "decision" if they accepted or chose something, "correction" if they rejected,
  redirected or fixed something, "directive" if they stated a standing rule.
- "claim": one sentence, phrased as what THE PERSON decided, chose, rejected or required.
  Say "They approved…", "They rejected…", "They require…". NEVER phrase it as though the
  person wrote the assistant's text.
- "verbatim_quote": an EXACT substring of WHAT THE PERSON REPLIED above. Never quote the
  assistant. If their reply is too short or empty to quote meaningfully, return nothing.
- "context_scope": "global" if it applies generally, otherwise the project or repo name.

Return NOTHING (an empty array) when the reply is mere acknowledgement with no decision
in it — "thanks", "ok", "haha" — or when the proposal is a one-off task with nothing
durable in it. Most exchanges are like this. Zero is the normal answer.

DROP: anything true only that week; one-off debugging that got resolved; the mechanics of
a single task ("put the file here", "output only a list").
KEEP: choices with reasons, standing rules, rejections, and facts about who they are,
what they do, or who they work with.

Return ONLY a JSON array, no prose, no markdown fence:
[{{"kind":"decision|correction|directive","claim":"<one sentence>",
   "verbatim_quote":"<exact substring of the person's reply>","context_scope":"global|<name>"}}]
"""

IDENTITY_PROMPT = """Below are messages a person typed to an AI assistant, and what the
assistant had said just before each one.

Answer one question only: **what does this reveal about who this person is, what they do
for a living, and who they work with?**

Do NOT extract their preferences about how to be answered — a different pass handles
those, and duplicating them here is wasted. Look instead for role, employer, domain
expertise, responsibilities, colleagues, and ongoing commitments, including where those
appear incidentally inside a work request.

EXCHANGES:
{exchanges}

Extract 0-3 facts. Each needs a "verbatim_quote" that is an EXACT substring of something
THE PERSON typed — never the assistant. Quote what shows the fact, even if the fact is
only implied by it.

Return ONLY a JSON array, no prose, no fence:
[{{"kind":"identity","claim":"<one sentence about who they are / what they do / who with>",
   "verbatim_quote":"<exact substring the person typed>","context_scope":"global|<name>"}}]
"""


# SHORT IS NOT THE SAME AS RESPONSIVE — this cost a whole run to learn.
#
# The first version selected any turn under 200 characters that had context above it, on
# the assumption that a short turn is a verdict on the proposal. It is not: in a working
# conversation most short turns are simply the NEXT REQUEST. Sent to a model told to "work
# out what the person decided", "can you shorten it by 50%" dutifully became "They require
# the assistant to shorten the provided text by 50%" — a task restated as a standing rule.
# The smoke run produced 38 observations from 12 conversations, 4x Pass 1a's rate, and
# almost all of it was that shape. High yield, no signal.
#
# A verdict is recognisable: it responds rather than asks.
_VERDICT_RE = re.compile(
    r"(?i)^\W*("
    r"yes|yep|yeah|yup|ok|okay|good|great|perfect|nice|correct|exactly|agreed|sure|"
    r"do (it|that|this)|go ahead|sounds good|looks good|lets do|let's do|proceed|ship it|"
    r"no|nope|not quite|not really|wrong|incorrect|actually|instead|rather|"
    r"i'?d rather|i prefer|don'?t|do not|stop|hold on|wait"
    r")\b")

# A fresh ask, even a short one, is not a verdict on anything.
_REQUEST_RE = re.compile(
    r"(?i)^\W*(can|could|would|will|please|write|give|make|create|show|explain|"
    r"what|which|who|when|where|why|how|is|are|does|do you|tell me|find|list|add)\b")


def is_verdict(text):
    """True when this reply responds to a proposal rather than making a fresh request."""
    t = (text or "").strip()
    if not t:
        return False
    if _REQUEST_RE.match(t) and not _VERDICT_RE.match(t):
        return False
    return bool(_VERDICT_RE.match(t))


def exchanges_for_decide(record):
    """(reply, context) pairs worth asking about: a short VERDICT with a proposal above it.

    Three conditions, each earning its place:
      - context present — with nothing above it there is nothing to have decided about,
        and a model asked anyway will invent the proposal;
      - short — a long turn stands on its own and Pass 1a already reads it well;
      - verdict-shaped — see above; this is the condition whose absence produced a run of
        task requests relabelled as standing requirements.
    """
    out = []
    for turn in record.get("turns") or []:
        text = (turn.get("text") or "").strip()
        ctx = (turn.get("context") or "").strip()
        if not text or not ctx:
            continue
        if len(text) > SHORT_TURN_CHARS:
            continue
        if not is_verdict(text):
            continue
        out.append((text, ctx))
    return out


def parse_and_verify(raw, user_texts):
    """Parse the model's array and keep only claims quoting the USER.

    The quote gate is unchanged from Pass 1a and is the reason assistant context cannot
    launder itself into a fact: a claim whose quote is not found in something the user
    actually typed is dropped, no matter how plausible it reads.
    """
    items = oe.parse_observations(raw)
    kept, rejected = [], 0
    haystack = oe.normalize(" \n ".join(user_texts))
    for item in items:
        quote = (item.get("verbatim_quote") or "").strip()
        claim = (item.get("claim") or "").strip()
        if not quote or not claim:
            rejected += 1
            continue
        if oe.normalize(quote) not in haystack:
            rejected += 1
            continue
        kept.append(item)
    return kept, rejected


def run_decide(record, model, endpoint, timeout):
    pairs = exchanges_for_decide(record)
    found, rejected = [], 0
    for reply, ctx in pairs[:8]:
        prompt = DECIDE_PROMPT.format(context=ctx[:3000], reply=reply[:1000])
        try:
            raw = oe.call_ollama(prompt, model, endpoint, timeout)
        except Exception:
            continue
        kept, rej = parse_and_verify(raw, [reply])
        found.extend(kept)
        rejected += rej
    return found, rejected, len(pairs)


def run_identity(record, model, endpoint, timeout):
    lines, user_texts = [], []
    for turn in (record.get("turns") or [])[:12]:
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        user_texts.append(text)
        ctx = (turn.get("context") or "").strip()
        if ctx:
            lines.append(f"[assistant said] {ctx[:600]}")
        lines.append(f"[person typed] {text[:800]}")
    if not user_texts:
        return [], 0
    prompt = IDENTITY_PROMPT.format(exchanges="\n".join(lines)[:9000])
    try:
        raw = oe.call_ollama(prompt, model, endpoint, timeout)
    except Exception:
        return [], 0
    return parse_and_verify(raw, user_texts)


def main():
    ap = argparse.ArgumentParser(
        description="Pass 1b — verdict-anchored and identity extraction.")
    ap.add_argument("--in", dest="inp", required=True,
                    help="Pass 0 JSONL, written WITH --with-agent-context")
    ap.add_argument("--out", required=True, help="observations JSONL (appended; resumable)")
    ap.add_argument("--mode", choices=("decide", "identity"), required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-conversations", default=None,
                    help="file of conversation_ids, one per line — e.g. the zero-yield set")
    args = ap.parse_args()

    only = None
    if args.only_conversations:
        with open(args.only_conversations, encoding="utf-8") as fh:
            only = {ln.strip() for ln in fh if ln.strip()}

    done = oe.already_done(args.out)
    records = []
    with open(args.inp, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["conversation_id"] in done:
                continue
            if only is not None and r["conversation_id"] not in only:
                continue
            records.append(r)
    if args.limit:
        records = records[:args.limit]

    no_context = sum(1 for r in records
                     if not any((t.get("context") or "") for t in r.get("turns") or []))
    if records and no_context == len(records):
        print("ERROR: no turn in this input carries `context`. Re-run the Pass 0 reader "
              "with --with-agent-context; this pass cannot work without it.", file=sys.stderr)
        return 2

    totals = {"conversations": 0, "observations": 0, "quote_not_found": 0,
              "mode": args.mode, "model": args.model, "no_context_records": no_context}
    # single_writer is a LOCK guard and yields nothing — it exists because two concurrent
    # runs both read already_done() at startup and then both append, which on 2026-08-07
    # produced 238 rows for 140 conversations. Duplicated rows inflate the evidence count
    # a later pass uses to judge whether a preference is stable, so this is not cosmetic.
    with oe.single_writer(args.out):
        with open(args.out, "a", encoding="utf-8") as out:
            for r in records:
                if args.mode == "decide":
                    found, rejected, _pairs = run_decide(
                        r, args.model, args.endpoint, args.timeout)
                else:
                    found, rejected = run_identity(
                        r, args.model, args.endpoint, args.timeout)
                totals["conversations"] += 1
                totals["observations"] += len(found)
                totals["quote_not_found"] += rejected
                out.write(json.dumps({
                    "conversation_id": r["conversation_id"],
                    "source": r.get("source"),
                    "date": r.get("date"),
                    "observations": found,
                }, ensure_ascii=False) + "\n")
                out.flush()
    print(json.dumps(totals, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
