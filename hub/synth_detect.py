#!/usr/bin/env python3
"""
hub/synth_detect.py — deterministic synthesis cluster detector (report-only).

Clusters memories on [[wikilink]] co-reference and shared type:. Rule (design §2): ≥3
memories that MUTUALLY link — an edge exists only when A links B *and* B links A —
OR ≥3 memories linking a common missing [[name]] → synthesis proposal.

Mutuality matters: co-reference in one direction alone makes the link graph one big
connected component (a densely cross-linked brain yields a single 17-member blob with
an arbitrary member as its "topic"), which is the degenerate output the health check
is supposed to alarm on rather than propose.

Degenerate topics — single tokens, stopwords, or a bare taxonomy type — and oversized
components are refused and reported as detector-health warnings instead of clusters.
Wired nowhere — report emitter only.

CLI:
    python3 hub/synth_detect.py --brain-dir PATH [--json]
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "shall", "can",
        "it", "its", "this", "that", "these", "those", "i", "you", "he",
        "she", "we", "they", "what", "which", "who", "when", "where", "why",
        "how", "all", "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "no", "nor", "not", "only", "own", "same", "so",
        "than", "too", "very", "just", "also", "now", "here", "there", "then",
        "once", "about", "into", "through", "during", "before", "after", "above",
        "below", "between", "out", "off", "over", "under", "again", "further",
    }
)

# A bare taxonomy type is not a synthesis topic — "project" says nothing about what
# the cluster is *about*. Treated as degenerate for the same reason single tokens are.
TYPE_WORDS = frozenset(
    {"user", "feedback", "project", "reference", "knowledge", "person", "decision"}
)

# A component this large is link-graph sprawl, not a coherent synthesis topic.
MAX_CLUSTER_MEMBERS = 8


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


def read_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def resolve_link(brain_dir, name):
    hits = []
    for candidate in (
        os.path.join(brain_dir, "memories", f"{name}.md"),
        os.path.join(brain_dir, "knowledge", f"{name}.md"),
        os.path.join(brain_dir, "skills", name, "SKILL.md"),
    ):
        if os.path.isfile(candidate):
            hits.append(candidate)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return "ambiguous"
    return None


def load_items(brain_dir):
    items = {}
    for folder, prefix in (("memories", "memories"), ("knowledge", "knowledge")):
        root = os.path.join(brain_dir, folder)
        if not os.path.isdir(root):
            continue
        for fname in os.listdir(root):
            if not fname.endswith(".md") or fname == "README.md":
                continue
            path = os.path.join(root, fname)
            text = read_file(path)
            fm, body = parse_frontmatter(text)
            name = (fm or {}).get("name") or os.path.splitext(fname)[0]
            typ = (fm or {}).get("type", "reference")
            links = list(dict.fromkeys(WIKILINK_RE.findall(body)))
            items[name] = {
                "name": name,
                "type": typ,
                "path": f"{prefix}/{fname}",
                "links": links,
            }
    return items


def connected_components(names, edges):
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)

    groups = defaultdict(list)
    for n in names:
        groups[find(n)].append(n)
    return [sorted(g) for g in groups.values() if len(g) >= 3]


def is_degenerate_topic(topic):
    tokens = re.findall(r"[a-z0-9]+", topic.lower())
    if not tokens:
        return True
    if len(tokens) == 1:
        tok = tokens[0]
        if len(tok) <= 2 or tok in STOPWORDS or tok in TYPE_WORDS:
            return True
    if all(t in STOPWORDS for t in tokens):
        return True
    return False


def common_slug_prefix(names):
    """Longest shared hyphen-segment prefix, e.g. project-coach-{db-safety,login-ui}."""
    segs = [n.split("-") for n in names]
    shared = []
    for i in range(min(len(s) for s in segs)):
        tok = segs[0][i]
        if all(s[i] == tok for s in segs) and i < min(len(s) for s in segs) - 1:
            shared.append(tok)
        else:
            break
    return "-".join(shared) if shared else None


def detect_clusters(brain_dir):
    items = load_items(brain_dir)
    names = set(items.keys())
    edges = set()
    missing_link_counts = defaultdict(set)

    for name, info in items.items():
        for link in info["links"]:
            target = resolve_link(brain_dir, link)
            if target == "ambiguous":
                continue
            if target and link in names:
                # Design §2: "memories that mutually link" — both directions required.
                if name in items[link]["links"]:
                    edges.add(tuple(sorted((name, link))))
            elif not target:
                missing_link_counts[link].add(name)

    link_clusters = connected_components(names, edges)

    clusters = []
    warnings = []

    for members in link_clusters:
        types = [items[m]["type"] for m in members]
        shared_type = types[0] if len(set(types)) == 1 else None
        inter_links = sum(
            1 for a in members for b in members
            if a != b and b in items[a]["links"]
        ) // 2
        if len(members) > MAX_CLUSTER_MEMBERS:
            warnings.append(
                {
                    "reason": "oversized-cluster",
                    "topic": common_slug_prefix(members) or shared_type or members[0],
                    "members": members,
                    "signal": "mutual-link",
                }
            )
            continue
        topic = common_slug_prefix(members) or shared_type or members[0]
        if is_degenerate_topic(topic):
            # A bare type is a bad label, not a bad cluster — fall back to a member
            # name before refusing. Only genuinely degenerate output alarms.
            topic = members[0]
        if is_degenerate_topic(topic):
            warnings.append(
                {
                    "reason": "degenerate-topic",
                    "topic": topic,
                    "members": members,
                    "signal": "mutual-link",
                }
            )
            continue
        clusters.append(
            {
                "topic": topic,
                "members": members,
                "signal": "mutual-link",
                "shared_type": shared_type,
                "evidence": {
                    "member_count": len(members),
                    "inter_links": inter_links,
                    "types": types,
                },
            }
        )

    for missing, members_set in missing_link_counts.items():
        members = sorted(members_set)
        if len(members) < 3:
            continue
        topic = missing
        if is_degenerate_topic(topic):
            warnings.append(
                {
                    "reason": "degenerate-topic",
                    "topic": topic,
                    "members": members,
                    "signal": "common-missing-link",
                }
            )
            continue
        clusters.append(
            {
                "topic": topic,
                "members": members,
                "signal": "common-missing-link",
                "shared_type": None,
                "evidence": {
                    "member_count": len(members),
                    "missing_link": missing,
                },
            }
        )

    return {
        "clusters": clusters,
        "warnings": warnings,
        "item_count": len(items),
    }


def format_human(report):
    lines = []
    lines.append(f"synth_detect: {len(report['clusters'])} cluster(s) from {report['item_count']} items")
    for i, cl in enumerate(report["clusters"], 1):
        ev = cl["evidence"]
        lines.append(
            f"  [{i}] topic={cl['topic']} signal={cl['signal']} "
            f"members={len(cl['members'])} evidence={json.dumps(ev, sort_keys=True)}"
        )
        lines.append(f"       members: {', '.join(cl['members'])}")
    if report["warnings"]:
        lines.append(f"detector-health: {len(report['warnings'])} degenerate topic warning(s)")
        for w in report["warnings"]:
            lines.append(
                f"  warning {w['reason']}: topic={w['topic']} signal={w['signal']} "
                f"members={len(w['members'])}"
            )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Synthesis cluster detector (report-only)")
    parser.add_argument("--brain-dir", required=True, help="Path to Loreport brain repo")
    parser.add_argument("--json", action="store_true", help="Print JSON only (no human lines)")
    args = parser.parse_args()
    brain_dir = os.path.abspath(args.brain_dir)
    report = detect_clusters(brain_dir)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_human(report))
        print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
