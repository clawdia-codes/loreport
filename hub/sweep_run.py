#!/usr/bin/env python3
"""
hub/sweep_run.py — nightly runner that turns sweep candidates into provider commits.

hub/sweep_extract.py finds deterministic capture candidates and writes nothing;
this is the piece that commits them. It deliberately owns no scrubbing, no schema
rules and no git plumbing of its own — every candidate goes through the exact gate
chain hub/inbox_ingest.py already runs for an assistant-authored capture:

    parse_block → validate_schema → scan_secrets → scan_imperative → commit_block

so the secret scrub, the injection scan, the ownership check, the repo lock and the
provider-branch commit are the same code paths, not a second implementation that can
drift from them. A candidate failing any gate lands in hub/quarantine/<provider>/ and
shows up in the next merge digest, same as any other rejected capture.

Idempotence has two independent layers. The fingerprint ledger skips any candidate
already offered in an earlier run, and underneath it commit_block() returns
"skipped: no change" when the content is byte-identical to what is already on the
provider branch. Re-running the same window therefore produces zero new commits even
if the ledger is deleted.

Semantic duplicates are NOT this script's job. A swept statement often says the same
thing as a curated memory in different words; brain_merge's find_dupes catches only
exact bodies and identical descriptions, and folding the rest is consolidate.md's
work. The sweep's contract is "capture what was said", not "decide what it merges into".

ChatGPT has no local session logs, so it is never swept — see SWEEP_PROVIDERS.

CLI:
    python3 hub/sweep_run.py [--brain-dir PATH] [--window-days N] [--dry-run]
                             [--state-file PATH]
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import inbox_ingest as ingest  # noqa: E402
import sweep_extract as extract  # noqa: E402

# ChatGPT keeps no session log on this host. A sweep that emitted chatgpt-sourced
# candidates would be inventing provenance, so the provider is absent by design.
SWEEP_PROVIDERS = ("claude", "codex", "openclaw")

# The ledger lives OUTSIDE the brain repo on purpose. sync.sh halts the whole nightly
# pipeline — no publish, no push, Telegram alarm — if the working tree has modified
# tracked files after the merge, and the sweep runs 30 minutes before that merge.
DEFAULT_STATE_FILE = os.path.join(
    os.path.expanduser("~"), ".local", "state", "loreport", "sweep-state.json"
)


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"seen": {}}
    if not isinstance(data.get("seen"), dict):
        data["seen"] = {}
    return data


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def classify(outcome):
    """Map an outcome string to its summary bucket, or None for "don't count".

    Extracted so the MAPPING is testable rather than only the outcome string.
    The counter used to compare `outcome == "committed"`, so the moment the
    outcome grew a suffix — `committed (inferred: file, action)` — it silently
    stopped counting captures, and the nightly report would have under-reported
    exactly the class this suffix exists to make visible. A prefix match inline
    in run() would have fixed the bug and stayed just as unreachable from a test.
    """
    if outcome.startswith("committed"):
        return "committed"
    if outcome == "skipped: no change":
        return "no_change"
    if outcome.startswith("quarantined"):
        return "quarantined"
    return None


def process_candidate(brain_dir, candidate, block_path, trust):
    """Run one candidate through inbox_ingest's gate chain. Returns an outcome
    string; quarantine side effects are inbox_ingest's own."""
    provider = candidate["provider"]
    block, err = ingest.parse_block(candidate["block"])
    if err:
        reason = ingest.quarantine_reason(err)
        ingest.quarantine(brain_dir, provider, block_path, reason, err)
        return f"quarantined: {reason} ({err})"

    schema_err = ingest.validate_schema(block)
    if schema_err:
        ingest.quarantine(brain_dir, provider, block_path, "schema-invalid", schema_err)
        return f"quarantined: schema-invalid ({schema_err})"

    secret_hit = ingest.scan_secrets(block["raw"])
    if secret_hit:
        # Never echo the match itself into a log the sweep writes unattended.
        ingest.quarantine(brain_dir, provider, block_path, "secret-scan",
                          "matched a secret pattern")
        return "quarantined: secret-scan"

    imp_hit = ingest.scan_imperative(block["raw"])
    if imp_hit:
        ingest.quarantine(brain_dir, provider, block_path, "imperative-scan",
                          f"unattributed standing instruction: \"{imp_hit}\"")
        return "quarantined: imperative-scan"

    try:
        result = ingest.commit_block(brain_dir, provider, block, trust)
    except ingest.OwnershipError as e:
        ingest.quarantine(brain_dir, provider, block_path, "ownership-denied", str(e))
        return "quarantined: ownership-denied"
    except Exception as e:  # git failure — commit_block restored its own path only
        ingest.quarantine(brain_dir, provider, block_path, "git-error", str(e))
        return f"quarantined: git-error ({e})"

    if result == "skipped: no change":
        return "skipped: no change"
    # Say WHICH captures needed the recovery path. The outcome string is what
    # lands in the printed report AND in the sweep's state ledger, and the sweep
    # is the path the four real bare-tag emits came from — so a bare "committed"
    # here meant the frequent case had no record anywhere. Commit-side, the same
    # fact rides an `Inferred:` trailer.
    if block.get("inferred"):
        return f"committed (inferred: {', '.join(block['inferred'])})"
    return "committed"


def run(brain_dir, window_days, state_file, dry_run, trust):
    state = load_state(state_file)
    seen = state["seen"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    candidates = extract.scan_logs(None, window_days)
    summary = {
        "window_days": window_days,
        "found": len(candidates),
        "already_seen": 0,
        "committed": 0,
        "no_change": 0,
        "quarantined": 0,
        "skipped_provider": 0,
        "dry_run": bool(dry_run),
        "outcomes": [],
    }

    with tempfile.TemporaryDirectory(prefix="loreport-sweep-") as tmpdir:
        for candidate in candidates:
            fingerprint = candidate["fingerprint"]
            provider = candidate["provider"]

            if provider not in SWEEP_PROVIDERS:
                summary["skipped_provider"] += 1
                continue
            if fingerprint in seen:
                summary["already_seen"] += 1
                continue

            block_path = os.path.join(tmpdir, f"{fingerprint[:12]}.txt")
            with open(block_path, "w", encoding="utf-8") as fh:
                fh.write(candidate["block"])

            if dry_run:
                outcome = "dry-run: would offer"
            else:
                outcome = process_candidate(brain_dir, candidate, block_path, trust)
                seen[fingerprint] = {
                    "slug": candidate["slug"],
                    "provider": provider,
                    "kind": candidate["kind"],
                    "outcome": outcome,
                    "at": now,
                }

            bucket = classify(outcome)
            if bucket:
                summary[bucket] += 1
            summary["outcomes"].append(
                {"slug": candidate["slug"], "provider": provider, "outcome": outcome}
            )

    if not dry_run:
        state["last_run"] = now
        save_state(state_file, state)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Commit deterministic sweep captures")
    parser.add_argument("--brain-dir", default=None, help="Brain repo root")
    parser.add_argument("--window-days", type=int, default=1,
                        help="Rolling window to sweep (default: 1, matching a nightly run)")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE,
                        help="Fingerprint ledger (default: ~/.local/state/loreport/sweep-state.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be offered; commit nothing, remember nothing")
    parser.add_argument("--trust", choices=("local", "cloud"), default="local",
                        help="Trust tier for the commit. The sweep reads this host's own "
                             "session logs, so 'local' is the honest tier.")
    args = parser.parse_args()

    brain_dir = args.brain_dir or ingest.default_brain_dir()
    summary = run(brain_dir, args.window_days, args.state_file, args.dry_run, args.trust)

    for item in summary["outcomes"]:
        print(f"  {item['provider']:9s} {item['slug']:56s} {item['outcome']}")
    print(
        f"sweep_run: found={summary['found']} new={len(summary['outcomes'])} "
        f"committed={summary['committed']} no-change={summary['no_change']} "
        f"quarantined={summary['quarantined']} already-seen={summary['already_seen']}"
        + (" (dry-run)" if summary["dry_run"] else "")
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
