#!/usr/bin/env python3
"""
hub/screen.py — knowledge-grab Pass 3: the adversarial screen.

Reads Pass 2's drafts (`hub/aggregate.py`) and tries to KILL each one. What survives is
ranked and handed to a human. Nothing here writes to the brain.

WHY A CRITIC AND NOT A SCORER
------------------------------
Pass 1 is high recall and Pass 2 turns recurrence into fluent prose. Fluent prose is
exactly what a reviewer cannot evaluate quickly — a well-written claim about a person
reads as true whether or not it is. So this pass is prompted to REFUTE rather than to
rate, and it defaults to refusing when uncertain. A screen that must be argued *out* of
keeping something has a very different failure mode from one that must be argued into it.

The plan's expected yield is 30-80 net-new items from tens of thousands of turns. If this
pass keeps 400, it is broken, not generous: every surviving line is loaded into every
session forever, so 400 mediocre items are strictly worse than 40 good ones.

THE FIVE WAYS A DRAFT DIES
---------------------------
1. **Barnum** — true of almost anyone ("values efficiency", "likes clear communication").
   This is the signature failure of extracting personality from text and the reason the
   quote gate exists upstream; a Barnum claim can be perfectly well-evidenced and still
   carry no information.
2. **Unactionable** — nothing an assistant would do differently for knowing it.
3. **Mis-scoped** — a project detail dressed as a durable trait, or the reverse.
4. **Thin evidence** — under 3 distinct conversations and not an explicit directive.
5. **Stale-risk** — a fact about a moving target (a version, a current status) that will
   be wrong soon and has no trigger to correct it.

Rule 4 is arithmetic, so it is applied in code before any model call: it is cheap,
deterministic, and not something a persuasive draft can talk its way past.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import observe_extract as oe  # noqa: E402

DEFAULT_MODEL = "nemotron3:33b"
DEFAULT_ENDPOINT = oe.DEFAULT_ENDPOINT

RANK = {"high": 3, "medium": 2, "low": 1}

CRITIC_PROMPT = """You are screening a candidate memory before it enters a person's \
permanent second-brain. Every item that survives is loaded into every future assistant \
session forever, so the bar is high and the default is REJECT.

Your job is to find a reason to REJECT this draft. Only keep it if you cannot.

DRAFT
name: {name}
type: {type}
description: {description}
body: {body}

EVIDENCE
seen in {evidence} distinct conversations, {first_seen} to {last_seen}, sources: {sources}
quotes:
{quotes}

Reject if ANY of these hold:
- BARNUM: it would be true of almost anyone. "Values efficiency", "prefers clear \
answers", "is curious" are all Barnum. This is the most common failure — check it first.
- UNACTIONABLE: an assistant would not do anything differently knowing it.
- MIS-SCOPED: it is a detail about one project dressed up as a durable trait, or a \
durable trait written as if it were project state.
- STALE-RISK: it states something that will simply be wrong in a few months (a version, \
a current status, a count) with nothing to correct it.
- UNSUPPORTED: the quotes do not actually establish the claim.

Answer with ONE JSON object, no prose, no code fence:

{{"verdict": "keep|kill",
  "reason_code": "barnum|unactionable|mis-scoped|stale-risk|unsupported|none",
  "reason": "one sentence",
  "confidence": "high|medium|low",
  "behavioral_impact": "high|medium|low"}}

If you are uncertain, answer "kill"."""


def thin_evidence(draft):
    """Rule 4, in arithmetic. Returns True when the draft dies without a model call."""
    stats = draft.get("stability") or {}
    if stats.get("evidence", 0) >= 3:
        return False
    # An explicit directive is worth keeping on one telling — the person only had to say
    # it once, and saying it again would be strange.
    return draft.get("type") not in ("feedback", "decision")


def quotes_block(draft):
    lines = []
    for q in (draft.get("evidence_quotes") or [])[:5]:
        text = (q.get("quote") or "").replace("\n", " ")[:240]
        lines.append(f'- "{text}"  ({q.get("source")}, {q.get("date")})')
    return "\n".join(lines) or "(none)"


def screen_one(draft, model, endpoint, timeout):
    stats = draft.get("stability") or {}
    prompt = CRITIC_PROMPT.format(
        name=draft.get("name", ""),
        type=draft.get("type", ""),
        description=draft.get("description", ""),
        body=draft.get("body", ""),
        evidence=stats.get("evidence", 0),
        first_seen=stats.get("first_seen"),
        last_seen=stats.get("last_seen"),
        sources=", ".join(stats.get("sources") or []),
        quotes=quotes_block(draft),
    )
    raw = oe.call_ollama(prompt, model, endpoint, timeout)
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def score(draft, verdict):
    """Rank order: confidence x behavioural impact, tie-broken by evidence breadth."""
    conf = RANK.get((verdict.get("confidence") or "").lower(), 1)
    impact = RANK.get((verdict.get("behavioral_impact") or "").lower(), 1)
    stats = draft.get("stability") or {}
    return (conf * impact, stats.get("evidence", 0), stats.get("span_days", 0))


def main():
    ap = argparse.ArgumentParser(description="Knowledge-grab Pass 3 — adversarial screen.")
    ap.add_argument("--in", dest="inp", required=True, help="Pass 2 drafts JSON")
    ap.add_argument("--out", required=True, help="survivors JSON")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    with open(args.inp, encoding="utf-8") as fh:
        drafts = json.load(fh).get("drafts", [])

    # Deterministic kills first — no reason to pay a model to reject these.
    pre_killed = [d for d in drafts if thin_evidence(d)]
    candidates = [d for d in drafts if not thin_evidence(d)]
    if args.limit:
        candidates = candidates[:args.limit]

    print(f"drafts in:        {len(drafts)}")
    print(f"killed on evidence: {len(pre_killed)}  (under 3 conversations, not a directive)")
    print(f"sent to critic:   {len(candidates)}")

    survivors, killed, failures = [], [], 0
    reasons = defaultdict(int)
    for i, d in enumerate(candidates, 1):
        v = screen_one(d, args.model, args.endpoint, args.timeout)
        if not v:
            # A critic that did not answer is not a pass. Fail closed.
            failures += 1
            reasons["critic_no_answer"] += 1
            killed.append({**d, "verdict": {"verdict": "kill",
                                            "reason_code": "critic_no_answer",
                                            "reason": "critic returned no parseable verdict"}})
            continue
        if (v.get("verdict") or "").lower() == "keep":
            survivors.append({**d, "verdict": v})
        else:
            reasons[v.get("reason_code", "unspecified")] += 1
            killed.append({**d, "verdict": v})
        if i % 10 == 0:
            print(f"  screened {i}/{len(candidates)}", flush=True)

    survivors.sort(key=lambda d: score(d, d["verdict"]), reverse=True)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({
            "survivors": survivors,
            "killed": killed,
            "killed_on_evidence": len(pre_killed),
            "critic_failures": failures,
            "kill_reasons": dict(reasons),
        }, fh, indent=2, ensure_ascii=False)

    print(f"survivors:        {len(survivors)}")
    print(f"kill reasons:     {dict(reasons)}")
    print(f"critic failures:  {failures}  (counted as kills — a non-answer is not a pass)")
    print(f"wrote:            {args.out}")
    if len(survivors) > 120:
        print("WARNING: yield far above the plan's 30-80 expectation — the screen may be "
              "too permissive. Review before accepting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
