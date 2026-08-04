# Changelog

## 1.5.2 — 2026-08-04
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
