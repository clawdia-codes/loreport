#!/usr/bin/env python3
"""The attention queue — things the brain must ASK the user about, annotated at
the point of use.

TWO STATES, ONE RENDERING PATH
------------------------------
    parked      a capture that never landed (parse error, secret scan, git
                failure). Its content is NOT in the brain — it exists only as a
                gitignored file under hub/quarantine/.
    contested   two or more items that BOTH landed and disagree.

They render through one function (`render_marker` / `render_entry`) so the shape
stays identical, and they never share a NAME. One word covering two conditions
has damaged this codebase twice ("merge digest needs review" read as "the merge
failed"; one FAIL state covering broken/stale/needs-review). `parked` is also a
deliberately different word from `quarantine`, which already means "a capture
that failed to commit" in the digest, the health check and three code paths — a
quarantine artifact is the FILE, a parked entry is the OPEN QUESTION about it.

ANNOTATE, NEVER BLOCK
---------------------
Nothing here withholds a memory or stops a session. An entry adds a marker to
the item's index line and a short question to the injected block; the agent
proceeds with the stated best guess and asks the user to confirm. A blocking
design would let one bad detection freeze a memory, and detection is heuristic
by nature — false candidates are guaranteed, which is why `dismiss` takes no
required argument.

WHY THE INDEX LINE
------------------
Loreport's primary delivery is INJECTION: the whole index is written into the
provider's own context file and read whether the agent wants it or not.
`loreport_search_memories` exists but is the exception — memory an agent must
choose to fetch does not get fetched. So the annotation rides the projected
index line (hub/project.py), which every session unavoidably reads.

It is applied at PROJECTION time, never in `brain_merge.build_index_bytes`:
`indexes_are_current()` compares the working-tree INDEX.md against a
deterministic rebuild, so an index that depended on this mutable queue would be
rewritten and committed by every resolve/dismiss and would trip the projection
freshness and hand-edit checks in scripts/loreport-health.

STATE FILE
----------
hub/attention.json, gitignored alongside hub/merge-state.json and
hub/synthesis-report.json: it is local operational state rewritten outside a
commit, and tracking it would leave the nightly tree dirty for loreport-sync's
post-merge guard. Deliberately NOT under hub/quarantine/ —
brain_merge.count_quarantine_items walks that whole tree and a nonzero count
suppresses the health check's specific human-region wording.

Writes are a read-modify-write finished by an atomic os.replace. It does NOT
take hub/.loreport.lock: `quarantine()` in inbox_ingest.py calls in on the
failure path, and that path can run while the capture still holds the lock —
a second acquisition would deadlock the very handler that reports the failure.
The residual race (two writers, both mid-update, last one wins an entry) costs
at most one re-detection; a deadlocked failure handler costs the report.

Stdlib only. No cross-imports (this module is imported BY others, never the
other way round).
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

ATTENTION_FILE = "hub/attention.json"
SCHEMA_VERSION = 1

PARKED = "parked"
CONTESTED = "contested"
STATES = (PARKED, CONTESTED)

# The whole block is part of the projection's FIXED prefix — never truncated,
# because a question that gets truncated is a question that never gets asked.
# It therefore has to be bounded, or a detector having a bad night eats the
# index budget. Overflow is reported, not hidden.
MAX_RENDERED = 5

RESOLVE_CMD = "python3 hub/attention.py"


# --- state file ---------------------------------------------------------------


class QueueUnreadable(RuntimeError):
    """hub/attention.json exists but is not a queue we can read.

    Raised rather than swallowed: a silently-empty queue is a vacuously-true
    check, which this repo has shipped twice. Callers that must not crash
    (the projector) catch it and render the failure where a human will see it.
    """


def _path(brain_dir):
    return os.path.join(brain_dir, ATTENTION_FILE)


def empty_queue():
    return {"version": SCHEMA_VERSION, "entries": [], "acked_signature": None}


def load(brain_dir):
    """Read the queue. A MISSING file is an empty queue (the normal state); a
    present-but-unparseable one raises."""
    path = _path(brain_dir)
    if not os.path.isfile(path):
        return empty_queue()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise QueueUnreadable(f"{ATTENTION_FILE}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise QueueUnreadable(f"{ATTENTION_FILE}: not an attention queue")
    data.setdefault("version", SCHEMA_VERSION)
    data.setdefault("acked_signature", None)
    return data


def save(brain_dir, queue):
    path = _path(brain_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


# --- entries ------------------------------------------------------------------


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _key(state, names, path):
    parts = [state] + sorted(names or []) + ([path] if path else [])
    return " ".join(parts)


def make_id(state, names=None, path=None):
    """Stable id derived from WHAT the entry is about, not when it was seen —
    so re-detecting the same condition updates one entry instead of growing a
    new one every night."""
    return hashlib.sha1(_key(state, names, path).encode("utf-8")).hexdigest()[:8]


def is_open(entry):
    return not entry.get("resolved") and not entry.get("dismissed")


def open_entries(queue):
    return [e for e in queue.get("entries", []) if is_open(e)]


def find(queue, ident):
    """Look an entry up by id or unique id prefix. Dismissing a false candidate
    has to be cheaper than ignoring it, so `dismiss 3f` is accepted."""
    entries = queue.get("entries", [])
    exact = [e for e in entries if e.get("id") == ident]
    if exact:
        return exact[0]
    hits = [e for e in entries if str(e.get("id", "")).startswith(ident)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise KeyError(f"ambiguous id '{ident}' — matches {len(hits)} entries")
    raise KeyError(f"no attention entry '{ident}'")


def _upsert(queue, entry):
    for i, existing in enumerate(queue["entries"]):
        if existing.get("id") == entry["id"]:
            if not is_open(existing):
                # Re-detected after a human answered: that is new information,
                # so it reopens — but keep the original first_seen.
                entry["first_seen"] = existing.get("first_seen", entry["first_seen"])
                queue["entries"][i] = entry
                return entry, True
            existing["last_seen"] = entry["last_seen"]
            for k in ("detail", "guess", "reason", "names", "path", "artifact"):
                if entry.get(k) is not None:
                    existing[k] = entry[k]
            return existing, False
    queue["entries"].append(entry)
    return entry, True


def record_parked(brain_dir, path, reason, detail="", artifact=None):
    """A capture that never landed.

    `path` is the relpath it TRIED to write, and is None when the block failed
    before anyone could tell (a parse error names no file). `artifact` is where
    the raw block was set aside. The id keys on `path or artifact` so two
    different failed captures stay two entries rather than collapsing into one.
    """
    queue = load(brain_dir)
    now = _now()
    entry = {
        "id": make_id(PARKED, path=path or artifact),
        "state": PARKED,
        "path": path,
        "artifact": artifact,
        "names": [],
        "reason": reason,
        "detail": detail,
        "guess": None,
        "first_seen": now,
        "last_seen": now,
        "resolved": None,
        "dismissed": None,
    }
    entry, _new = _upsert(queue, entry)
    save(brain_dir, queue)
    return entry


def record_contested(brain_dir, names, guess=None, detail=""):
    """Two or more items that BOTH landed and disagree."""
    names = sorted(set(names))
    if len(names) < 2:
        raise ValueError("a contested entry needs at least two item names")
    if guess is not None and guess not in names:
        raise ValueError(f"best guess '{guess}' is not one of {names}")
    queue = load(brain_dir)
    now = _now()
    entry = {
        "id": make_id(CONTESTED, names=names),
        "state": CONTESTED,
        "path": None,
        "names": names,
        "reason": "contradiction",
        "detail": detail,
        "guess": guess,
        "first_seen": now,
        "last_seen": now,
        "resolved": None,
        "dismissed": None,
    }
    entry, _new = _upsert(queue, entry)
    save(brain_dir, queue)
    return entry


# --- anti-nag -----------------------------------------------------------------


def signature(entries):
    """Fingerprint of the OPEN SET, for alert-on-change.

    Timestamps are excluded — they move every run and would page every run, the
    normalisation `scripts/loreport-health`'s `_AGE_RE` exists for. Ids, states,
    names, paths and the recommended answer are all IN: a set growing from one
    entry to five, or the best guess flipping, is a real change the user has not
    answered yet.
    """
    parts = []
    for e in sorted(entries, key=lambda e: str(e.get("id"))):
        parts.append("|".join([
            str(e.get("id")),
            str(e.get("state")),
            ",".join(e.get("names") or []),
            str(e.get("path") or ""),
            str(e.get("guess") or ""),
        ]))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def needs_ask(queue):
    """True while the open set differs from what the user last answered.

    The ANNOTATION persists for as long as the condition does — it is context at
    the point of use, and hiding it would rebuild the silent parking this
    replaces. Only the ASK DIRECTIVE is gated, and `acked_signature` advances
    only on a human action (resolve / dismiss / ack). Three unanswered sessions
    therefore show one standing question; a question does not vanish unanswered.
    """
    entries = open_entries(queue)
    if not entries:
        return False
    return signature(entries) != queue.get("acked_signature")


def _ack(queue):
    queue["acked_signature"] = signature(open_entries(queue))


# --- rendering (ONE path, two words) ------------------------------------------


def render_marker(entry):
    """The in-line marker. Both states render through here; only the WORD
    differs."""
    return f"⚠ {entry['state'].upper()}#{entry['id']}"


def annotated_names(entry):
    """Item names this entry should mark on their index lines. A parked capture
    that was an UPDATE to an existing item still has one; a parked new capture
    has none, because nothing landed."""
    if entry.get("state") == CONTESTED:
        return list(entry.get("names") or [])
    path = entry.get("path") or ""
    stem = os.path.basename(path)
    if stem.endswith(".md"):
        stem = stem[: -len(".md")]
    return [stem] if stem else []


def annotate_index_text(index_text, entries, resolvable=None):
    """Insert each open entry's marker into its item's index line.

    The marker goes BETWEEN `[[name]]` and the em-dash, never at the end.
    Two consumers anchor on the end of the line — `project.TYPE_FROM_LINE_RE`
    (`(type)$`, and its fallback silently reclassifies every line as `knowledge`,
    which reorders truncation and starts dropping `user`/`person` lines) and
    `mcp_server.py`'s `endswith("(skill)")`. A suffix annotation breaks both with
    no error and no log.

    `resolvable` (optional) is the set of names that survived the caller's
    visibility filter; names outside it are not marked.
    """
    by_name = {}
    for e in entries:
        for name in annotated_names(e):
            if resolvable is not None and name not in resolvable:
                continue
            by_name.setdefault(name, []).append(e)
    if not by_name:
        return index_text

    out = []
    for line in index_text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        for name, hits in by_name.items():
            needle = f"[[{name}]]"
            if not stripped.startswith("- ") or needle not in stripped:
                continue
            markers = " ".join(render_marker(e) for e in hits)
            line = line.replace(needle, f"{needle} {markers}", 1)
            break
        out.append(line)
    return "".join(out)


def render_entry(entry, include_paths=True):
    """One entry as block lines. Same skeleton for both states.

    `include_paths=False` withholds a parked capture's intended path. A parked
    capture never landed, so nothing classified it — and this repo's fail-closed
    rule reads an absent `visibility:` as `local`. The path is therefore
    withheld from cloud-scope surfaces and shown in full on the local ones,
    which is where the primary hook lives anyway.
    """
    head = render_marker(entry)
    if entry.get("state") == CONTESTED:
        names = entry.get("names") or []
        claims = " vs ".join(f"[[{n}]]" for n in names)
        guess = entry.get("guess")
        guess_txt = f"[[{guess}]]" if guess else "none recorded"
        lines = [
            f"- {head} — {len(names)} items disagree: {claims}. Best guess: {guess_txt}.",
            f"  Proceed with the best guess, and ask: \"I have contradicting facts here — "
            f"my understanding is {guess or 'unclear'}. Do you agree?\"",
        ]
        if entry.get("detail"):
            lines.append(f"  detail: {entry['detail']}")
    else:
        # No `[[...]]` for a parked capture: nothing landed, so a wikilink would
        # resolve to nothing. Every surface auditor treats `[[x]]` as an item
        # reference, and an unresolvable one is reported as a finding on every
        # run — a permanent false alarm about the thing we are trying to report.
        where = entry.get("path") or entry.get("artifact") or "(unknown path)"
        target = where if include_paths else "(withheld from this surface)"
        lines = [
            f"- {head} — a capture never landed: {target} "
            f"({entry.get('reason') or 'unknown reason'}). Its content is NOT in the brain.",
            "  Treat the item as missing, and ask the user to re-state it.",
        ]
    lines.append(
        f"  resolve: {RESOLVE_CMD} resolve {entry['id']}"
        + (" --winner <name>" if entry.get("state") == CONTESTED else "")
        + f"   |   not a real problem: {RESOLVE_CMD} dismiss {entry['id']}"
    )
    return lines


def render_block(entries, ask, include_paths=True):
    """The whole attention section, or "" when there is nothing to say.

    Deterministic — no clock, no run counter — so an unchanged set projects
    byte-identical output and cannot make the surface churn.
    """
    entries = sorted(entries, key=lambda e: (str(e.get("state")), str(e.get("id"))))
    if not entries:
        return ""
    shown, extra = entries[:MAX_RENDERED], entries[MAX_RENDERED:]
    lines = [f"## Loreport — needs your input ({len(entries)})"]
    if ask:
        lines.append(
            "NEW since the user last answered. This does NOT block you: use the memory, "
            "state your best guess, and ask the question below in your reply."
        )
    else:
        lines.append(
            "Standing (already raised, still unanswered). Do not re-ask unprompted — "
            "use the memory with the best guess below."
        )
    for entry in shown:
        lines.extend(render_entry(entry, include_paths=include_paths))
    if extra:
        lines.append(f"- +{len(extra)} more — see `{RESOLVE_CMD} list`")
    return "\n".join(lines) + "\n"


# --- CLI ----------------------------------------------------------------------


def _cmd_list(brain_dir, args):
    queue = load(brain_dir)
    entries = queue.get("entries", []) if args.all else open_entries(queue)
    if args.json:
        print(json.dumps(entries, indent=2, sort_keys=True))
        return 0
    if not entries:
        print("attention queue: empty")
        return 0
    print(render_block(entries, needs_ask(queue), include_paths=True), end="")
    return 0


def _cmd_contest(brain_dir, args):
    entry = record_contested(brain_dir, args.names, guess=args.guess, detail=args.detail)
    print(f"contested {entry['id']}: {', '.join(entry['names'])} "
          f"(best guess: {entry.get('guess') or 'none'})")
    return 0


def _cmd_park(brain_dir, args):
    entry = record_parked(brain_dir, args.path, args.reason, args.detail)
    print(f"parked {entry['id']}: {entry['path']} ({entry['reason']})")
    return 0


def _cmd_resolve(brain_dir, args):
    queue = load(brain_dir)
    entry = find(queue, args.id)
    if entry.get("state") == CONTESTED:
        names = entry.get("names") or []
        winner = args.winner or entry.get("guess")
        if winner is None:
            print("resolve: --winner <name> is required (no best guess on record)",
                  file=sys.stderr)
            return 2
        if winner not in names:
            print(f"resolve: '{winner}' is not one of {', '.join(names)}", file=sys.stderr)
            return 2
        entry["winner"] = winner
        losers = [n for n in names if n != winner]
    else:
        entry["winner"] = None
        losers = []
    entry["resolved"] = _now()
    entry["answer"] = args.winner or args.note
    _ack(queue)
    save(brain_dir, queue)
    print(f"resolved {entry['id']} — marker cleared.")
    if losers:
        # The queue records the answer and clears the marker; retiring the losing
        # item is a git mutation and stays on the one code path that is allowed
        # to make them (allowlist + lock + scoped cleanup).
        print(f"Recorded answer: {winner} wins. Superseded: {', '.join(losers)}.")
        print("Retire the superseded item(s) through the normal capture path "
              "(a `delete` block through inbox_ingest.py) — this command does not "
              "edit the brain itself.")
    return 0


def _cmd_dismiss(brain_dir, args):
    queue = load(brain_dir)
    entry = find(queue, args.id)
    entry["dismissed"] = _now()
    entry["dismiss_reason"] = args.reason
    _ack(queue)
    save(brain_dir, queue)
    print(f"dismissed {entry['id']} ({args.reason}) — marker cleared.")
    return 0


def _cmd_ack(brain_dir, args):
    queue = load(brain_dir)
    _ack(queue)
    save(brain_dir, queue)
    print(f"acknowledged {len(open_entries(queue))} open entry(ies); "
          "they stay annotated, but stop being announced as new.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Loreport attention queue — parked captures and contested items."
    )
    parser.add_argument("--brain-dir", default=os.environ.get("LOREPORT_BRAIN_DIR", os.getcwd()))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show the open queue")
    p.add_argument("--all", action="store_true", help="include resolved/dismissed")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("contest", help="record two or more items that disagree")
    p.add_argument("names", nargs="+")
    p.add_argument("--guess", default=None, help="the item you believe is right")
    p.add_argument("--detail", default="")
    p.set_defaults(func=_cmd_contest)

    p = sub.add_parser("park", help="record a capture that never landed")
    p.add_argument("path")
    p.add_argument("--reason", required=True)
    p.add_argument("--detail", default="")
    p.set_defaults(func=_cmd_park)

    p = sub.add_parser("resolve", help="record the answer and clear the marker")
    p.add_argument("id")
    p.add_argument("--winner", default=None)
    p.add_argument("--note", default="")
    p.set_defaults(func=_cmd_resolve)

    # No required argument: detection is heuristic, so throwing out a false
    # candidate must be cheaper than living with it.
    p = sub.add_parser("dismiss", help="not a real problem — clear it")
    p.add_argument("id")
    p.add_argument("--reason", default="false candidate")
    p.set_defaults(func=_cmd_dismiss)

    p = sub.add_parser("ack", help="asked already; stop announcing as new")
    p.set_defaults(func=_cmd_ack)

    args = parser.parse_args(argv)
    brain_dir = os.path.abspath(args.brain_dir)
    try:
        return args.func(brain_dir, args)
    except (QueueUnreadable, KeyError, ValueError) as exc:
        print(f"attention: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
