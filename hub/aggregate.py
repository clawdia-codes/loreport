#!/usr/bin/env python3
"""
hub/aggregate.py — knowledge-grab Pass 2: aggregation and stability analysis.

Reads Pass 1's observations (`hub/observe_extract.py`) and reduces thousands of
single-conversation signals into a small number of DRAFT memories, each carrying the
evidence that supports it.

Nothing here writes to the brain. Output is a private JSON artifact that Pass 3 screens
and a human reviews.

THE SHAPE OF THE FUNNEL
-----------------------
Pass 1 is high recall on purpose: one conversation in, a few candidate claims out, each
anchored to a verified quote. That produces thousands of rows in which the same fact
appears twenty times in twenty conversations. The interesting signal is not any single
row — it is **how often, over how long, and from how many sources** a claim recurs. A
preference stated once in 2023 and never again is noise. The same preference restated
across two providers and eighteen months is a durable trait.

So clustering is deterministic and stability is arithmetic. Only the final wording is a
model's job, and the plan is explicit about why that wording matters:

    "Spend real care on the `description` line: that line is the product."

The description is what gets injected into every future session's index. The body is read
rarely; the description is read always.

WHAT GETS DRAFTED, AND WHAT DOES NOT
-------------------------------------
A model call per cluster is the expensive step, so clusters that Pass 3 would certainly
kill are not drafted at all. Pass 3's own rule is "evidence <3 and not an explicit
directive" — so a cluster is drafted when it has evidence from **2 or more distinct
conversations**, OR when it contains an explicit directive (`meta-statement`), which is
valuable precisely because the user only had to say it once.

Everything else is counted and reported as `weak_singletons`, never silently dropped. A
cap nobody can see reads as "we covered everything" when it did not.

VISIBILITY IS FORCED, NOT SUGGESTED
------------------------------------
Every draft is stamped `visibility: local` by this code, regardless of what the model
says. The plan's privacy wall requires imported items to default local, with promotion to
`shared` an explicit human decision. This brain has already had three batches of
unclassified items silently cloud-published in a single day because an absent field
defaulted open; a model's opinion is not an acceptable input to that decision.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse Pass 1's model transport and normalizer rather than writing a second one.
import observe_extract as oe  # noqa: E402

DEFAULT_MODEL = "nemotron3:33b"
DEFAULT_ENDPOINT = oe.DEFAULT_ENDPOINT

# Words that carry no topical signal. Deliberately short: an aggressive stoplist merges
# unrelated clusters, which is worse than leaving a few near-duplicates for Pass 3.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these",
    "those", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in", "on",
    "for", "with", "as", "at", "by", "from", "it", "its", "he", "she", "they", "them",
    "their", "his", "her", "user", "users", "wants", "want", "would", "should", "could",
    "has", "have", "had", "do", "does", "did", "not", "no", "so", "up", "out", "about",
    "into", "over", "when", "which", "who", "what", "how", "why", "there", "here",
}

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-_]{2,}")


def content_tokens(text):
    """Topical tokens of a claim: lowercase words of 3+ chars, minus stopwords."""
    return {t for t in TOKEN_RE.findall((text or "").lower()) if t not in STOPWORDS}


def load_observations(paths):
    """Flatten Pass 1 JSONL files into one list of observation dicts.

    Pass 1 writes one record per conversation with an `observations` array; this lifts
    each observation to top level and carries the conversation's identity down with it,
    because stability is measured in distinct CONVERSATIONS, not distinct rows.
    """
    out = []
    for path in paths:
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for obs in rec.get("observations") or []:
                    if not isinstance(obs, dict) or not obs.get("claim"):
                        continue
                    out.append({
                        "claim": obs.get("claim", ""),
                        "kind": obs.get("kind", ""),
                        "verbatim_quote": obs.get("verbatim_quote", ""),
                        "context_scope": obs.get("context_scope", ""),
                        "conversation_id": rec.get("conversation_id"),
                        "source": rec.get("source"),
                        "date": rec.get("date"),
                    })
    return out


def cluster(observations, threshold=0.34):
    """Group observations whose claims share enough topical vocabulary.

    Candidate pairs come from an inverted index, so only observations sharing at least
    one content token are ever compared — the alternative is quadratic over thousands of
    rows. Merging is union-find: A~B and B~C puts all three together even when A and C
    do not directly pass the threshold, which is what lets a topic accumulate phrasing
    variants across three years.
    """
    parent = list(range(len(observations)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    toks = [content_tokens(o["claim"]) for o in observations]
    index = defaultdict(list)
    for i, ts in enumerate(toks):
        for t in ts:
            index[t].append(i)

    for _, members in index.items():
        # A token shared by a huge fraction of the corpus is a topic word, not a link;
        # comparing every pair under it is expensive and merges unrelated claims.
        if len(members) > 400:
            continue
        for a_pos, i in enumerate(members):
            for j in members[a_pos + 1:]:
                if find(i) == find(j):
                    continue
                ti, tj = toks[i], toks[j]
                if not ti or not tj:
                    continue
                inter = len(ti & tj)
                if inter / len(ti | tj) >= threshold:
                    union(i, j)

    groups = defaultdict(list)
    for i, o in enumerate(observations):
        groups[find(i)].append(o)
    return list(groups.values())


def stability(members):
    """Arithmetic, not judgement: how well-supported is this cluster?"""
    convos = {m["conversation_id"] for m in members if m["conversation_id"]}
    dates = sorted(d for d in (m["date"] for m in members) if d)
    sources = sorted({m["source"] for m in members if m["source"]})
    span_days = 0
    if len(dates) >= 2:
        try:
            span_days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
        except ValueError:
            span_days = 0
    return {
        "evidence": len(convos),
        "observations": len(members),
        "sources": sources,
        "first_seen": dates[0] if dates else None,
        "last_seen": dates[-1] if dates else None,
        "span_days": span_days,
    }


def is_draftable(members, stats):
    """Pass 3 kills 'evidence <3 and not an explicit directive'; don't pay to draft those.

    Threshold is 2 rather than 3 deliberately: Pass 3 is the screen, and drafting a
    borderline cluster so a critic can reject it with the wording in front of it is
    better than pre-judging it here with no wording at all.
    """
    if stats["evidence"] >= 2:
        return True
    return any(m.get("kind") == "meta-statement" for m in members)


def brain_index_lines(brain_dir):
    """The existing index, as `name — description` lines, for the dedup prompt.

    Descriptions are what the model needs to answer 'do we already know this?'. Reading
    80 index lines is cheap; reading 80 whole memories is not, and would not answer the
    question any better.
    """
    lines = []
    mem_dir = os.path.join(brain_dir, "memories")
    if not os.path.isdir(mem_dir):
        return lines
    for fn in sorted(os.listdir(mem_dir)):
        if not fn.endswith(".md"):
            continue
        try:
            with open(os.path.join(mem_dir, fn), encoding="utf-8") as fh:
                text = fh.read(4000)
        except OSError:
            continue
        name = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        desc = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if name:
            lines.append(f"{name.group(1).strip()} — {(desc.group(1).strip() if desc else '')}")
    return lines


PROMPT = """You are distilling a personal memory for a portable second-brain.

Below is a cluster of observations extracted from one person's real conversations. They \
all appear to be about the same thing. Each carries a verbatim quote of what the person \
actually typed.

CLUSTER (evidence: {evidence} distinct conversations, {first_seen} to {last_seen}):
{cluster_text}

The brain ALREADY contains these items (name — description):
{index_text}

Write ONE draft memory as a single JSON object, no prose, no code fence:

{{"name": "kebab-case-slug",
  "type": "user|feedback|project|reference|decision|person",
  "description": "one line, under 160 chars",
  "body": "2-5 sentences stating the durable fact. For feedback/project include why it \
matters and how to apply it.",
  "relation": "new|update|duplicate",
  "relates_to": "existing name if relation is update or duplicate, else empty",
  "confidence": "high|medium|low",
  "behavioral_impact": "high|medium|low"}}

Rules:
- The description line is the product. It is injected into every future session; the body \
is rarely read. Make it specific and useful on its own.
- State what is DURABLY true about this person, not what happened in one conversation.
- If the brain already covers this, say relation "duplicate" and name it.
- If the brain covers it but this adds or corrects something, say "update".
- Do not invent anything the quotes do not support.
- behavioral_impact is how much knowing this would change an assistant's behaviour."""


def draft_cluster(members, stats, index_text, model, endpoint, timeout):
    lines = []
    for m in members[:12]:
        q = (m.get("verbatim_quote") or "").replace("\n", " ")[:240]
        lines.append(f'- [{m.get("kind")}] {m.get("claim")}\n  quote: "{q}"')
    prompt = PROMPT.format(
        evidence=stats["evidence"],
        first_seen=stats["first_seen"],
        last_seen=stats["last_seen"],
        cluster_text="\n".join(lines),
        index_text="\n".join(index_text) or "(the brain is empty)",
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


def main():
    ap = argparse.ArgumentParser(description="Knowledge-grab Pass 2 — aggregation.")
    ap.add_argument("--in", dest="inp", required=True, action="append",
                    help="Pass 1 observations JSONL (repeatable)")
    ap.add_argument("--brain-dir", required=True, help="Loreport brain repo, for dedup")
    ap.add_argument("--out", required=True, help="drafts JSON output")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--limit", type=int, default=None, help="draft only N clusters (testing)")
    ap.add_argument("--stats-only", action="store_true",
                    help="cluster and report, make no model calls")
    args = ap.parse_args()

    observations = load_observations(args.inp)
    clusters = cluster(observations)
    scored = [(c, stability(c)) for c in clusters]
    scored.sort(key=lambda cs: (-cs[1]["evidence"], -cs[1]["observations"]))

    draftable = [(c, s) for c, s in scored if is_draftable(c, s)]
    weak = len(scored) - len(draftable)

    print(f"observations:     {len(observations):,}")
    print(f"clusters:         {len(scored):,}")
    print(f"draftable:        {len(draftable):,}")
    print(f"weak_singletons:  {weak:,}  (reported, not drafted — Pass 3's rule would kill these)")
    if scored:
        print(f"largest cluster:  {scored[0][1]['evidence']} conversations, "
              f"{scored[0][1]['observations']} observations")

    if args.stats_only:
        return 0

    index_text = brain_index_lines(args.brain_dir)
    print(f"brain index:      {len(index_text)} existing items")

    todo = draftable[:args.limit] if args.limit else draftable
    drafts, failures = [], 0
    for i, (members, stats) in enumerate(todo, 1):
        obj = draft_cluster(members, stats, index_text, args.model, args.endpoint, args.timeout)
        if not obj:
            failures += 1
            continue
        # The privacy wall is enforced here, not requested from the model.
        obj["visibility"] = "local"
        obj["stability"] = stats
        obj["evidence_quotes"] = [
            {"quote": m.get("verbatim_quote"), "source": m.get("source"), "date": m.get("date")}
            for m in members[:5]
        ]
        drafts.append(obj)
        if i % 10 == 0:
            print(f"  drafted {i}/{len(todo)}", flush=True)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"drafts": drafts, "weak_singletons": weak,
                   "model_failures": failures}, fh, indent=2, ensure_ascii=False)

    by_rel = defaultdict(int)
    for d in drafts:
        by_rel[d.get("relation", "?")] += 1
    print(f"drafts written:   {len(drafts)}  {dict(by_rel)}")
    print(f"model failures:   {failures}")
    print(f"wrote:            {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
