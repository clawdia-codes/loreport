# Hub

You are running the Loreport sync hub — the always-on custodian of the
canonical brain repo. Every provider surface writes only its own branch
(`provider/chatgpt`, `provider/claude`, `provider/openclaw`); you are the only writer
of `main`. Live captures usually arrive through `hub/mcp_server.py`'s `brain_capture`
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
   bridging (`openclaw`, `claude`, `chatgpt`) — you don't need all three.
2. **Create the `provider/*` branches from `main`.** Each connected provider writes
   only its own branch; the hub is the only writer of `main`.
   ```
   git checkout main
   git branch provider/openclaw
   git branch provider/claude
   git branch provider/chatgpt
   ```
   Skip branches for providers you aren't bridging yet — `brain_merge.py` silently
   skips any `provider/*` branch that doesn't exist.
3. **Set the `MPB_*` credential tokens.** Every credential in `hub/config/providers.json`
   maps to an environment variable (e.g. `MPB_OPENCLAW_TOKEN`, `MPB_CLAUDE_LOCAL_TOKEN`,
   `MPB_CLAUDE_WEB_TOKEN`, `MPB_CHATGPT_TOKEN`) and carries both a provider and a trust
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

## Daily reconciliation ritual

Run once a day (see `hub/config/cron.txt` for the schedule):

1. **Backup tag first, always.** Before anything destructive happens, `brain_merge.py`
   tags `main` as `pre-merge/<date>`. Every step after this one can be undone by
   returning to that tag — never skip it, never reorder it later.
2. **Fetch** all provider branches.
3. **Merge into `main` in the fixed order: `openclaw` → `claude` → `chatgpt`.** The
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
consider the day's cycle closed. `brain_merge.py` exits nonzero whenever that
digest needs your attention — a PROFILE conflict, a renamed add/add twin, or a
local-visibility scrub warning — so a cron/notify hook can catch it without you
having to remember to look.

## Monthly full consolidation

Once a month, run `prompts/consolidate.md` over the full brain — this is the one
place semantic judgment (near-duplicate merges, rewrites, dangling-link repair)
belongs, with you and the user reviewing the change plan together per the `docs/
load-paths.md` apply/rollback ritual. After applying the plan, run one fresh daily
cycle (merge + publish) so the INDEX and the published packet reflect the
consolidated state everywhere.

## The rollback ritual

If a merge went wrong — a bad scrub-abort recovery, a conflict resolved badly, a
report that doesn't look right — reset to the day's backup tag and start over:

```
git reset --hard pre-merge/<date>
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
