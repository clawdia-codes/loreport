# MAESTRO post-mortem — Model-Proof Brain run (xdkrhz), 2026-07-22/23

Overnight unattended compose run. It succeeded, but only after ~5 windows of firefighting.
The failures cluster into a few root causes. **The common thread: MAESTRO fails silently —
timeouts and empty stages report as success — and it's tightly coupled to the invoking
session's lifecycle, which makes headless/unattended runs fragile.** Ordered by severity.

---

## P0 — Silent failures that report as success (the dangerous class)

### 1. Producer 0-file timeout converges as "done"
- **Symptom:** a producer hits its hard time cap and emits `✓ … 0 file(s)`; the stage
  treats this as completion and advances/converges.
- **Impact:** the build stage ran to a **20-min cap having written 0 files (only mkdir'd
  empty dirs), and the build+results gates AUTO-CONVERGED — the run was marked `complete`
  on an empty repo.** Only caught by a human eyeballing the tree.
- **Fix:** a producer that finishes with 0 new files (or fails its own acceptance) must be
  a **retry/fail**, never a converge. Gates must run an **objective acceptance check
  against the artifact** (do the expected files exist / match the task-graph?) before
  passing — not rubber-stamp the producer's self-report.

### 2. Build/results gates have no board review
- **Symptom:** unlike discover/architect/design (10-lens board), the build and results
  gates converged with **no independent check**.
- **Impact:** compounded #1 — nothing between "producer said done" and "run complete."
- **Fix:** give build/results a real gate: a checker that verifies the produced repo
  against the plan's per-task acceptance greps before allowing `complete`.

### 3. Status shows stale "running"
- **Symptom:** `maestro_status` reported a producer `running…` long after it was dead.
- **Impact:** wasted polling; masked the session-death bug (#4). We had to judge liveness
  by **file mtimes under `docs/`**, not status events.
- **Fix:** status liveness should reflect the actual producer process / recent writes, and
  surface "no output in N min" as a warning.

---

## P1 — Lifecycle coupling breaks unattended runs

### 4. Ending the turn kills the producer (ROOT CAUSE of attempts 1–3 dying)
- **Symptom:** to "wait" for a producer, the driving session ended its turn — but the
  `claude -p` process exit **killed the MCP server and its producer children.**
- **Impact:** three consecutive design-revision producers died at 0 files; looked like #1.
  The only workaround was **babysitting in-turn** with blocking `Bash` waits (never ending
  the turn while a producer runs) — see `watch.sh`. That's fragile and undocumented.
- **Fix:** producers/board should run **detached** from the invoking session (own process
  group / daemonized), so a driver session can exit and reconnect. This is the single
  highest-leverage fix for headless/scheduled operation.

### 5. `continuity=resume` from a "running" state mis-triages into a NEW riff lane
- **Symptom:** rehydrating a run whose persisted status was `running` (mid-producer)
  re-ran triage and spawned a **new riff/change-brief lane on the stub goal**, polluting
  `run-state.json`.
- **Impact:** required manual **state surgery** (hand-editing `run-state.json`
  `running`→`stopped`, keeping backups) to recover the compose run. Did this 3×.
- **Fix:** `continuity=resume` must detect a dead-producer "running" state and re-enter the
  **same stage of the same lane**, never re-triage. Only rehydrate-as-new from clean
  `stopped`/`awaiting-human`.

### 6. Gate feedback (request-changes) not persisted
- **Symptom:** a `request-changes` feedback string sent at a gate was **lost when the
  session died** before the producer consumed it.
- **Impact:** the 2-tier correction had to be re-sent at the design gate after recovery.
- **Fix:** persist gate feedback to `run-state` synchronously on `maestro_approve`, so a
  fresh-session resume carries it. (Also: `maestro_resume` takes `feedback` but is
  same-session-only; a fresh process must use `maestro_run continuity=resume`, which has
  **no feedback param** — so injected feedback can only ride a gate. That gap forces the
  awkward "resume → wait for gate → re-send feedback" dance.)

---

### 6b. Background progress never reaches the launching terminal (no push-back channel) — *user-reported*
- **Symptom:** scheduling/resume works, but the interactive terminal that **launched** the
  run stays silent while work continues in the separate headless resume processes. Progress
  lands only in `JOURNAL.md`, which the user has to know to open. The user came back to a
  dark terminal and had to ask "how did it go?" — there was no signal that anything was
  happening, had finished, or was stuck.
- **Impact:** zero live visibility during the long unattended leg; the user can't supervise
  or intervene without manually inspecting files.
- **Root cause:** headless resumes are separate processes that cannot write into the
  original TUI (frozen awaiting input), and the launching session usually budget-halts
  anyway — so nothing is left alive to report into.
- **Fix (want: a response mechanism that reaches the launching terminal / the user):**
  - **Primary — out-of-band push:** emit each milestone (resume, task done, halt, gate,
    complete, blocker) to a channel the user already watches — the **Telegram/Clawdia
    channel** — so status arrives regardless of terminal state. One dense line per event,
    same content as the JOURNAL entry.
  - **For the launching terminal specifically (while it's alive):** the launching session
    arms a lightweight **`JOURNAL` watcher** (background `tail`/mtime poll) that surfaces
    new journal lines into that session as they land, degrading to Telegram/`notify-send`
    when the launching session is halted.
  - **Minimum viable:** a `notify` hook fired on milestone events that appends to a
    well-known status file **and** pings Telegram/desktop — decoupled from any one session's
    lifetime.
- **Note:** the budget-sprint layer already *writes* the JOURNAL faithfully; the gap is
  purely the **push** — nothing reaches out, so a quiet terminal is indistinguishable from a
  dead one.
- **STATUS 2026-07-23 — IMPLEMENTED (Telegram + terminal-wake).**
  - *Out-of-band push:* `report.sh log` now fires a notifier hook (resolves
    `$SPRINT_NOTIFY_CMD` → `<plan_dir>/.notify-cmd` → `~/.config/budget-sprint/notify-cmd`).
    `~/bin/tg-notify.sh` sends via Clawdia's Telegram bot (creds read from `openclaw.json`);
    `~/bin/sprint-notify-tg.sh` wraps it; the global symlink enables it for **every**
    budget-sprint. So all milestones now reach the user's phone regardless of session state.
  - *Terminal-wake:* `~/bin/journal-wait.sh` blocks until the JOURNAL gains a milestone, then
    prints it — a live driving session runs it as a background job, so its completion
    re-invokes the agent to relay into the terminal and re-arm. Covers the "session still
    alive" case; Telegram covers the halted case.

---

## P2 — Robustness & ergonomics

### 7. Executor lane spawn-crashes, silently, at an internal attempt cap
- **Symptom:** the executor crashed at spawn twice (`4s, 0 files`) — likely an internal
  retry/attempt cap — with no surfaced error. Forced a fallback to hand-authoring the
  remaining files via Sonnet subagents.
- **Fix:** surface executor spawn failures as errors; make the attempt cap visible.

### 8. In-place mode silently wipes "foreign" files under projectRoot
- **Symptom:** MAESTRO's tree snapshot/restore **deleted `.sprint/PLAN.md` + JOURNAL.md**
  created inside the project dir (same mechanism that "discards out-of-scope writes").
- **Fix:** document that `projectRoot` is exclusively MAESTRO's during a run; external
  tooling must live outside it. Ideally provide a designated writable side-channel.

### 9. Producer buffers all writes to the end
- **Symptom:** producers wrote nothing until near the end, so a mid-run timeout lost
  everything. The fix that worked was steering: *"start writing within 2 min, save per
  file."*
- **Fix:** encourage/require incremental file writes so a timeout still lands partial work.

---

## What actually got it across the line
Babysitting producers in-turn (never ending the turn mid-producer), surgical short
feedback with anti-timeout instructions, a one-file discriminating test before trusting a
build pass, and — when the executor gave out — hand-authoring via Sonnet subagents against
the approved task-graph, with all verification (S-1..S-7) done independently rather than
trusting agent self-reports. Net: the design/plan/board quality was excellent; the
**execution lane and unattended-lifecycle handling** are where the fixes are needed.
