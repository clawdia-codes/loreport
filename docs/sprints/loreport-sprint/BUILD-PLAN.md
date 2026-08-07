# Loreport build — visibility feature + fable review recipe

Durable checkpoint for the multi-phase build (2026-07-24). Framework repo `~/projects/loreport`.
Spec: `docs/visibility-design.md`. Findings: `REVIEW.md`. Sonnet implements; I verify gates + commit.

STATUS legend: [ ] todo · [~] in progress · [x] done+verified

## Phase A — config + visibility field (foundational)  [x] b94674b — verified maps chatgpt/cloud, claude-local/local, openclaw/local
- providers.json (providers{branch,merge_order} + credentials{provider,trust}) — fixes REVIEW #22
- inbox_ingest: accept/validate/preserve `visibility: shared|local` (default shared) — spec §1
- all three hub scripts read providers.json (fallback to defaults; keep LIVE tokens working)
- format-spec.md documents `visibility`
- GATE: providers.json parsed by all scripts; capture preserves visibility:local; absent=shared;
  live ChatGPT tunnel (MPB_CHATGPT_TOKEN) + Claude Code (claude-local-dev-token) still work.

## Phase B — enforcement + tools + rename (security core)  [x] 8a2df27 — independently verified cloud≠local read, packet excludes local, tunnel restarted live
- rename brain_* → loreport_* (save_memory/read_memory/search_memories/load_context) + 2 new tools
- trust-aware reads: cloud caller can't read/search/surface `local` items
- loreport_view_memory_settings / loreport_change_memory_settings (gating: local=any, cloud=own)
- snapshot_publish excludes local from packet+INDEX
- GATE (spec §7): local item unreadable by cloud caller + absent from packet, readable by local;
  cross-caller set-visibility refused; shared item round-trips; restart tunnel, re-verify connector.

## Phase C — concurrency + read correctness (REVIEW P1)  [x] e327f99 — verified reads-from-main, determinism, tunnel live. TODO(E): flock on change_memory_settings write.
- reads via `git show main:` (#6); worktree-per-op + flock + CAS ff (#5); merge classifier (#7);
  PROFILE precedence ours/quarantine (#8); wrap commit_block + no-op (#13); post-merge scrub
  fail-closed on exception (#18); subprocess timeouts (#19)
- GATE: concurrent capture+merge loses nothing; reads always from main; INDEX determinism intact.

## Phase D — scrub + imperative + onboarding (REVIEW P2 + spec §5)  [x] ef73a9a — imperative FP fixed, broader secrets, onboard/bootstrap visibility grammar (spec b79536a)
- broaden secret corpus + entropy (#9); scope imperative-scan + scan frontmatter/INDEX (#10,#21);
  onboard.md explicit global-vs-local step + import classification (spec §5); bootstrap.md
  visibility grammar + fetch-before-update rule (spec §5, #16)
- GATE: "User always prefers X" no longer quarantined; onboarding tags visibility; broader secrets caught.

## Phase E — docs + polish (REVIEW P3/P4)  [x] 2fafd8c — scrub scoped (local=warn, shared=fail-closed), change_settings flock, digest+nonzero-exit, HUB.md setup, tier rename, jargon strip, atomic publish. Real-brain merge verified exit-0 clean; backup in sync.
- digest artifact + notify + nonzero exit (#15); INDEX archive policy + reword frugality (#17);
  tier-name collision (#23); disable-native-memory rec (#24); HUB.md Setup (#25); strip jargon (#26);
  --test-scrub exit code (#20); atomic publish; LOW cluster as capacity allows.

## Sprint N — capture parity + reconcile (2026-07-24)  [x] edec63b — all gates PASS on live brain (annotated hits w/ source, local still cloud-refused, skill lines [source: —]); skills installed in brain e84c7ce; tunnel restarted; both repos pushed. Remaining: user adds capture-parity line to ChatGPT project instructions + first live "reconcile my memories" run in ChatGPT.
User's model refinement: native memory stays ON; Loreport is the cross-provider truth layer.
1. skills/capture-parity/ — shadow-capture ("whenever you'd save native, also loreport_save_memory")
   + recall preference (native primary for own facts, Loreport for cross-provider) + sweep tie-in
2. skills/memory-reconcile/ — recallable diff: dump native → match=skip / missing=add / conflict=UPDATE;
   junk filter; sensitive→local
3. mcp_server.py — search hits annotated with source: (provenance-aware recall; NO filtering)
4. onboard.md Phase 2 import → "run memory-reconcile"; bootstrap.md + providers.md/README:
   rec flips from "disable native" to "native ON + capture parity" (both modes documented)
5. Install both skills into live brain + INDEX (skill) lines; restart tunnel
GATE: search shows source on hits; reconcile triage correct on a pasted native dump;
   local items still cloud-invisible; live brain merge stays exit-0.
(Taxonomy/lifecycle sprint queued BEHIND this — spec done: docs/taxonomy-lifecycle-design.md)

## Sprint N+1 — portable loreport-ops skill (2026-07-24)  [x] b46e054
- skills/loreport-ops: ONE prose skill, three homes (brain source of truth; ~/.claude/skills/loreport
  = /loreport; ~/.openclaw/workspace/skills/loreport-ops). Access-tier detection connector>fs>paste.
- assets/loreport-status.sh (tested on live brain: 46 items, 8 shared/38 local, 3 skills)
- sync.sh: propagation step (framework prompts -> brain; brain skill -> bridges) + frontmatter
  name rewrite so bridge dir == skill name. Fixes the framework->brain DRIFT class of bug.
- docs/onboarding-runbook.md: per-platform order openclaw -> Claude Code -> ChatGPT -> Gemini
- /loreport verified registered in Claude Code. KNOWN: loreport MCP tools need a FRESH session.
NEXT: user runs the onboarding round per the runbook.

## Phase F — build the personal brain (loreport-<owner>) using the feature  [x] LIVE
- Built `~/projects/loreport-<owner>`: 46 real memories (8 shared / 38 local), PROFILE, tiered. Enforcement
  verified on real data (cloud refused project-portia/project-coach-agent; shared readable).
- Backed up to PRIVATE `github.com/clawdia-codes/loreport-<owner>` (verified private before push, all branches).
- ChatGPT tunnel repointed --brain-dir → loreport-<owner> (restarted, live). Claude Code MCP repointed
  (user-scope `loreport`, claude-local trust → can read local items).
- Daily backup: systemd --user `loreport-brain-sync.timer` (merge+publish+push), enabled, next 00:00.
- TODO(E): scrub false-positives on LOCAL items abort merges (reword workaround used on project-clawdia) — scope the
  egress scrub to SHARED items only; + flock on change_memory_settings write.
- new brain seeded from `~/projects/<brain>-manifest.md` (shareable→shared, sensitive/operational→local)
- wire ChatGPT + Claude connectors (trust tiers) + private GitHub backup (+ B2 redundancy)
- GATE: cloud provider can read shared memories, cannot read local ones; backup pushes.

## PARKED — 4 follow-ups, deliberately deferred (2026-07-25)
Deferred by the user in favour of one more sprint (being planned now). Do NOT start these
without saying so first.

1. **82 broken wikilinks** in the live brain — underscore/hyphen leftover from the
   auto-memory migration (`[[feedback_model_routing_coach]]` -> `feedback-model-routing-coach.md`).
   Mechanical: rewrite only where the hyphenated target exists; leave the 13 genuine
   forward references alone. Edits body text of the user's memories, so ask first.
   Detect with: `DOCTOR_VERBOSE=1 ./doctor.sh` in the brain.
2. **Onboarding round** — Claude Code needs a FRESH session (this one predates the MCP
   registration), then `/loreport reconcile`. ChatGPT needs the docs/setup.md §3a standing
   instruction pasted into Project instructions, then reconcile + verify the local wall.
   openclaw is already wired (AGENTS.md) but has not run its first reconcile.
3. **Namespace refactor** (agreed as the "next" half of the ownership hardening):
   `memories/<provider>/<slug>.md` + `memories/shared/<slug>.md` (local-agent-only), so the
   write gate becomes a one-line path-prefix check instead of a provenance lookup. Needs a
   migration of the 46 items + INDEX rebuild + doc updates.
4. **Taxonomy/lifecycle sprint** — specced but unbuilt: `docs/taxonomy-lifecycle-design.md`
   (`type: decision`, `lifespan`, `expires`, cold shelf). Landmine recorded there:
   `ITEM_TYPES` in inbox_ingest.py AND brain_merge.py must gain `decision` or it is
   silently quarantined.

Unrelated env note: ANTHROPIC_API_KEY (or another auth source) is set and overrides the
claude.ai login — disables org connectors, and affects whether unattended budget-sprint
resumes draw on the subscription or bill the metered API.

## SPRINT: v1.3 — versioning, change summaries, HTML report (planned 2026-07-25)
Planned with the user in dialogue. The user confirmed gate 2 (stale-packet detection)
specifically. The A/B split is the USER'S DECISION (2026-07-25): two sprints, with a
checkpoint between. A ships first because its status call is what tells you whether B's
report is showing current data — build B first and there is no independent way to know
if what it displays is stale.

### Phase A — versioning + the two MCP calls
1. VERSION (semver) + curated CHANGELOG.md. One real entry per sprint, NOT generated
   from git log. check-docs.sh gate: fail if hub/ or prompts/ changed since the last
   version bump.
2. MCP status call — tooling version + git SHA + brain fingerprint + PACKET FRESHNESS
   + providers configured.
   GATE (non-gameable): with the packet deliberately stale, the call must report STALE.
   A call that only reads VERSION and reports success would have stayed green through
   every bug fixed on 2026-07-25 — that is the failure mode being designed against.
3. MCP "what changed" call — narrative, not a diff dump. Two halves: software (from
   CHANGELOG.md) + per-provider brain activity (from git history). Must surface a dead
   connector ("chatgpt 0 entries · 11d ago") — a broken provider looks identical to a
   quiet one, and the status call cannot distinguish them.

### Phase B — the HTML report
4. ONE self-contained file, no external requests. Phone-first, responsive.
   Dashboard-first layout: status → providers table → search → entries newest-first.
   Full memory bodies, client-side search (titles + bodies), privacy badge per entry,
   [[wikilinks]] as working anchors. Providers table: name, trust, configured when,
   last contribution, count.
   Generated daily by sync.sh. Static file, gitignored (derived/regenerable).
   Served over tailnet via `tailscale serve`. Port must NOT collide with Coach
   (:8000 + tailnet root) or Portia (:8011 -> :8443). VERIFY before binding.

### Trust boundary (applies to both phases)
- Report URL: given to cloud callers too. Security rests on tailscale auth, NEVER on
  URL secrecy. **CONSTRAINT: the report must never move to public serving without
  revisiting this** — at that moment, handing ChatGPT the URL becomes handing it the
  38 private entries.
- Counts + per-provider activity: IDENTICAL for cloud and local. Discrepant numbers
  across assistants would make all of them untrustworthy (user's explicit reasoning).
- Local entry TITLES: shown to local, redacted for cloud ("2 private"). Titles are
  content; the 2026-07-25 security review found that leaking local names to a cloud
  caller was the recon step that made the write/delete exploit aimable.
  GATE: a cloud credential gets the URL, the same counts, and zero private titles.

### Vocabulary (decided in dialogue)
Never print "N items" — in one conversation it meant 46, 48 and 49. Name the things:
memories · skills · knowledge pages. Collective = "entries". Human-facing text says
"private" / "stays on this machine"; `visibility: local` is UNCHANGED in frontmatter
and code, so no files or parsers need to change.

### Out of scope
The 4 parked follow-ups above. Do not start them.

## SPRINT C — filed as a GitHub issue (2026-07-26)
The 4 parked follow-ups + the INDEX-churn fix are now written up as a pick-up-ready issue:
  https://github.com/clawdia-codes/loreport-<owner>/issues/1
Filed on the PRIVATE brain repo, not the public framework one, because several items name
real entries and by this project's own rule a title is content.
