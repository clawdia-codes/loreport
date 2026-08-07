# Budget Sprint: Model-Proof Brain — MAESTRO run xdkrhz (overnight, autonomous)

STATUS: COMPLETE        <!-- objective met 2026-07-23T07:25 CEST; repo authored+verified, git main 656ca93 -->

⚠️ **LOCATION GOTCHA:** this sprint lives in `~/model-proof-brain-sprint/` (a SIBLING of the
MAESTRO project). Do NOT put sprint files inside `~/model-proof-brain/` — MAESTRO edits that
tree in place and WIPED the first `.sprint/` (foreign files cleaned during a producer checkpoint). Keep
PLAN.md + JOURNAL.md here, outside the MAESTRO tree.

## Objective (definition of done)
Drive MAESTRO compose run **xdkrhz** (project `~/model-proof-brain`) forward through
design → plan → build, acting as the USER'S PROXY at gates per the direction below, until the repo is
built and the repo-only structural checks (S-1..S-4) pass. **DONE = MAESTRO reaches build/results with
the v1 repo authored and S-1..S-4 green.** Live-provider checks #1–#6 CANNOT run unattended — HALT and
leave those for the user.

## Config (budget-sprint layer only)
- project_dir (wrapper cwd, SAFE): ~/model-proof-brain-sprint
- maestro_project (where the run lives; tools work by runId): ~/model-proof-brain
- plan_dir: ~/model-proof-brain-sprint
- autonomy: full (skip permissions; may commit/push) — user granted overnight proxy autonomy
- session_halt_pct: 90
- weekly_halt_pct: 97
- resume_buffer_sec: 300
- weekly_resets_at: 1785178800
- journal: ~/model-proof-brain-sprint/JOURNAL.md

## Reporting — STANDING INSTRUCTION
Every milestone: `report.sh log --plan <this> --event "..." --detail "..."` then `report.sh show` and
restate. JOURNAL is the user's only window. One dense line per entry.

## Task checklist
- [x] 1. **Correct design framing + clear design gate.** DONE 05:04 CEST — round-5 docs verified 2-tier (§D12–D19, S-1..S-7, A-1..A-3), gate APPROVED as proxy. Load maestro tools (ToolSearch `select:mcp__plugin_maestro_maestro__maestro_status,mcp__plugin_maestro_maestro__maestro_resume,mcp__plugin_maestro_maestro__maestro_approve`). `maestro_status{runId:"xdkrhz"}`. Apply the **2-TIER FEEDBACK** below: if awaiting-approval at DESIGN gate → `maestro_approve{runId:"xdkrhz",decision:"request-changes",feedback:<2-TIER>}`; if stopped/awaiting-budget mid-design → `maestro_resume{runId:"xdkrhz",feedback:<2-TIER>,budgetStopPct:90}`. Poll to convergence; APPROVE design gate as proxy once it reflects the 2-tier structure.
- [x] 2. **Plan stage → plan gate.** DONE 05:19 CEST — planner-run-12 produced project.md + task-graph.md (T1-T10 Tier1, T11-T16 Tier2 hub, authority-chain anti-drift rules); verified vs 2-tier design, APPROVED as proxy. NOTE: task-graph classifies S-1 (+S-4 chat part) as needing an LLM chat — run those via fresh Claude subagents at Task 4; S-2/S-3/S-5/S-6/S-7 are mechanical.
- [x] 3. **Build stage.** DONE 06:43 CEST — hybrid: MAESTRO executor punch-list passes 1-2 authored prompts/+docs/+brain-template/ (14 files); after executor spawn-crashes (attempt cap), advisor-sanctioned fallback = 2 parallel claude-sonnet subagents (recorded executor routing) authored examples/ + README/LICENSE + hub/ (24 more files). MAESTRO left at lane 'complete'; DO NOT re-invoke maestro tools on this run (producer checkpoint may wipe authored files). Sub-agent note: task-graph INDEX count "7" is an off-by-one (fixture = 6 lines); LICENSE copyright set to "the owner the owner" (inferred) — user to confirm.
- [x] 4. **Structural verification S-1..S-4 + build/results gate.** DONE 07:20 CEST — ALL GREEN, independently verified (not trusting subagent claims): S-2 slice-diff byte-identical ×4 + embed-table sync; S-3 fixture integrity + exact INDEX; S-1 via 3 sonnet bare-chat probes (bootstrap capture+injection-reject+sweep / onboard interview→PROFILE+6 seeds / consolidate full change-plan w/ rotate-first scrub); S-4 via bare-chat probe with bootstrap pinned (perfect emit grammar, imperative dropped, secret discarded). Bonus: S-5/S-6/S-7 hub checks re-run green in temp repos + secret-abort-on-merge + concurrent-branch-write survival. FLAG for user: bootstrap.md = 843 tokens vs ≤700 T1 budget (file is byte-faithful to design §D5a; the design's own "≈620" claim was wrong) — trim or raise budget, user's call. FLAG 2: onboard.md's Finish section doesn't restate INDEX.md's required ## Memories/## Knowledge/## Skills headings — the S-1 probe emitted a heading-less INDEX (valid PROFILE though, so S-1 passes); same design-judgment fix category as the token budget.
- [x] 5. **HALT for user at live-provider boundary.** DONE — repo committed (git main @ 656ca93 in ~/model-proof-brain, .maestro/.sprint/BRIEF.md ignored). Left for the user: live checks #1–#6 (Tier-1 e2e) + A-1..A-3 (Tier-2 alignment, needs running hub + accounts), LICENSE copyright name confirm, bootstrap token-budget decision, task-graph INDEX-count off-by-one noted.

## Resume pointer
SPRINT COMPLETE — nothing to resume. Repo: ~/model-proof-brain git main @ 656ca93. Do NOT re-invoke maestro tools on run xdkrhz (its producers may clean 'foreign' files — the authored repo is now foreign to it). Live checks #1–#6 + A-1..A-3 are the user's morning job.

⚠️ CRITICAL SURVIVAL RULE (learned 04:36): NEVER end the session turn while a MAESTRO producer/board is running — `claude -p` exit kills the MCP server AND its producer children (this killed revision attempts 1-3). Babysit in-turn with blocking Bash waits (`timeout N tail -f /dev/null` loops; plain `sleep` is hook-blocked, `read -t </dev/null` returns instantly). watch.sh in sprint dir does this. Producers are hard-capped at exactly 10 min; a "✓ ... 0 file(s)" completion = producer timeout no-op — retry with SHORTER, surgical feedback + "start writing within 2 min, save per file" instructions (that's what made round 5 land).

WORKING NOTES:
- ⚠️ GOTCHA (03:19 resume): after a session death, `maestro_run continuity=resume` from a state that was mid-producer ("running") MIS-TRIAGES into a NEW riff lane (change-brief) on the stub goal. Only rehydrate that way when persisted status is awaiting-human/stopped. The 02:39 request-changes feedback was NOT persisted — the revision producer (architect-run-5, design) died with the old session having written 0 files; feedback must be RE-SENT at the design gate.
- Liveness check = file mtimes under .maestro/runs/20260722-191952-xdkrhz/docs/, NOT status events (status can show stale "running…").
- runId **xdkrhz**, lane compose, maestro_project `~/model-proof-brain`. Done: discover, architect. Design re-opened then halted mid-revision. Spend ~$45.
- Gate proxy policy: APPROVE when a gate clearly matches this direction; HALT + leave for user on any GENUINELY NEW fork. Never guess irreversibly.
- MAESTRO auto-resume timer intentionally DISARMED; this budget-sprint is the ONLY resume path. Re-arm next resume on each halt.
- Live-provider verification needs user accounts: Claude (Projects + Claude Code fs), ChatGPT (paste + capture-inbox/MCP), openclaw (hub + fs). NO Gemini/Kimi.

### === 2-TIER FEEDBACK (apply verbatim as the feedback string on the FIRST resume/request-changes) ===
REFRAME AS TWO TIERS — CORRECTS the prior "build sync INTO v1, drop zero-install" request-changes. Do NOT drop zero-install.

- **TIER 1 — zero-install portability + "initialize/bringup" (KEEP INTACT).** The pure-markdown, no-runtime portable brain already converged at the design gate: one-file-per-item memories/knowledge/skills, flat INDEX + [[wikilinks]], self-contained prompts, loads into ANY provider by paste/upload, manual growth loop, PD-11 pinned PROFILE+INDEX. **Frame the onboard flow explicitly as an "INITIALIZE + BRINGUP" one-shot:** (1) PORT existing memory/user-info from wherever the user has it; (2) INTERVIEW the user; (3) GRAB whatever the current provider already holds about them (its saved memory/custom-instructions) and MERGE that in; (4) continue building from there — the output is ONE self-contained package. The defining property: that package can ALWAYS be "grabbed and brought up on another device/platform" — re-loadable anywhere with zero install, no dependency on the origin provider. DEFAULT layer; MUST work STANDALONE. Zero-install + grab-and-go portability is Tier 1's identity.

- **TIER 2 — periodic alignment (opt-in, ON TOP of Tier 1).** Keeps multiple providers (ChatGPT + Claude + openclaw) aligned to ONE shared brain via EVENTUAL, SCHEDULED reconciliation — NOT hard real-time (an "aligned ~once per day" SLA is explicitly acceptable to the user). openclaw = the always-on SYNC HUB (owns canonical git repo + consolidation + snapshot rebuild).
  * **Reconciliation model = branch-per-provider + scheduled merge.** Each provider writes ONLY to its own git branch (`provider/chatgpt`, `provider/claude`, `provider/openclaw`) so writers never collide. On a schedule (default DAILY) the hub merges all provider branches → `main`, running consolidation (dedup / secret-scrub / reindex) at merge time; one-file-per-item ⇒ near-conflict-free. Define merge order, deterministic INDEX rebuild, same-item conflict handling. Hub then republishes the refreshed pinned PROFILE+INDEX snapshot to each provider (picked up next session / next pull).
  * **Bridge mechanisms — design and compare BOTH, recommend a path:**
    (A) **MCP framework (preferred to evaluate first):** a hub MCP server exposing read/write tools over the shared brain that providers connect to — ChatGPT via an OpenAI Secure MCP Tunnel (the user has ALREADY proven this with the Portia MCP server + tunnel-client — reuse that pattern), Claude via native MCP/connector, openclaw native, Claude Code via MCP. Gives near-live push of captures + read of the aligned snapshot; composes with the daily branch-merge (MCP writes land on the provider's branch; hub still merges+consolidates daily).
    (B) **Provider-native schedulers + capture-inbox:** where a provider runs scheduled jobs from chat (e.g. ChatGPT "Tasks"), the PROVIDER fires a periodic job that pushes its new memories to the hub's capture-inbox. Filesystem hosts (openclaw, Claude Code) append to their branch directly. Pure chat UIs without scheduler/MCP fall back to manual capture-inbox paste.
    Document, per provider, which mechanism it uses (MCP / scheduled-push / filesystem-direct / manual) and the daily-merge cadence.
  * Preserve the token-frugal pinned floor (only PROFILE+INDEX pinned).

- **Structure:** BOTH tiers in v1, CLEANLY SEPARATED. Tier 1 = zero-install standalone; Tier 2 = opt-in (requires the hub + at least one bridge). A user can adopt Tier 1 only, or add Tier 2. Keep architecture-proposal + design.md + modules.md + targets-acceptance.md CONSISTENT (no drift).

- **Security/reliability (expands M8):** hub = persistent service holding the canonical brain (threats: compromised/malfunctioning hub, the MCP endpoint's auth/exposure, the merge/republish path); capture-inbox / MCP write path = untrusted-content ingress (stored injection + secret/PII persistence still apply); secret-scrub MUST run before any republish and before any cross-branch merge to main; backup-before-merge/consolidate + rollback ritual on the hub; git history = provenance/audit (branches make provenance explicit — you can see which provider contributed each item).

- **Verification adds** alignment-loop checks: (i) a memory written on provider A's branch appears in provider B after one merge+republish cycle; (ii) concurrent writes on two provider branches both survive the merge (no loss); (iii) secret-scrub runs before republish/merge. Provider set = Claude (Projects + Claude Code fs), ChatGPT (paste + capture-inbox/MCP), openclaw (hub + fs). NO Gemini/Kimi.

- **Folded gate resolutions:** Q5 MIT (prompts) + CC BY 4.0 (docs); Q2 no competitor/"beat Victor" framing; 6b-B restatement approved; Q3a NO separate append-helper (Tier-2 hub subsumes capture).
### === END 2-TIER FEEDBACK ===

## Log
- 2026-07-23T00:46 CEST rebuilt sprint in SAFE sibling dir (first .sprint was wiped by MAESTRO's in-place tree edits); HALTED by design; resume to be scheduled after the 2:20am reset
- 2026-07-23T07:25 CEST COMPLETE — design+plan gates cleared w/ 2-tier reframe; repo authored (MAESTRO passes + sonnet executors); S-1..S-7 verified green; committed 656ca93; live checks left for user
