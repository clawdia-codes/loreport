# Changelog

## [1.14.0] — 2026-08-10

### Fixed — the alerting path could not be trusted to alert

- **A misconfigured health run was recorded by systemd as a success.** The unit sets
  `SuccessExitStatus=1` so an unhealthy brain is not a broken unit; the startup guards
  used `${VAR:?}`, which exits **1**. A weekly timer therefore stayed green while the
  check had examined nothing. Guards now `exit 2`. The unit comment claiming these kept
  codes "2, and 127" was measured wrong and corrected.
- **The one alert that cannot self-clear re-paged on every run.** The state-change gate
  hashes the message set, and `last completed merge 88h ago` increments hourly.
  Measured 3 notifications over 3 runs vs 0 at a fixed age. Only the `<N>h ago` shape is
  normalised, and only inside the signature — a count moving 1 → 5 still alerts.

### Added — the leak audit runs on the timer, instead of only when asked

- **`scripts/loreport-audit` had no caller.** No unit, no timer, not from
  `loreport-health`, not from `bin/loreport-sync` — the one thing asserting that
  unclassified items stay unpublished ran only when a human typed it, which is the
  same producer-with-no-consumer shape that produced the leaks it exists to catch.
  `loreport-health` now runs it, because a second timer is a second thing to forget.
  Severity is **mapped, not flattened**: `LEAK`/`BLIND` fail, `RISK`/`META` go to
  NEEDS REVIEW, and an audit that *cannot run* fails rather than passing quietly.

### Fixed — skills read as leaks after the visibility flip

- **Nine false leaks on the live brain.** A skill carries no `visibility:` field at
  all (§1); once an absent key meant `local`, every skill in every published surface
  read as a leak. `check-docs.sh` §2 stayed green because it compares the *parsers*,
  and the carve-out has never lived in a parser. `hub/brain_audit.py` now applies the
  same **defeasible** carve-out as `snapshot_publish._item_visibility`, at the
  resolver — an explicit `visibility:` on a SKILL.md still wins.

### Fixed — the audit checker agreed with the old default (merge-introduced)

- **`hub/brain_audit.effective_visibility` returned `shared` for an absent `visibility:`**
  while every publisher and `docs/format-spec.md` §1 had moved to `local`. Its own docstring
  claimed FAIL CLOSED and did the opposite. This existed on **no individual branch** — green
  on `main` and on each of the four source branches, red only once the parser flip and the new
  checker landed together, which is why it took an independent measurement pass to find.
  Effect was fail-**open** on the exact assertion the checker exists to enforce: an unmarked
  item that had reached a cloud surface would not be flagged, while a correctly withheld one
  raised a false "shared item missing from the packet" finding. `scripts/check-docs.sh` §2 now
  pins the two parsers against each other.

### Changed — `visibility:` is now REQUIRED, and absent means `local`

- **The last fail-open default in a fail-closed engine is closed.** `docs/format-spec.md`
  §1 made `visibility:` optional and defaulted it to `shared` — cloud-published. The publish
  filter itself was never wrong; it drops items marked `local`. The defect was that nothing
  asserted an item had been classified *at all*, so an unmarked item was not merely
  unfiltered — it was positively **published**. Three batches leaked that way on 2026-08-07.
  An item is now `shared` only on an explicit `visibility: shared`; an absent field, an
  unparseable value, and a file with no frontmatter all read `local`.

- **Both halves shipped, because either alone is a half-measure.** The default protects;
  a publish gate reports. `hub/snapshot_publish.py` now enumerates `memories/`/`knowledge/`
  on `main` before building anything and refuses the whole republish while any item lacks
  the field, naming each one to stdout and to `hub/quarantine/digest.md` — the same channel
  and the same nonzero exit as the egress secret scrub, which `bin/loreport-sync` already
  turns into an alert. A safe default that is *also* silent would only convert a leak into a
  growing pile of items no provider can see.

- **Every site where the default was materialised, not just the parser.** The five
  `_visibility_from_text` copies had one rule; `hub/project.py`, `brain-template/make-surface.sh`
  and `examples/brain/make-surface.sh` had a *different* one — "include unless an exact
  `^visibility:\s*local\s*$` line appears". Those write `hub/surface-*.md`, the files whose
  entire purpose is to be pasted into a cloud assistant. The two rules disagreed on every
  input they could differ on: an unmarked item, `visibility: "local"` (quoted),
  `visibility: local  # temp` (trailing comment), and a file with no frontmatter — the packet
  withheld all of them while the surface published them, and `doctor.sh` used the *producer's*
  rule and reported green. Flipping the parser alone would have left that path wide open. All
  three implementations now answer identically; verified case-by-case across 14 inputs.

- **Skills are exempt in FOUR places, and missing one of them is the easy mistake.**
  `hub/report_build.py` walks `skills/` and badges each entry shared/private; it got the
  parser flip but no carve-out in the first pass, which would have turned every skill in the
  brain private and dropped it from the report's shareable count. Caught in review, before
  merge, by grepping the call sites rather than trusting that the parser change was local —
  the live-brain diff would NOT have caught it, because that diff compared parsers, not the
  resolver paths through them.

- **Skills are exempt, and that is not a new exception.** A skill is a package, not an item,
  and carries no `visibility:` field at all (format-spec.md §1) — `hub/brain_merge.py`'s
  secret scrub and `mcp_server`'s status counter have always said so. Under the old default
  that fell out for free; now it must be stated, and it is stated at the **resolvers** that
  know a path is a skill, never in the parser, which only ever sees text and must stay
  byte-identical across its copies. Without it, flipping the default would have silently
  deleted every skill from every packet and surface.

- **The secret scrub was NOT loosened along the way.** `scan_brain_for_secrets` demotes a
  secret hit from "abort the merge" to "a line in a digest" for `local` items. Routing that
  through the flipped parser would have quietly extended the demotion to every unclassified
  item — a directional loosening bought by a change meant to tighten. The demotion now
  requires an *explicit* `visibility: local` (`_has_explicit_visibility`, drift-checked
  against its second copy by `scripts/check-docs.sh` like the other duplicated primitives).

- **Producer and checker both moved, or the change would not stick.** The `emit-grammar v1`
  spec slice told assistants to "omit for shared (default)"; left alone, captures would keep
  omitting the field and everything would silently become `local`. Updated in all four synced
  copies, plus `prompts/bootstrap.md`, `prompts/onboard.md`, `README.md`,
  `docs/visibility-design.md`, the `brain-template/` READMEs and the example skills.

- **No existing item is reclassified.** Verified against the author's live 90-item brain by
  running old and new rules over every INDEX item on both delivery paths: 0 changes to the
  packet, 0 to the surface, 0 unclassified items found by the new gate. The five items in
  `examples/brain/` were unmarked and are now explicitly `visibility: shared` — a shipped
  example must demonstrate the required field, not the omission.
- **The skills carve-out is a DEFAULT, not an override.** Skills carry no `visibility:`
  field (format-spec.md §1), so the resolvers supply `shared` for them; written
  unconditionally, that also overruled a `visibility:` a human had written. The result was a
  privacy control that reported success and changed nothing:
  `loreport_change_memory_settings(name="secret-skill", visibility="local")` returned
  `{'status': 'changed', 'visibility': 'local'}` and really did commit the line to `main`,
  while the skill stayed in `hub/published/packet.md`, stayed in `hub/surface-*.md`, was
  still served in full at cloud trust, and was still reported back as `shared` by
  `loreport_view_memory_settings`. An explicit `visibility:` now wins at every resolver:
  `snapshot_publish._item_visibility`, `mcp_server._visibility_of`,
  `project.filter_index_make_surface`, `report_build.load_entries`, and `item_is_shared` in
  **both** copies of `make-surface.sh`. The shape is
  `skills/… and not _has_explicit_visibility(…)`, never a blanket removal — the three skills
  in the author's live brain carry no `visibility:` line, and a blanket removal would
  withhold every one of them. `hub/mcp_server.py` gained a byte-identical copy of
  `_has_explicit_visibility` and `scripts/check-docs.sh` §2c now drift-checks it too;
  `hub/project.py` got the disk-reading sibling it needs (it reads the worktree, not `main`).
- **`brain-template/make-surface.sh` is now covered by `scripts/check-docs.sh` §4.** It is
  the copy `scripts/init-brain.sh` installs into every real brain, and it was the only
  duplicated file in the repo with no checker of any kind — reverting its fail-closed rule
  left every gate green. §4 now runs the same fixture and the same assertions against both
  copies, including a new one: a SKILL a human marked `visibility: local` must not reach the
  default surface.

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

- `tests/test_visibility_failclosed.py` — 22 tests. Each names the single-line production
  mutation it reddens, and all 23 mutations (21 Python, 2 shell) were run: every one turned
  a test or a `check-docs.sh` assertion red. One of them pins the migration path this
  changelog promises — `loreport_change_memory_settings` must be able to INSERT a
  `visibility:` line on an item that has none, or a brain could be left permanently unable
  to publish by the very gate meant to prompt a fix. Running them found a real defect in the suite —
  the ownership test passed under the old default too, because `check_ownership` also denies
  on a foreign `source:`; it now pins `source:` so only visibility can decide.
- `scripts/check-docs.sh` §4 now asserts the *positive* as well: a `visibility: shared` item
  and a skill must still reach the surface. The previous assertions ("no local item leaked")
  were all satisfied by an empty surface — which a fail-closed filter makes a live failure
  mode rather than a theoretical one.
- **A leak check you can point at a brain — `scripts/loreport-audit --config <brain>/loreport.conf`.**
  On 2026-08-07 three batches of memories reached cloud-published surfaces and there was no
  runnable check that could have said so. Two separate reasons: `brain-template/doctor.sh`
  does `cd "$(dirname "$0")"`, so the brain it audits is always its own directory — it
  cannot be aimed, and the live brain no longer has a copy of it at all; and
  `scripts/loreport-health`, which *can* be aimed, carries no leak assertion whatsoever.
  The new script takes the same `--config` as `bin/loreport-sync` and `loreport-health`, is
  read-only, and asserts: every item carries an explicit `visibility:` and `domain:` (absent
  `visibility` means *shared*, so an unclassified item is cloud-published by omission); no
  `local` item appears in `hub/published/packet.md`, `hub/surface-chatgpt.md` or
  `hub/surface-claude-ai.md`; and the counts reconcile — on disk vs classified vs catalogued
  vs published, including that every shared item catalogued on `main` reaches the packet.
  Logic lives in `hub/brain_audit.py`; the script is the config plumbing. Item frontmatter
  and the INDEX are read from `main` (like `snapshot_publish.py`, because that is what the
  publisher used); the surfaces are read from disk, because two of the three are gitignored
  and exist nowhere else.
- **Blind spots are failures, not passes.** A safety assertion that iterates a collection is
  vacuously true when the collection is empty, and this repo shipped exactly that bug once
  (the published-packet privacy check passed on an EMPTY packet). So zero items, a missing
  surface, an unreadable surface, an empty surface and an empty INDEX each produce a
  `BLIND` finding and a nonzero exit — and the guards are **unconditional**. A guard gated
  on "…and shared items exist" is itself a path back to reporting green having examined
  nothing, which is why the test for it uses a brain whose items are *all* local.
- **The checker uses the fail-closed visibility rule, and `scripts/check-docs.sh` now pins
  it there.** The repo holds two rules: fail-closed parsing (5 copies) and a whole-line
  `^visibility:\s*local\s*$` match (`hub/project.py`, `make-surface.sh`, `doctor.sh`). They
  disagree on `visibility: "local"`, `visibility: local  # note` and a file with no
  frontmatter — the projector keeps such an item in the paste-into-cloud surfaces while
  `snapshot_publish` drops it from the packet, and `doctor.sh`, sharing the *producer's*
  rule, reports green on a real leak. The audit reports it as a leak *and* reports the
  non-canonical spelling before it becomes one; the check-docs parser-agreement gate now
  covers `brain_audit.effective_visibility` so it cannot drift.
- **Nothing asserted that the merge had actually run.** Check #1 looks at the age of `main`'s
  last commit, which only moves when content changes, and the digest check graded the newest
  `hub/digest-*.md` without ever asking how old it was — so a merge that died a month ago kept
  being graded on its last good day, green. A completed merge (a quiet no-op night included)
  now stamps `hub/merge-state.json`; the abort paths deliberately do not. `loreport-health`
  fails when that stamp exceeds `LOREPORT_MERGE_MAX_AGE_HOURS` (default 36), with distinct
  messages for "never merged here" and "merged N hours ago".

### Fixed
- **The audit went blind on any item whose filename carries a non-ASCII byte.** `git ls-tree`
  runs with `core.quotePath=true` by default, so such a path comes back C-quoted *and*
  double-quoted — `"memories/caf\314\201-note.md"` — no longer ends in `.md`, and was
  filtered out of `item_paths`. The item was then never classification-checked (no RISK for
  a missing `visibility:`, no META for a missing `domain:`) and, because it was also never
  counted, the unconditional vacuity guard stayed quiet as long as one ASCII item existed.
  Measured, one variable changed: a brain whose only unclassified item had a non-ASCII
  filename reported `items=1, findings=[], exit 0`; the byte-identical brain with an ASCII
  filename reported the RISK and exit 1. So the instrument certified clean on exactly the
  published-by-omission defect it was built to detect, silently. `_run_git` now passes
  `-c core.quotePath=false`, and `encoding="utf-8"` with it — `text=True` alone decodes with
  the locale encoding, and this runs from a systemd user unit that may carry no `LANG`.
  Item slugs are derived from names, so non-ASCII item names are ordinary input.
- **An unreadable or non-UTF-8 surface killed the audit instead of being reported BLIND.**
  `UnicodeDecodeError` is not an `OSError`, so the section-2 guard did not catch it, and
  section 3 re-opened `hub/published/packet.md` with no guard at all. Either path raised out
  of `audit()`: `format_report` never ran, the operator saw a traceback and no report, and
  Python's uncaught-exception status (1) is this script's *findings* code — a cannot-run
  condition delivered as "audited fine, found problems", the exact conflation the 0/1/2 exit
  design exists to prevent. Both reads now produce a `BLIND` finding.
- `scripts/loreport-audit` deliberately does **not** copy two idioms from its siblings.
  `--config` with no value: `shift 2 || true` shifts nothing when one argument remains and
  swallows the failure, so `$1` stays `--config` and the loop spins forever — verified,
  `timeout 5 bash scripts/loreport-health --config` exits 124, and under a timer a hung unit
  is worse than a failed one. And `${LOREPORT_BRAIN:?…}` aborts a *script* with exit 1 on
  this bash, which is the new script's "found problems" code — a wiring error must not be
  able to impersonate a finding, so the required keys are tested explicitly and exit 2.
  Both are pinned by tests. The same two issues remain in `scripts/loreport-health` and
  `bin/loreport-sync`; fixing them there touches units and exit-code contracts, so it is
  left as its own change.
- **A failed capture destroyed every uncommitted change in the brain, not just its own.**
  `inbox_ingest.commit_block`'s failure handler ran `git checkout -- .` plus a pathless
  `git reset` against the single shared working tree. Measured twice on 2026-08-07/08: a
  session's hand-edit was discarded at 23:06:25.069, 7ms before the quarantine file for an
  unrelated block was written at 23:06:25.076. The loop was self-reinforcing — a dirty tree
  is exactly what makes `git checkout provider/<host>` fail, so the handler wiped the tree
  and destroyed the very edit that had caused the failure.
  The cleanup is not removed; its purpose (REVIEW.md #13 — a half-staged capture must not
  poison the next capture or the nightly merge) is real. It is now scoped to `block["file"]`
  alone, via `_restore_capture_path`, and it only runs if the capture actually mutated
  something: on the checkout-refused path the correct amount of repair is **none**, because
  scoping alone would still have restored the dirty path and destroyed the same hand-edit.
  Two leftovers the old handler never cleaned are also fixed — a failed *create* left the
  new file untracked on disk (which `finally`'s `git checkout main` then carried onto main),
  and a failed *delete* left an unstaged deletion that halts the next nightly merge.
  If the scoped restore cannot complete, `CleanupError` names the path and the original
  failure and the capture fails loudly; there is deliberately no fallback to wiping the tree.
  Note the narrow case that decides where the "did this capture mutate anything" flag is set:
  a delete aimed at a path that exists on disk but is *untracked* (a session's hand-created
  file) makes `git rm` fail at pathspec-match having changed nothing, and the old handler left
  untracked files alone — so the flag is set **after** the `rm`, and **before** the `open()`
  on the create/update branch, which truncates on entry.
  Covered by `tests/test_ingest_cleanup.py` (13 tests, run against a real two-branch git repo
  with real failures — a `pre-commit` hook exiting 1, a genuinely dirty tree blocking the
  branch switch, and an untracked delete target).
- **Three of the five NEEDS REVIEW reasons reached the owner through no channel at all.**
  `brain_merge.needs_review_reasons()` has five members. The health check re-derived two of
  them by grepping the digest: `profile_conflicts` (the "PROFILE conflicts … none" line) and
  `human_region_violations` (they write files under `hub/quarantine/`, so the count line
  catches them). A renamed add/add twin, a secret-scrub warning on a `visibility: local`
  item, and a reverted provenance violation write no quarantine file and appear in no line
  it read. Moving the merge's nonzero exit off the alert path — correct, that exit had been
  read as "the merge failed" — removed their last route out. Measured on the branch as it
  stood: with a `sk-…` sitting in a local item, `brain_merge` exited 3 and the owner's phone
  received `🧠 brain OK — 2 memories`, with the banner deleted. An unremediated secret in
  the brain announced itself as healthy; the cross-provider tamper gate reverted silently
  and said nothing.
  `hub/merge-state.json` already carried a `needs_review` bool and nothing anywhere read it.
  It now also carries `needs_review_kinds` — the subsystem KEYS, never the payloads, because
  `scrub_warnings`' payload is the matched secret fragment and `merge-state.json` is
  untracked-but-not-gitignored in brains created before this version — and
  `scripts/loreport-health` §6c is the consumer. It iterates whatever kinds are present
  rather than testing a fixed list, so a sixth reason added to `brain_merge` is reported the
  day it lands; it skips, by name, the kinds §6b already stated in the digest's own words, so
  the common conditions are not double-reported and a kind §6b has never heard of can never
  fall into the gap between them. A `merge-state.json` from an older engine (bool, no list)
  reports that something is waiting and that it cannot name the subsystem, rather than
  nothing. `needs_review_reasons` and the new `needs_review_kinds` are both derived from one
  `NEEDS_REVIEW_KINDS` table, so the digest wording and the machine-readable keys cannot
  drift apart.
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
