# Hub

You are running the Loreport sync hub — the always-on custodian of the
canonical brain repo. Every provider surface writes only its own branch
(`provider/chatgpt`, `provider/claude`, `provider/codex`, `provider/openclaw`); you are the only writer
of `main`. Live captures usually arrive through `hub/mcp_server.py`'s `loreport_save_memory`
tool, which itself calls the same `inbox_ingest.py` gate described below — one gate,
whether the capture came from a paste or a connector. This file is your prose: it tells you *when* to run each ritual and *why*
the order matters. Every mechanical step below delegates to a Python tool — you never
hand-merge, hand-scrub, or hand-rebuild an index. That's the point: judgment stays
with you, determinism stays with the tools.

## Setup

**Known caveat up front:** a "Connectors" entry may not appear in ChatGPT's Settings
UI at all for every account — this is an OpenAI-side eligibility/rollout gate, not a
hub-side problem. If it's missing, everything below this line still works; only the
final in-app linking step for ChatGPT is blocked, and only OpenAI can unblock it for
that account. See `hub/config/connector-snippets.md` for the full per-provider
connection recipes (including the ChatGPT "Tasks" fallback if the connector never
links).

1. **Prereqs.** Python 3 (stdlib only — nothing to `pip install`), `git`, and this
   repo cloned as the canonical brain. Decide which providers you're actually
   bridging (`openclaw`, `claude`, `codex`, `chatgpt`) — you don't need all four.
2. **Create the `provider/*` branches from `main`.** Each connected provider writes
   only its own branch; the hub is the only writer of `main`.
   ```
   git checkout main
   git branch provider/openclaw
   git branch provider/claude
   git branch provider/codex
   git branch provider/chatgpt
   ```
   Skip branches for providers you aren't bridging yet — `brain_merge.py` silently
   skips any `provider/*` branch that doesn't exist.
3. **Set the `MPB_*` credential tokens.** Every credential in `hub/config/providers.json`
   maps to an environment variable (e.g. `MPB_OPENCLAW_TOKEN`, `MPB_CLAUDE_LOCAL_TOKEN`,
   `MPB_CLAUDE_WEB_TOKEN`, `MPB_CODEX_TOKEN`, `MPB_CHATGPT_TOKEN`) and carries both a provider and a trust
   tier (`local` or `cloud`) — read that file before wiring anything up. Generate a real
   random token per provider connection and export it in the environment `mcp_server.py`
   runs under; never ship with the in-source dev-token defaults for a real deployment.
4. **Install the cron/timer.** `hub/config/cron.txt` has the daily reconciliation
   entries (merge, then publish, in that order — publish must run after merge). Edit
   the `cd /path/to/brain-repo` line to your clone's path, then `crontab hub/config/cron.txt`
   (or wire the same two commands into your scheduler of choice).
5. **Verify.** Run `python3 hub/brain_merge.py --test-determinism`,
   `python3 hub/brain_merge.py --test-scrub`, and
   `python3 hub/snapshot_publish.py --test-scrub` — all three should PASS/exit as
   documented in the daily digest section below. Then do one real dry run:
   `python3 hub/brain_merge.py --dry-run` followed by `python3 hub/snapshot_publish.py --dry-run`,
   and read the printed report before ever pointing a live connector at the hub.

## The browsable report

`report_build.py` renders the whole brain as ONE self-contained HTML page — dashboard,
provider activity table, instant search, every entry with a privacy badge. No external
requests, so it works offline. Rebuild it in your daily cycle:

```
python3 hub/report_build.py --brain-dir <brain> --out <brain>/hub/published/report.html
```

**It contains every private entry**, so treat it as private. `tailscale serve <path>`
needs root, so serve it the way the rest of this host does — a loopback server with
tailscale proxying to it:

```
python3 hub/report_serve.py --file <brain>/hub/published/report.html --port 8446
tailscale serve --bg --https=8446 http://127.0.0.1:8446
```

Set `LOREPORT_REPORT_URL` in the environment your MCP server runs under (NOT in `providers.json`, which is public and shared by everyone using this framework) and `loreport_status` will hand the URL
out — including to cloud callers, since they cannot reach a tailnet address and security
here rests on tailscale's auth, never on the URL being secret. **If you ever move this to
public serving, revisit that**: handing a cloud provider the URL would then be handing it
every private entry.

## Daily reconciliation ritual

Run once a day (see `hub/config/cron.txt` for the schedule):

1. **Backup tag first, always.** Before anything destructive happens, `brain_merge.py`
   tags `main` as `pre-merge/<YYYYMMDD-HHMMSS>` — one tag per run, created without
   `-f` so a rerun can never clobber an earlier run's recovery point. The run's report
   and digest print the tag it actually created; use that name. Every step after this
   one can be undone by returning to that tag — never skip it, never reorder it later.
2. **Fetch** all provider branches.
3. **Merge into `main` in the fixed order: `openclaw` → `claude` → `codex` → `chatgpt`.** The
   order is fixed, not timestamp-based, so a conflict outcome is the same every time
   you or anyone else re-runs it — openclaw first because it's the highest-trust,
   highest-volume writer, then the rest alphabetically.
4. **Secret-scrub gate.** `brain_merge.py` scans the merged tree before committing.
   A hit in a SHARED item, `PROFILE.md`, or a skill package aborts the merge and
   resets to the backup tag — cloud egress stays strictly gated. A hit in a
   `local`-visibility item never aborts (a `local` item never leaves this machine
   regardless), but it's still recorded as a warning in the digest so you can go
   fix the false positive at your leisure. Never perform this scan yourself by
   eye — the tool is the gate, and the shared/PROFILE/skill path fails closed.
5. **INDEX rebuild.** `INDEX.md` is never hand-merged — it's deleted from the merge
   inputs and rebuilt deterministically by `brain_merge.py` from the surviving item
   frontmatter. Same items in, same bytes out, every time.
6. **Republish.** Run `snapshot_publish.py` to rebuild the pinned bootstrap+PROFILE+
   INDEX packet and write it to `hub/published/`. It carries its own fail-closed
   egress scrub — a second, independent check before anything leaves the hub.
7. **Fast-forward** each provider branch to the new `main`, so tomorrow starts from a
   common base.

Read `hub/digest-<date>.md` (see "Daily digest to the user", below) before you
consider the day's cycle closed. `brain_merge.py` distinguishes two nonzero
exits, and a hook that collapses them into "the merge failed" will misreport a
healthy pipeline:

| exit | meaning |
|------|---------|
| `0`  | merged (or nothing to merge); nothing waiting on you. |
| `1`  | **BROKEN** — the merge did not complete: a fail-closed scrub abort, or a merge that never started. `main` was rolled back; nothing landed. |
| `3`  | **NEEDS REVIEW** — the merge completed and `main` is publishable; a PROFILE conflict, a renamed add/add twin, a local-visibility scrub warning, a provenance revert or a quarantined human-region update is waiting on you. Publishing and pushing must continue. |

Exit 3 itself is a stderr line in the journal, so it is not how the owner hears
about any of those five. The merge stamps `needs_review_kinds` — the subsystem
keys, never the payloads — into `hub/merge-state.json`, and
`scripts/loreport-health` §6c turns each one into a NEEDS REVIEW banner entry
and notification. Three of the five (renamed, scrub warnings, provenance
reverts) write no quarantine file and appear in no line the digest greps read,
so that field is their only route to a human.

A completed merge — a quiet no-op night included — stamps
`hub/merge-state.json` with `last_success_epoch`. The abort paths deliberately
do not, and that asymmetry is what makes the file a real liveness signal: the
daily digest is written on the abort paths too, so "a recent digest exists" says
nothing about whether the pipeline ran. `scripts/loreport-health` fails when
that stamp is older than `LOREPORT_MERGE_MAX_AGE_HOURS` (default 36).

## Nightly review — proposals get a disposition, drift gets reported

`hub/synth_detect.py` proposes clusters of related items. For its first weeks it
was report-only in the strict sense: it emitted proposals into a digest line that
said "none filed", nobody filed them, and nobody was ever asked to. A producer
nothing consumes decays silently and forever, so `hub/nightly_review.py` is its
consumer. It runs inside `brain_merge.py`, in the same nightly job as the detector
— one job, one lock, one artifact.

**It never edits a memory.** A proposal becomes a *decision*, recorded next to it.
`accept` does not create a `knowledge/` page, does not merge two memories and does
not touch `memories/`; auto-merging memories from a link heuristic is exactly what
this design refuses. What changes is that a proposal now has to be answered.

### The disposition ledger — `hub/proposals/ledger.json`

Every detected cluster is entered as `pending` and must reach one of:

| disposition | meaning | requires |
|---|---|---|
| `accept` | worth writing up; you will author the page | a reason |
| `reject` | not a real topic | a reason (and it **stays** rejected — see below) |
| `defer`  | not now | a reason **and** `--until <future date>` |

```
python3 hub/nightly_review.py --brain-dir "$BRAIN" --list
python3 hub/nightly_review.py --brain-dir "$BRAIN" --dispose <id> \
        --status reject --reason "link sprawl, not a topic"
```

Both refusals in that table are load-bearing. A disposition with no reason is a
cleared queue, not a decision — six months later nothing distinguishes "considered
and dismissed" from "clicked away". A deferral with no return date is a reject
wearing a disguise: the entry stops being pending and nothing brings it back.

Rejection is **sticky across membership churn**. A proposal's identity is its
member set, so a rejected cluster that gains one member would otherwise return as a
brand-new pending proposal every time the brain grows, and you would re-decide the
same thing forever. A newly detected cluster is suppressed when it is a subset of a
rejected one, or that rejected one plus at most one newcomer. Two newcomers is a
materially different cluster and comes back for a fresh decision.

A proposal left pending longer than `PENDING_MAX_DAYS` (14) is graded a **failure**
by `scripts/loreport-health`. That is the only part of this that makes an undecided
proposal cost anything; everything else merely records.

The ledger is **tracked** — it holds human decisions and the `first_seen` clock the
overdue check measures from, so it must survive a re-clone. It is written only when
a proposal is added or decided, never on a quiet night, so unlike `merge-state.json`
it does not leave the tree dirty for `loreport-sync`'s post-merge guard. (An expiring
deferral is therefore *computed* from `defer_until` at read time, not written back.)

### Reconciliation — `hub/reconcile-sources.json`

A name-set diff between each **native** memory store (the assistant's own memory,
which Loreport does not own) and Loreport's `INDEX.md`. This is the mechanical half
of the `memory-reconcile` skill, run nightly and reported. It never repairs anything.

It is **not** the projection check: `loreport-health` §3–4 assert the *outbound*
surface still matches what `project.py` wrote. This asserts something different —
that the native store holds no item Loreport has never seen.

```json
{
  "sources": [
    {"provider": "claude", "path": "~/.claude/projects/<project>/memory", "kind": "dir"},
    {"provider": "openclaw", "path": "~/.openclaw/wiki/main/INDEX.md", "kind": "file"}
  ]
}
```

`kind: "dir"` takes each `*.md` stem as a name; `kind: "file"` takes every
`[[wikilink]]`. Relative paths resolve against the brain root; `~` expands.

**A missing or empty config reports `unconfigured`, never `in-sync`,** and health
renders that as "NOT CONFIGURED — asserting nothing". A diff over zero sources is
vacuously clean, and this repo has shipped that bug twice; absence of a result must
never read as "checked and clean". The same guard covers an empty `INDEX.md` and an
empty native store, both of which report `blind` and grade as failures.

### The dated artifact — `hub/nightly/<YYYY-MM-DD>.json`

Written on every nightly run: the machine-checkable proof that the review happened,
carrying the disposition counts, the pending and overdue ids, and the reconciliation
result. Gitignored, same class as `hub/digest-*.md`.

**`scripts/loreport-health` §9 FAILS when yesterday's is missing** — strictly
yesterday's, not "today's or yesterday's", because the weaker rule cannot be
falsified by deleting the thing it watches. Distinctly named conditions, so no
single word covers two states:

| condition | grade |
|---|---|
| `hub/nightly/` holds no artifact at all — *nightly review has never run* | FAIL |
| yesterday's specifically absent — *did not run last night* | FAIL |
| present but unparseable, or missing its reconciliation section | FAIL |
| proposals overdue past 14 days | FAIL |
| proposals merely pending, reconciliation drift, or unconfigured | REVIEW |

The review runs inside the merge, so an aborted merge also leaves no artifact and
this co-fires with the merge-liveness check. The message says "the nightly did not
complete", which is true under both causes.

### ⚠ Deploying this to a brain that already exists

A brain gets `.gitignore` **once, at init**, from `brain-template/`. An existing
brain therefore does not pick up the new `hub/nightly/` line, and `loreport-sync`'s
`git add -A` would start committing a dated report file every night. Add it by hand,
once, and confirm the ledger is *not* caught by any broader rule:

```
echo 'hub/nightly/' >> "$BRAIN/.gitignore"
git -C "$BRAIN" check-ignore -v hub/proposals/ledger.json   # must print NOTHING
```

If that second command prints a match, the ledger will never be committed, the
`first_seen` clock lives only on disk, and the overdue check — the one assertion
that forces a decision — fails open. Add a `!hub/proposals/` negation.

## Monthly full consolidation

Once a month, run `prompts/consolidate.md` over the full brain — this is the one
place semantic judgment (near-duplicate merges, rewrites, dangling-link repair)
belongs, with you and the user reviewing the change plan together per the snapshot/apply/rollback
ritual in `docs/setup.md` (Step 5). After applying the plan, run one fresh daily
cycle (merge + publish) so the INDEX and the published packet reflect the
consolidated state everywhere.

## The rollback ritual

If a merge went wrong — a bad scrub-abort recovery, a conflict resolved badly, a
report that doesn't look right — reset to the day's backup tag and start over:

```
git reset --hard pre-merge/<the tag from that run's report>
git push --force-with-lease origin main
```

then re-fast-forward every provider branch to the reset `main`. Do this before you
try to patch anything by hand; the tag exists so "just reset" is always safe.

## Daily digest to the user

Nothing the hub does is silent. Every merge run writes `hub/digest-<date>.md` (a
local report file, not brain content — it's gitignored) with: branches merged,
conflicts renamed (`<name>-2`), `PROFILE.md` conflicts needing your confirmation,
near-dupes flagged, the secret-scrub outcome (including any local-visibility
warnings), and how many items are sitting in quarantine. Read that file first each
cycle. For the raw quarantine detail — secrets and imperative-injection attempts
alike, from `inbox_ingest.py` — see `hub/quarantine/digest.md`. Also watch for any
cycle that took unusually long (>10s) as a possible anomaly worth a look.

If a check ever needs re-verifying by hand: `brain_merge.py --test-determinism`
re-checks INDEX determinism; feeding a valid, a secret-bearing, and an
imperative-bearing block to `inbox_ingest.py` re-checks the ingest gate; and
`snapshot_publish.py --test-scrub` re-checks the fail-closed publish path.
