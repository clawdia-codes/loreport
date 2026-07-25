# Changelog

Curated by hand, one entry per sprint — deliberately not generated from git log,
because a list of commit subjects tells you what was touched, not what changed.
Newest first. Versions follow [semver](https://semver.org).

## [Unreleased]

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
