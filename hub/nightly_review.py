#!/usr/bin/env python3
"""Nightly review: give the synthesis detector a consumer, and reconcile drift.

WHY THIS FILE EXISTS
--------------------
`hub/synth_detect.py` has run nightly for weeks as a pure report emitter. Its
proposals reached a digest line that says "REPORT-ONLY, none filed" and then
nothing happened to them — no decision, no record, no reminder. A producer
nothing consumes decays silently and forever, so this file is the consumer.

Two jobs, one nightly artifact:

1.  **Dispositions.** Every proposal the detector emits is entered in a durable
    ledger as `pending` and must eventually reach one of `accept` / `reject` /
    `defer`, each with a recorded reason. A pending proposal that sits past
    ``PENDING_MAX_DAYS`` is a *failure* in `scripts/loreport-health` — that is
    the only thing that actually forces a decision rather than hoping for one.

    ⚠ NOTHING HERE EDITS A MEMORY. A disposition is a decision *about* a
    proposal, recorded next to it. `accept` does not create a `knowledge/` page,
    does not merge two memories, and does not touch `memories/`. Auto-merging
    memories from a link-graph heuristic is exactly the failure this design
    refuses; the ledger is the audit trail proving the human decided.

2.  **Reconciliation.** A name-set diff between each configured *native* memory
    store (the provider's own memory, e.g. an assistant's on-disk memory dir)
    and Loreport's `INDEX.md`. This is the mechanical half of the
    `memory-reconcile` skill, run nightly and reported. It is deliberately NOT
    the projection-hash check: `loreport-health` checks 3-4 already assert the
    *outbound* surface matches what projection wrote. This asserts something
    different — that the native store has no items Loreport has never seen.
    It also never repairs anything; it reports.

THE DATED ARTIFACT
------------------
`hub/nightly/<YYYY-MM-DD>.json` is written on every nightly run. It is the
machine-checkable proof that the review happened, and `loreport-health` FAILS
when yesterday's is missing. Without it, "the review runs nightly" is an
unfalsifiable claim — which is how the detector spent weeks emitting into a void.

WHAT IS TRACKED AND WHAT IS NOT
-------------------------------
- `hub/nightly/<date>.json` is a derived per-run report: **gitignored**, same
  class as `hub/digest-*.md` and `hub/synthesis-report.json`.
- `hub/proposals/ledger.json` holds human decisions and `first_seen` clocks:
  **tracked**. It is deliberately written ONLY when its content changes — a new
  proposal appears, or a human runs `--dispose`. A ledger rewritten every night
  would leave the brain tree dirty every night, which is precisely why
  merge-state.json and the digests are gitignored (see brain-template/.gitignore).
  `last_seen` therefore lives in the attestation, not the ledger, and an expiring
  deferral is computed from `defer_until` at read time rather than written back.

Stdlib only, single file, no imports from other hub modules.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

SCHEMA = 1

LEDGER_FILE = "hub/proposals/ledger.json"
ATTESTATION_DIR = "hub/nightly"
RECONCILE_SOURCES_FILE = "hub/reconcile-sources.json"

# A proposal may sit undecided this long. Past it, health grades it a failure.
# Long enough that a week of travel does not page the owner; short enough that a
# decision queue cannot quietly become a graveyard.
PENDING_MAX_DAYS = 14

STATUSES = ("accept", "reject", "defer")
TERMINAL = ("accept", "reject")

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# How many names to spell out in the attestation before summarising. The count is
# always exact; only the enumeration is capped.
MAX_LISTED = 20


class LedgerError(Exception):
    """The ledger exists but could not be read. Never repaired by overwriting:
    that would silently discard recorded human decisions."""


class DispositionError(Exception):
    """A refused disposition. Refusing is the point — see dispose()."""


# --- dates -------------------------------------------------------------------


def today_iso():
    return date.today().isoformat()


def _parse_iso(text):
    if not isinstance(text, str) or not ISO_DATE_RE.match(text):
        raise ValueError(f"not a YYYY-MM-DD date: {text!r}")
    return datetime.strptime(text, "%Y-%m-%d").date()


def _days_between(start_iso, end_iso):
    try:
        return (_parse_iso(end_iso) - _parse_iso(start_iso)).days
    except ValueError:
        return 0


def yesterday_iso(today=None):
    base = _parse_iso(today) if today else date.today()
    return (base - timedelta(days=1)).isoformat()


# --- identity ----------------------------------------------------------------


def proposal_id(cluster):
    """Stable id for a detected cluster: its signal plus its member set.

    Sorted, so member order from the detector cannot change the id, which would
    make the same proposal arrive new every night and reset its clock.
    """
    members = sorted(str(m) for m in (cluster.get("members") or []))
    payload = str(cluster.get("signal", "")) + "\n" + "\n".join(members)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _rejected_member_sets(ledger):
    return [
        (pid, frozenset(entry.get("members") or []))
        for pid, entry in ledger.get("proposals", {}).items()
        if entry.get("status") == "reject"
    ]


def suppressed_by(members, rejected):
    """Return the id of a rejected proposal that already covers `members`.

    Rejection has to survive membership churn. The id is the member set, so a
    cluster that gains one member is a brand-new id — and without this, every
    rejected proposal returns as pending the next time the brain grows, and the
    owner re-decides the same thing forever. Two shapes count as "already
    rejected":

      * the new set is a SUBSET of a rejected one (the detector narrowed it), and
      * the new set is the rejected one PLUS AT MOST ONE newcomer.

    Deliberately not a similarity threshold: a tuning knob here would be a knob
    nobody ever tunes. Two newcomers is a genuinely different cluster and comes
    back for a fresh decision.
    """
    new = frozenset(members)
    if not new:
        return None
    for pid, rs in rejected:
        if not rs:
            continue
        if new <= rs:
            return pid
        if rs <= new and len(new - rs) <= 1:
            return pid
    return None


# --- ledger ------------------------------------------------------------------


def ledger_path(brain_dir):
    return os.path.join(brain_dir, LEDGER_FILE)


def load_ledger(brain_dir):
    path = ledger_path(brain_dir)
    if not os.path.isfile(path):
        return {"schema": SCHEMA, "proposals": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise LedgerError(f"{LEDGER_FILE}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), dict):
        raise LedgerError(f"{LEDGER_FILE}: not a ledger object")
    return data


def save_ledger(brain_dir, ledger):
    path = ledger_path(brain_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    text = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def effective_status(entry, today):
    """`defer` is temporary by construction: once `defer_until` has passed the
    proposal is pending again. Computed, never written back, so a quiet night
    does not dirty the tracked ledger."""
    status = entry.get("status", "pending")
    if status == "defer":
        until = entry.get("defer_until")
        if until and _parse_iso(until) <= _parse_iso(today):
            return "pending"
    return status


def _clock_start(entry, today):
    """When the current pending spell began: the deferral's expiry if it came
    back from a deferral, otherwise first detection."""
    if entry.get("status") == "defer" and entry.get("defer_until"):
        return entry["defer_until"]
    return entry.get("first_seen") or today


def pending(ledger, today):
    return [
        entry for entry in ledger.get("proposals", {}).values()
        if effective_status(entry, today) == "pending"
    ]


def overdue(ledger, today, max_days=PENDING_MAX_DAYS):
    return [
        entry for entry in pending(ledger, today)
        if _days_between(_clock_start(entry, today), today) > max_days
    ]


def refresh(ledger, synthesis, today):
    """Enter newly detected clusters into the ledger as `pending`.

    Returns a summary; `changed` tells the caller whether the tracked ledger
    needs writing (and staging). Existing entries are NOT touched — no
    `last_seen` bump, no status rewrite — so an unchanged brain writes nothing.
    """
    clusters = (synthesis or {}).get("clusters") or []
    rejected = _rejected_member_sets(ledger)
    proposals = ledger.setdefault("proposals", {})
    added, suppressed = [], []

    for cluster in clusters:
        pid = proposal_id(cluster)
        if pid in proposals:
            continue
        members = [str(m) for m in (cluster.get("members") or [])]
        covered = suppressed_by(members, rejected)
        if covered:
            suppressed.append({"id": pid, "topic": cluster.get("topic"),
                               "already_rejected_as": covered})
            continue
        proposals[pid] = {
            "id": pid,
            "topic": cluster.get("topic"),
            "signal": cluster.get("signal"),
            "members": sorted(members),
            "first_seen": today,
            "status": "pending",
            "reason": None,
            "decided": None,
            "decided_by": None,
            "defer_until": None,
        }
        added.append(pid)

    return {"added": added, "suppressed": suppressed, "changed": bool(added)}


def dispose(ledger, pid, status, reason, today, until=None, decided_by=None):
    """Record a disposition. Every refusal below is load-bearing.

    A disposition without a reason is not a disposition — it is a cleared queue,
    and six months later nobody can tell whether the proposal was considered or
    dismissed. `defer` without a future date is `reject` wearing a disguise: it
    would leave the entry non-pending forever with nothing to bring it back.
    """
    if status not in STATUSES:
        raise DispositionError(
            f"unknown disposition {status!r}; expected one of {', '.join(STATUSES)}")
    if not isinstance(reason, str) or not reason.strip():
        raise DispositionError(
            "a disposition needs a recorded reason — an unexplained decision is "
            "indistinguishable from a cleared queue")
    entry = ledger.get("proposals", {}).get(pid)
    if entry is None:
        raise DispositionError(f"no proposal with id {pid!r}")

    if status == "defer":
        if not until:
            raise DispositionError(
                "defer needs --until YYYY-MM-DD; a deferral with no return date "
                "is a silent reject")
        try:
            until_date = _parse_iso(until)
        except ValueError as e:
            raise DispositionError(str(e))
        if until_date <= _parse_iso(today):
            raise DispositionError(
                f"--until {until} is not in the future; it would come back pending "
                "on the same day")
        entry["defer_until"] = until
    else:
        if until:
            raise DispositionError(f"--until only applies to defer, not {status}")
        entry["defer_until"] = None

    entry["status"] = status
    entry["reason"] = reason.strip()
    entry["decided"] = today
    entry["decided_by"] = decided_by or os.environ.get("USER") or "unknown"
    return entry


# --- reconciliation ----------------------------------------------------------


def _index_names(brain_dir):
    names = set()
    for rel in ("INDEX.md", "INDEX-ARCHIVE.md"):
        path = os.path.join(brain_dir, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        names.update(m.group(1).strip() for m in WIKILINK_RE.finditer(text))
    return names


def _native_names(source_dir_root, source):
    """Names a native memory store holds.

    `dir`  — one markdown file per memory; the stem is the name (the layout every
             provider on this machine happens to use).
    `file` — a single index file; every `[[wikilink]]` in it is a name.
    """
    raw = source.get("path") or ""
    path = raw if os.path.isabs(raw) else os.path.join(source_dir_root, raw)
    path = os.path.expanduser(path)
    kind = source.get("kind", "dir")
    if kind == "dir":
        if not os.path.isdir(path):
            raise OSError(f"not a directory: {path}")
        names = set()
        for name in os.listdir(path):
            if not name.endswith(".md") or name == "README.md":
                continue
            if name.upper().startswith("INDEX"):
                continue
            names.add(name[:-3])
        return names
    if kind == "file":
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        return {m.group(1).strip() for m in WIKILINK_RE.finditer(text)}
    raise OSError(f"unknown source kind {kind!r} (expected 'dir' or 'file')")


def reconcile(brain_dir):
    """Name-set diff: each configured native store vs Loreport's INDEX.

    Never repairs. Reports `only_native` (the drift that matters — a memory the
    assistant knows and Loreport has never seen), plus counts for the rest.
    """
    cfg_path = os.path.join(brain_dir, RECONCILE_SOURCES_FILE)
    if not os.path.isfile(cfg_path):
        return {
            "status": "unconfigured",
            "source_count": 0,
            "sources": [],
            "detail": f"no {RECONCILE_SOURCES_FILE} — reconciliation is asserting nothing",
        }
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        sources = cfg["sources"]
        if not isinstance(sources, list):
            raise ValueError("'sources' is not a list")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, KeyError, ValueError) as e:
        return {
            "status": "error",
            "source_count": 0,
            "sources": [],
            "detail": f"{RECONCILE_SOURCES_FILE} unreadable: {e}",
        }

    if not sources:
        # The empty-collection trap this repo has shipped twice: iterating zero
        # sources and reporting "in sync" is vacuously true. Say so instead.
        return {
            "status": "unconfigured",
            "source_count": 0,
            "sources": [],
            "detail": f"{RECONCILE_SOURCES_FILE} lists no sources — reconciliation is asserting nothing",
        }

    indexed = _index_names(brain_dir)
    results = []
    worst = "in-sync"
    for source in sources:
        label = source.get("provider") or source.get("path") or "?"
        try:
            native = _native_names(brain_dir, source)
        except (OSError, UnicodeDecodeError) as e:
            results.append({"provider": label, "status": "unreadable", "detail": str(e)})
            worst = "error"
            continue
        only_native = sorted(native - indexed)
        entry = {
            "provider": label,
            "status": "in-sync",
            "native_count": len(native),
            "only_native_count": len(only_native),
            "only_native": only_native[:MAX_LISTED],
            "only_loreport_count": len(indexed - native),
            "both_count": len(native & indexed),
        }
        if not native:
            entry["status"] = "blind"
            entry["detail"] = "native store holds no items — nothing was compared"
            if worst != "error":
                worst = "blind"
        elif only_native:
            entry["status"] = "drift"
            if worst == "in-sync":
                worst = "drift"
        results.append(entry)

    if not indexed and worst != "error":
        # Same trap on the other side: every native item looks like drift against
        # an empty index, and an empty index compared to an empty store looks clean.
        worst = "blind"

    return {
        "status": worst,
        "source_count": len(sources),
        "indexed_count": len(indexed),
        "sources": results,
    }


# --- the nightly artifact ----------------------------------------------------


def attestation_path(brain_dir, day):
    return os.path.join(brain_dir, ATTESTATION_DIR, f"{day}.json")


def build_attestation(ledger, synthesis, refreshed, reconciliation, today):
    counts = {"pending": 0, "accept": 0, "reject": 0, "defer": 0}
    pending_rows = []
    for entry in ledger.get("proposals", {}).values():
        status = effective_status(entry, today)
        counts[status] = counts.get(status, 0) + 1
        if status == "pending":
            pending_rows.append({
                "id": entry.get("id"),
                "topic": entry.get("topic"),
                "signal": entry.get("signal"),
                "members": entry.get("members"),
                "pending_since": _clock_start(entry, today),
                "pending_days": _days_between(_clock_start(entry, today), today),
            })
    pending_rows.sort(key=lambda r: (-r["pending_days"], r["id"] or ""))
    overdue_ids = [r["id"] for r in pending_rows if r["pending_days"] > PENDING_MAX_DAYS]

    synthesis = synthesis or {}
    return {
        "schema": SCHEMA,
        "date": today,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "synthesis": {
            "detected": len(synthesis.get("clusters") or []),
            "item_count": synthesis.get("item_count", 0),
            "error": synthesis.get("error"),
            "new_proposals": refreshed["added"],
            "suppressed": refreshed["suppressed"],
            "dispositions": counts,
            "pending": pending_rows,
            "overdue": overdue_ids,
            "pending_max_days": PENDING_MAX_DAYS,
        },
        "reconciliation": reconciliation,
    }


def run(brain_dir, synthesis, today=None, dry_run=False):
    """The nightly pass. Returns a summary dict; raises nothing the caller has
    to catch for correctness, but the caller (brain_merge) still guards it — a
    review step must never take the merge down with it."""
    today = today or today_iso()
    ledger = load_ledger(brain_dir)
    refreshed = refresh(ledger, synthesis, today)
    reconciliation = reconcile(brain_dir)
    attestation = build_attestation(ledger, synthesis, refreshed, reconciliation, today)

    if not dry_run:
        if refreshed["changed"]:
            save_ledger(brain_dir, ledger)
        path = attestation_path(brain_dir, today)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(attestation, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)

    return {
        "date": today,
        "ledger_changed": refreshed["changed"],
        "attestation": attestation,
    }


def format_lines(summary):
    """Digest lines. Names the disposition queue explicitly, because the whole
    point is that a proposal is now a decision someone owes an answer to."""
    if not summary:
        return ["- Proposal dispositions: nightly review did not run"]
    if summary.get("error"):
        return [f"- Proposal dispositions: nightly review errored — {summary['error']}"]

    att = summary.get("attestation") or {}
    syn = att.get("synthesis") or {}
    counts = syn.get("dispositions") or {}
    lines = [
        "- Proposal dispositions (decisions, never auto-merges): "
        f"{counts.get('pending', 0)} pending, {counts.get('accept', 0)} accepted, "
        f"{counts.get('reject', 0)} rejected, {counts.get('defer', 0)} deferred"
    ]
    for row in (syn.get("pending") or [])[:MAX_LISTED]:
        flag = " OVERDUE" if row["id"] in (syn.get("overdue") or []) else ""
        lines.append(
            f"    - [{row['id']}] {row['topic']} [{row['signal']}] "
            f"pending {row['pending_days']}d{flag}"
        )
    if syn.get("suppressed"):
        lines.append(
            f"- Proposals suppressed by an earlier reject: {len(syn['suppressed'])}"
        )

    rec = att.get("reconciliation") or {}
    status = rec.get("status", "unknown")
    if status == "unconfigured":
        lines.append("- Reconciliation (native memory vs Loreport): NOT CONFIGURED — asserting nothing")
    elif status == "error":
        lines.append(f"- Reconciliation (native memory vs Loreport): config error — {rec.get('detail')}")
    else:
        drifted = sum(s.get("only_native_count", 0) for s in rec.get("sources", []))
        lines.append(
            f"- Reconciliation (native memory vs Loreport): {status}, "
            f"{rec.get('source_count', 0)} source(s), {drifted} item(s) only in native memory"
        )
    lines.append(f"- Nightly review artifact: {ATTESTATION_DIR}/{att.get('date')}.json")
    return lines


# --- CLI ---------------------------------------------------------------------


def _cmd_list(brain_dir, today):
    ledger = load_ledger(brain_dir)
    rows = sorted(ledger.get("proposals", {}).values(),
                  key=lambda e: (e.get("first_seen") or "", e.get("id") or ""))
    if not rows:
        print("no proposals on the ledger")
        return 0
    for entry in rows:
        status = effective_status(entry, today)
        extra = ""
        if entry.get("status") == "defer" and status == "pending":
            extra = f" (deferral expired {entry.get('defer_until')})"
        elif entry.get("status") == "defer":
            extra = f" (until {entry.get('defer_until')})"
        print(f"{entry.get('id')}  {status:8}{extra}  {entry.get('topic')} "
              f"[{entry.get('signal')}]  seen {entry.get('first_seen')}")
        if entry.get("reason"):
            print(f"    reason: {entry['reason']}  ({entry.get('decided')} by {entry.get('decided_by')})")
        print(f"    members: {', '.join(entry.get('members') or [])}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Nightly proposal dispositions + native-memory reconciliation")
    parser.add_argument("--brain-dir", required=True)
    parser.add_argument("--today", default=None, help="override today's date (tests)")
    parser.add_argument("--run", action="store_true",
                        help="run the nightly pass and write the dated artifact")
    parser.add_argument("--dry-run", action="store_true", help="with --run: write nothing")
    parser.add_argument("--list", action="store_true", help="list the ledger")
    parser.add_argument("--dispose", metavar="ID", help="record a disposition for a proposal")
    parser.add_argument("--status", choices=list(STATUSES))
    parser.add_argument("--reason")
    parser.add_argument("--until", help="with --status defer: YYYY-MM-DD to reconsider")
    parser.add_argument("--by", help="who decided (default: $USER)")
    args = parser.parse_args(argv)

    brain_dir = os.path.abspath(args.brain_dir)
    today = args.today or today_iso()

    try:
        if args.dispose:
            if not args.status:
                raise DispositionError("--dispose needs --status accept|reject|defer")
            ledger = load_ledger(brain_dir)
            entry = dispose(ledger, args.dispose, args.status, args.reason or "",
                            today, until=args.until, decided_by=args.by)
            save_ledger(brain_dir, ledger)
            print(f"{entry['id']}: {entry['status']} — {entry['reason']}")
            return 0
        if args.list:
            return _cmd_list(brain_dir, today)
        if args.run:
            # Standalone runs re-detect; inside the merge, brain_merge passes the
            # report it already computed rather than walking the brain twice.
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import synth_detect  # noqa: E402
            synthesis = synth_detect.detect_clusters(brain_dir)
            summary = run(brain_dir, synthesis, today=today, dry_run=args.dry_run)
            print("\n".join(format_lines(summary)))
            return 0
    except (LedgerError, DispositionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    parser.error("nothing to do: pass --run, --list or --dispose")


if __name__ == "__main__":
    sys.exit(main())
