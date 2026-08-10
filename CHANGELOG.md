# Changelog

## [1.14.0] — 2026-08-10

### Changed
- **The health check emitted one failure state for three different conditions, and it caused
  a real false diagnosis.** `Loreport health FAIL: merge digest needs review` was the single
  line produced by a secret-scrub abort, a quarantined block, and a PROFILE conflict. On
  2026-08-10 two separate readers took it to mean the merge had failed and reported the brain
  as stuck for three days. It had merged and published every night. The states are separate
  now: **BROKEN** (exit 1) is a merge that did not complete, **STALE** (exit 1) is no
  completed merge inside the freshness budget, and **NEEDS REVIEW** (exit 0) is items waiting
  on a human with the pipeline otherwise healthy. Each message names its subsystem and what
  actually happened. `brain_merge.py` gained the matching distinction — exit **1** for the
  fail-closed aborts, exit **3** for needs-review — and `bin/loreport-sync` publishes and
  pushes on 3 without paging anyone.
- **Notifications fire on state CHANGE, not on every run**, and a recovery is announced so a
  reader who got a failure notice learns when it cleared. A first run on a new brain does not
  claim a recovery it never had.

### Added
- **Nothing asserted that the merge had actually run.** Check #1 looks at the age of `main`'s
  last commit, which only moves when content changes, and the digest check graded the newest
  `hub/digest-*.md` without ever asking how old it was — so a merge that died a month ago kept
  being graded on its last good day, green. A completed merge (a quiet no-op night included)
  now stamps `hub/merge-state.json`; the abort paths deliberately do not. `loreport-health`
  fails when that stamp exceeds `LOREPORT_MERGE_MAX_AGE_HOURS` (default 36), with distinct
  messages for "never merged here" and "merged N hours ago".

### Fixed
- **The three digest conditions were an `elif` chain**, so only the first ever surfaced — and
  the first, quarantine, is the one that never clears without a human. They are independent
  now.
- **`count_quarantine_items` counted `.gitkeep`.** The brain's `.gitignore` keeps that marker
  deliberately, so any brain having it reported one pending item forever — a count that can
  never reach zero, and therefore a check nobody reads.
- **The ChatGPT paste reminder sat behind a 30-day nag throttle that also gated the state.**
  Week 0 reported review; week 1 the throttle suppressed the item, the state fell back to
  healthy and the banner was deleted — which, once recovery announcements existed, would have
  reported "all checks green" while the surface was still unpasted, on a 30-day loop. The
  state-change gate is the only throttle now.
- **`index_item_count` returned the string `"0\n0"` on an INDEX with no items.** `grep -c`
  prints `0` *and* exits 1, so the `|| echo 0` fallback appended a second zero; the capture
  check then died with a `ValueError` that the script (no `set -e`) swallowed, leaving a
  crashed check reporting healthy.

## [1.13.2] — 2026-08-07

### Fixed
- **`make-surface.sh` had the same archive seam as the packet, and it was missed.** 1.11.0
  closed the seam for `hub/published/packet.md` but `surface.md` is a *second* delivery
  path — the one for paste hosts — and it composed `INDEX.md` only. A paste host cannot
  fetch a cold shelf any more than a cloud provider can, so the first time an item expired,
  every archived **shared** item would have vanished from every paste surface while
  `doctor.sh`'s new seam check stayed green (it only looked at the packet). `surface.md`
  now carries `INDEX-ARCHIVE.md` too, through the same local-item filter, and the doctor
  check covers both surfaces. Verified by running: archived shared item present, archived
  local item absent, and removing the line from `surface.md` makes doctor fail.
  Worth noting how this was found — the general claim "the archive seam is closed" was
  written down before both delivery paths had been checked. `sync.sh` copies **both**
  `doctor.sh` and `make-surface.sh` from the framework working tree, so like 1.13.1 this
  would have shipped to a live brain with no merge involved.

## [1.13.1] — 2026-08-07

### Fixed
- **`doctor.sh` reported every item as "invisible to search" on a brain with no archive.**
  1.11.0 taught the INDEX cross-check to consider `INDEX-ARCHIVE.md` by piping
  `cat INDEX.md INDEX-ARCHIVE.md | grep -q`. This script runs under `set -o pipefail`, so
  on a brain that has never archived anything `cat` fails on the missing file and the whole
  pipeline reports failure **even when grep matched** — 77 false failures on a 76-item
  brain. It reads correctly; only running it against a real brain exposed it. This matters
  more than an ordinary bug: `sync.sh` copies `doctor.sh` from the framework working tree,
  so it reaches the live brain on the next nightly sync **without any merge**. Now the
  index list is built first and only existing files are passed to `grep`.
- A no-op run no longer prints `Backup tag: none (--dry-run)` when it was not a dry run.

### Added
- A test for the seam where the archive (1.11.0) and the no-op guard (1.13.0) meet: an item
  expiring overnight changes what a rebuild produces while **no branch has moved**, so a
  guard that only asked "is there anything to merge?" would skip the archive transition and
  keep skipping it. Neither feature's own tests crossed that boundary.

## [1.13.0] — 2026-08-07

### Changed
- **A quiet day now produces no commits and no tag.** The merge runs daily from a timer,
  and on most days nobody captured anything — yet every one of those days still wrote two
  commits ("drop INDEX.md", "rebuild INDEX.md") plus a backup tag for byte-identical
  content. The cost was never untidiness: it buried the days something real happened, and
  made `git log INDEX.md` useless for answering "when did the catalog actually change?".
  A run is treated as a no-op only when **both** hold — every provider branch is already an
  ancestor of `main`, **and** the committed indexes already equal what a rebuild would
  produce. The second condition is load-bearing: an interrupted earlier run can leave the
  index stale while no branch has moved, and skipping the rebuild then would leave it wrong
  indefinitely. The report says so explicitly rather than printing a silent nothing, since
  a quiet success and a broken run should not look alike.
- The index drop is likewise skipped when there is nothing to merge — it exists to keep the
  indexes out of merge resolution, and with no merge it only spent a delete commit and a
  restore commit to arrive back at the same bytes.
- `do_merge()` returns its report (previously `None`), which is what lets the guard be
  tested against real repositories rather than asserted about.

## [1.12.0] — 2026-08-07

### Added
- **`hub/domain_backfill.py` — propose / review / apply for the `domain` field.** Two
  commands on purpose. `domain` says which side of a person's life an item belongs to;
  that is a judgement about *them*, not a property of the text, so a machine reading
  "deployed the staging cluster" cannot know whether it was their job or their weekend.
  `propose` reads only and writes a plain-text mapping with its reasoning; a human edits
  it; `apply` writes exactly what the file says. Measured on a real 76-item brain, the
  heuristic could only guess 25 and marked **51 as `?`** — which is the honest result and
  the reason the tool is built to hand those back rather than default them.
  Safety properties, each covered by a test: `?` lines are skipped rather than defaulted;
  a value outside the enum is refused; a line whose item has changed since the proposal is
  refused, so a stale review can never be replayed over newer content; `visibility` and
  every other byte of the item are preserved, because the two axes are independent and
  neither may be inferred from the other.

## [1.11.0] — 2026-08-07

### Added
- **Lifecycle: `lifespan` + `expires`, and a real `INDEX-ARCHIVE.md`.** The hot/cold index
  split has been a deferred design note since v1; it now ships. An item whose `expires`
  date has passed leaves `INDEX.md` and appears in `INDEX-ARCHIVE.md`, rebuilt
  deterministically alongside it. **Only the catalog line moves** — the file stays on disk,
  still readable and still wikilink-resolvable, so archiving can never be a quiet deletion.
  The trigger is a date comparison and nothing else: no duration math, no model judgement
  of staleness, and no "untouched since" notion (which would need access tracking the brain
  deliberately doesn't keep). An item with no `expires` is therefore untouchable by
  construction — which is what makes `permanent` and `active` safe, rather than a second
  rule that could drift out of sync with the first.
- **The archive/cloud seam.** The published packet now carries `INDEX.md` **and**
  `INDEX-ARCHIVE.md`, both through the same visibility filter. Without this, archiving a
  `shared` item would silently revoke every cloud assistant's access to it — providers get
  the packet, not the repo, and cannot fetch a cold shelf — while every health check stayed
  green. `doctor.sh` now asserts every archived shared item is present in the packet, and
  that assertion was proven non-vacuous by removing the archive from the packet build and
  watching it fail. The only thing ever excluded from the packet remains `visibility: local`.
- **`domain: work | personal | both`** — the work-vs-private axis, optional. This is
  explicitly **not** cloud exposure: that is `visibility`, and the two are independent (a
  work item may be local, a personal item may be shared). Nothing infers one from the other.
- `consolidate.md` gains a Lifecycle operation and an **Archived** output section; capture
  guidance for all three fields is in `bootstrap.md`. `doctor.sh` treats `INDEX-ARCHIVE.md`
  as catalogued — otherwise every archived item would be reported "invisible to search" the
  day it expired — and flags an item listed in both indexes.

### Changed
- `INDEX-ARCHIVE.md` is a derived artifact like `INDEX.md`: excluded from every merge and
  regenerated wholesale, since a hand-merged copy could only ever be wrong.

## [1.10.1] — 2026-08-07

### Fixed
- **`observe_extract` refuses to run while another process writes the same output.**
  Resumability alone was not enough: `already_done()` is read once at startup, so two
  concurrent runs both see the same completed set, both process the same remainder, and
  both append. Observed live — a backgrounded run was believed dead (its wrapper had
  exited) and a second was started; the artifact came out with **238 rows for 140
  conversations, 98 duplicated**. That is not cosmetic: Pass 2 judges whether a preference
  is stable by counting *how many distinct conversations* a claim appears in, so a
  double-counted conversation silently inflates its evidence. Now guarded by an exclusive
  `flock` on a sidecar file, released however the process dies. Verified by starting a
  second run against a held lock and confirming it exits with a clear message.

## [1.10.0] — 2026-08-07

### Added
- **`hub/observe_extract.py` — knowledge-grab Pass 1.** Maps a cheap LOCAL model over Pass 0
  records and proposes observations (`fact` / `trait-signal` / `meta-statement`), each
  anchored to a quote. Writes nothing to the brain; the output is a private working
  artifact for later aggregation and human review. Runs on ollama by default — a
  mechanical map over hundreds of conversations should not spend a metered budget — with
  `think` disabled, since reasoning defaults have blown idle watchdogs on this host.
  Resumable: output is appended per conversation and completed ids are skipped.
- **The quote gate.** Every observation must carry `verbatim_quote`, and the module
  *mechanically verifies the quote occurs in the source turns* (whitespace-normalized).
  Unverifiable observations are dropped and counted. This converts "a model asserted this
  about the user" into "the user demonstrably said this" — cheap, deterministic, and not
  arguable-past by a fluent model, which is the Barnum failure the design names. It fired
  on real output immediately: 1 of the first 5 conversations produced a fabricated quote.
- 16 tests, the core ones negative: an invented-but-plausible quote, a near-miss
  paraphrase of a real statement, and an over-short quote must all be rejected. Confirmed
  to fail with the gate removed.

### Changed
- Pass 1's prompt explicitly rejects **instructions the user gave to a machine**. This user
  operates AI agents, so their messages are full of task briefs and output-format specs
  that read exactly like personal preferences. A first live sample returned four
  observations, all of them agent task-specs ("write ONLY under .maestro/docs/preparation/")
  rather than facts about the person. The discriminator now stated in the prompt: does this
  describe THE PERSON, or HOW ONE TASK SHOULD BE DONE?

## [1.9.0] — 2026-08-07

### Added
- **`hub/corpus_prep.py` — knowledge-grab Pass 0.** Normalizes Claude Code / Codex /
  OpenClaw session logs into per-conversation records containing *only* genuine user
  turns, for the knowledge-grab pipeline. Deliberately high-recall and judgment-free:
  deciding what is worth remembering belongs to later passes. Reuses `sweep_extract`'s
  parsing rather than forking it. Writes nothing to the brain, touches no git, calls no
  model.
- **Structural template detection.** Marker lists rot — they only catch injections someone
  already noticed. A live-corpus sample showed marker filtering still leaving the artifact
  dominated by `/security-review` payloads and Codex review-continuation templates that
  carried no marker at all. So a *long* turn whose opening recurs across ≥3 distinct
  conversations is treated as injected, whatever words it uses: a human does not type the
  same 2,000-character message in fifty sessions. Length-gated so short human repeats
  ("try now", "continue") survive. Needs no per-payload maintenance and catches shapes that
  do not exist yet.
- **Whole-turn injection scan.** `sweep_extract.is_synthetic_turn` inspects only the first
  400 chars, which is correct there (candidates are capped at 1200). Pass 0 keeps long
  turns, so it scans the entire turn — two `/security-review` payloads (14,996 and 42,399
  chars) had leaked with markers at char 2378 and 4024. Plus a 20,000-char ceiling: a human
  does not type 42,000 characters.
- 12 tests, including two negative ones encoding production failures — the `[System]`
  gateway notice that became a memory *about* the user, and pasted credentials. Both were
  confirmed to fail with their fix removed.

### Measured
- Full local corpus → **140 conversations, 1,138 user turns**, 2026-05-02 → 2026-08-07.
  Median turn 81 chars (was 667 before template detection — the drop is injected payloads
  leaving). Zero `[system]`, `system-reminder`, `<command-name>` or credential-shaped
  strings survive into the artifact.

## [1.8.3] — 2026-08-07

### Fixed
- **`doctor.sh`'s published-packet privacy check was vacuously true on an empty packet.**
  Its leak loop iterates the packet's entries, so an empty packet ran the body zero times,
  left `pleak=0`, and reported "published packet contains no local item" — success. The
  instrument was blind to the exact failure it exists to catch: anything that stops the
  packet being populated silently stops every shared memory reaching every cloud provider,
  with a green health check. Now paired with a positive assertion — the packet must carry
  items whenever shared items exist on disk, and both counts are reported so drift is
  visible. Negative-tested against a clone: emptying the packet flips the run to FAIL while
  the old check still reported success beside it.

## [1.8.2] — 2026-08-07

### Fixed
- **Sweep no longer attributes system messages to the user.** OpenClaw injects gateway
  notices as `role=user` prompts prefixed `[System] `. A full-history dry run on 2026-08-06
  promoted `"[System] Your previous turn was interrupted by a gateway restart..."` into a
  candidate memory *about the user* — a system message wearing the user's identity, the
  attribution-error failure mode named in the knowledge-grab design. `SYNTHETIC_MARKERS`
  now carries the generic `[system]` prefix, so the whole class is filtered rather than the
  one sentence that leaked. Two regression tests cover it, including a positive control
  proving the filter keys on the marker and not on length or keywords; both were confirmed
  to fail with the fix removed. Verified against the live corpus: the candidate is gone.

## [1.8.1] — 2026-08-06

### Fixed
- `brain_merge` imports `synth_detect` path-safely (`a446671`). `check-docs.sh` imports the
  module without `hub/` on `sys.path`, so the top-level import broke the gate. The fix
  shipped on 2026-08-05 but landed **unrecorded** — no VERSION bump, no entry — which left
  `check-docs.sh` itself failing on its own "changed hub/ since VERSION was bumped" rule.
  Recorded here at the start of Sprint C v2 so the baseline gate passes.

## [1.8.0] — 2026-08-05

### Added
- Synthesis detection runs inside the nightly merge, **report-only** for the
  design-wiki-parity §2 calibration window (~3 weeks, to 2026-08-25). Proposals go into
  the digest and `hub/synthesis-report.json`; nothing is filed, and no code path can
  create a `knowledge/` page from detector output. A detector crash degrades to a digest
  note rather than failing the merge.
- Health check gains 6b: alarms when the detector emits degenerate topics (the REM
  failure design §2 names) or oversized clusters — so the calibration warnings have a
  consumer instead of sitting in a file nobody opens.
- `hub/synthesis-report.json` gitignored in the brain template, alongside the other hub
  report artifacts.

## [1.7.0] — 2026-08-05

### Added
- `hub/sweep_run.py` — the nightly runner that turns sweep candidates into provider-branch
  commits. It owns no scrubbing, schema or git logic of its own: every candidate goes
  through `inbox_ingest`'s existing chain (parse -> schema -> secret scan -> imperative scan ->
  locked `commit_block`), so a swept capture is gated exactly like an assistant-authored one
  and a rejected one lands in `hub/quarantine/` for the merge digest.
- `units/loreport-sweep.{service,timer}` — 23:30 nightly, half an hour ahead of the brain
  sync, so a night's captures merge to `main` in the same run instead of waiting a day.
- Idempotence has two independent layers: a fingerprint ledger at
  `~/.local/state/loreport/sweep-state.json` (deliberately outside the brain repo — `sync.sh`
  halts the nightly pipeline on a dirty tree), and `commit_block`'s own "skipped: no change".
  Verified against a clone: run 1 committed 4, run 2 offered 0, run 3 with the ledger deleted
  committed 0 and reported "skipped: no change" for all 4.
- ChatGPT is never swept — it keeps no local session log, and inventing one would be
  inventing provenance.

## [1.6.0] — 2026-08-05

### Added
- `hub/sweep_extract.py` — deterministic capture sweep over Claude Code, Codex and OpenClaw
  session logs. Emits emit-grammar blocks to stdout with a content fingerprint per candidate;
  no git operations, no writes. Wired nowhere yet.
- `hub/synth_detect.py` — synthesis cluster detector per design §2: ≥3 memories that mutually
  link, or link a common missing `[[name]]`. Report-only, wired nowhere; degenerate topics and
  oversized components are reported as detector-health warnings rather than proposals.
- `hub/wikilink_fix.py` — rewrites `[[a_b]]` → `[[a-b]]` where the hyphen target uniquely
  resolves, skipping human-authored regions. Dry-run by default.

### Fixed (S3 review, before any of the above was wired)
- Sweep precision: harness-injected `role=user` turns — skill bodies, slash-command payloads,
  cron prompts, hook and task notifications, agent dispatch briefs — were captured as user
  statements. A 3-day live run yielded 15 candidates, none of them typed by the user. Now
  filtered structurally and by marker, with a length cap.
- Sweep triggers: an explicit save must name what to keep (`remember this`, `save the
  following`, `note:`); the bare verb matched "save any outstanding work". A correction must
  quote a span in double quotes — an apostrophe in "that's wrong" no longer counts as a quote.
- Sweep slugs: candidates opening with the same words produced the same `memories/<slug>.md`
  path and silently overwrote each other. Slugs now carry a fingerprint suffix — unique per
  candidate, stable for identical content.
- Detector clustering: connected components over one-way links collapsed the brain into a
  single 17-member blob topic'd by an arbitrary member. Edges now require mutual linking, as
  design §2 specifies.

## [1.5.2] — 2026-08-04
- loreport-health: alarm on silent index truncation (dropped_budget > 0 in projection manifest) — closes the no-silent-decay gap found in S2.

Curated by hand, one entry per sprint — deliberately not generated from git log,
because a list of commit subjects tells you what was touched, not what changed.
Newest first. Versions follow [semver](https://semver.org).

## [Unreleased]

## [1.5.1] — 2026-08-04

### Added
- The projected block now opens with a **subagent guard**: a line telling a subagent or
  automated worker to treat the profile as context only and not to save memories or act on
  preferences. The projection is injected globally, so it reaches delegated workers too — and
  a capture from one is un-reviewed, while a reviewer that has absorbed the user's stated
  preferences is no longer an independent check. Instruction-level scoping only; structural
  scoping is S3.

## [1.5.0] — 2026-08-04

### Added
- **Managed human-edit regions** (`design-wiki-parity.md` §1). A `<!-- human:start -->…
  <!-- human:end -->` region marks text as the human's. Two layers enforce it: the capture
  protocol tells a model an `action="update"` must carry every existing region through
  verbatim, and — the part prose alone never delivered — `hub/brain_merge.py` now *checks*.
  A provider branch that drops or alters a region present on `main` has that file reverted
  to main's copy, the rejected version parked in `hub/quarantine/`, and the reason written
  to the digest the health check reads. Region bodies compare as a multiset, so reordering
  verbatim regions is fine; a file with no regions is untouched; a new file passes through.
  Scope is `memories/`, `knowledge/`, and `PROFILE.md` — the profile is projected into
  every provider surface, so it is the highest-value file in the brain to protect.
- **`type: person` and `type: decision`** (§4) accepted end to end: `ITEM_TYPES` in
  `brain_merge.py` and `inbox_ingest.py`, the emit grammars in `prompts/`, `doctor.sh`'s
  type check, and `project.py`'s truncation ranking (both rank with `user`/`feedback` —
  entities and rulings are load-bearing, so they survive a budget squeeze). `person`
  defaults to `visibility: local` by prompt rule.
- **Obsidian render** (§3). The brain was already a valid vault, so this is config, not a
  bridge: a minimal tracked `.obsidian/` (readable line length, wikilinks on, graph
  defaults) with volatile workspace state gitignored. `doctor.sh` gained a link-resolution
  pass that reports *ambiguous* `[[wikilinks]]` — a slug matching more than one of
  `memories/<name>.md`, `knowledge/<name>.md`, `skills/<name>/SKILL.md` — alongside the
  dangling-link summary it already printed.

### Fixed
- Projection stripped no `human:` markers, so the PROFILE region delimiters would have been
  copied into every provider surface. `project.py` now removes the marker lines — never the
  text between them — on the way out.
- A provider branch that *deleted* a guarded file raised `FileNotFoundError` mid-merge.
  Since `sync.sh` halts the whole nightly run on an unclean tree, that would have taken
  projection and the backup push down with it. A deletion is now treated as an empty body:
  every region reads as dropped, and the guard restores main's copy.

## [1.4.0] — 2026-07-26

### Added
- **Codex** is now a supported provider (`provider/codex`, `trust: local` — it's a local
  CLI host). Added to the fixed merge order and the capture gate; connector setup is in
  `hub/config/connector-snippets.md`.
- ADR-004 records the retrieval **graduation ladder** — the concrete, measurable trigger
  and the rung-by-rung path (derived link-graph → SQLite FTS5 → embeddings) for if/when the
  brain outgrows flat-index search. Documentation only; no retrieval code changed.

## [1.3.3] — 2026-07-26

### Fixed
- The tracked report churned on every sync. Stamping it with `main`'s last commit created
  a feedback loop: each sync commits a rebuilt index and packet, which moves `main`, which
  rewrites the stamp, which commits the report again — one 800KB commit per sync with
  nothing changed. Both the timestamp and the identifier now come from the last commit
  that touched your actual entries, so publishing activity no longer moves them.

## [1.3.2] — 2026-07-26

### Changed
- The HTML report is now **tracked in your brain repo** rather than treated as a throwaway
  artifact — it is part of the brain, so it travels and restores with it.
- To make that affordable, the report is stamped with your brain's **last commit time**
  instead of the build time. Same brain in, byte-identical file out, so it is committed
  when your memories change and not once a day for nothing.
- Where the report is reachable from outside your machine now defaults to
  `NEEDS_TO_BE_SETUP`, and `loreport_status` reports that verbatim rather than hiding the
  line. Hosting is genuinely optional — but not-set-up should be visible, not silent. The
  tool description tells assistants to offer help finishing it.
- Onboarding asks whether to publish the report, and makes clear that "later" is fine.

## [1.3.1] — 2026-07-26

### Fixed
- `report_url` is now read from the `LOREPORT_REPORT_URL` environment variable rather
  than committed to `hub/config/providers.json`. That file lives in the public framework
  repo, so a real deployment URL there published personal infrastructure and shipped one
  user's address as framework config for everyone.

## [1.3.0] — 2026-07-25

### Added
- `loreport_status` — one call reporting the tooling version, the brain's fingerprint,
  **whether the packet each provider reads is current with `main`**, and which providers
  are configured. The freshness check is the point: a version string alone stays green
  while a provider quietly reads three-day-old memories.
- `loreport_whats_changed` — what happened lately, in both halves: software changes from
  this file, and per-provider brain activity from git history. Surfaces a *silent*
  provider, which otherwise looks identical to a quiet one.
- `scripts/init-brain.sh` — creates a new brain: folder, git repo, and an optional
  private GitHub backup whose privacy is read back from the API before anything is
  uploaded. Refuses to create a brain inside an existing git repo.
- `doctor.sh` — post-setup self-test, shipped into every brain. Structure, git, index
  integrity, and the privacy wall; `--providers` probes each credential live and asserts
  cloud callers are refused private entries.
- `scripts/check-docs.sh` — documentation and duplication gate: spec-slice byte-identity,
  the template's protocol copy, skill name agreement, broken links, and a version-bump
  check.

### Fixed
- **A cloud credential could overwrite or delete any entry by path, including private
  ones it cannot read.** Ownership is now enforced at capture *and* re-checked at the
  merge, where a missing trust marker counts as untrusted — so a commit pushed straight
  to a provider branch is caught too.
- **The visibility parser failed open.** Six realistic hand-edits (`"local"`, `Local`,
  a trailing comment) silently became shared. Anything not parseable as an explicit
  `shared` is now treated as private.
- The pre-merge recovery tag was force-moved on every run, so the tag the error messages
  told you to recover at no longer pointed at the pre-run state. Tags are now per-run,
  and are actually pushed to the backup — they never were before.
- `snapshot_publish` read the working tree instead of `main`, so it could publish an
  unmerged provider branch labelled as canonical.
- Provider credentials no longer appear in process arguments, where any local user could
  read them with `ps`.

### Changed
- Documentation restructured: three overlapping guides collapsed into one walkthrough
  (`docs/setup.md`). Getting started is now two file-opens.
- Human-facing text no longer says "N items" — it names memories, skills, and knowledge
  pages, and says "private" rather than "local". The `visibility: local` field is
  unchanged.

[Unreleased]: https://github.com/clawdia-codes/loreport/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/clawdia-codes/loreport/releases/tag/v1.3.0
