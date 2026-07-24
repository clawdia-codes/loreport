# Loreport — Design, Architecture & Code Review

**Date:** 2026-07-24 · **Reviewers:** three independent passes (model: Fable 5) — architecture/design,
code correctness/security, simplicity/UX/privacy. Findings consolidated and deduped below, most-severe
first, each tagged with its source pass. Nothing here was auto-applied except the four items marked
**[FIXED 2026-07-24]** (live security holes on the running connector).

## Verdict

The **Tier-1 core is genuinely good** — clean tier separation, honest "strict-superset" degradation,
INDEX-as-derived-artifact with deterministic rebuild, byte-identical spec-slice discipline, and an
unusually honest `VERIFICATION.md` / "How this compares" section. The **Tier-2 hub is where the risk
is**: it has security holes on its live write path, a cluster of concurrency defects that contradict the
multi-provider story it exists to deliver, and — the single highest-value gap — **no sensitivity
tiering**, so a brain is all-or-nothing and everything in it is broadcast to every connected provider.

Fix the criticals before the hub fronts a real brain over a real connector. The privacy tiering
(`visibility: local|shared`) is the most important *design* change and unlocks safe real-memory sync.

## What's genuinely solid (keep as-is)
- Tier-1 stands alone; hub outage degrades to exactly Tier-1 through the same emit-grammar + gate.
- INDEX rebuild is byte-deterministic (sorted, stable, mtime-independent; valid on empty brain).
- `brain_read` name-validation blocks traversal on the **read** side; localhost bind is hard-enforced.
- `_as_tool_result` MCP wrapping, `_write_response` BrokenPipeError guard, stdio notification guard.
- The extractability warning, honest prior-art comparison, rotate-first framing, residual-retention
  table, and the answer-key-outside-the-fixture test design.

---

## CRITICAL

### 1. `brain_capture` is an arbitrary file-write/delete primitive (path traversal) — **[FIXED 2026-07-24]**
*(code C1/C2, arch F1)* The emit block's `file="…"` is never directory-validated. On new/update, an
absolute or `../` path escapes the repo, and even in-repo `file="prompts/bootstrap.md"` overwrites the
operating surface that `snapshot_publish.py` pins into **every** provider — a prompt-injected ChatGPT
capture becomes a persistent cross-provider system-prompt compromise. On **delete**, `validate_schema`
short-circuits with *no* path check and *no* scan, so `<MEMORY file="PROFILE.md" action="delete">`
deletes the identity file (or any tracked file, incl. hub code) with zero credential and zero content.
**Fix applied:** a path allowlist enforced for *all* actions before any fs/git op — reject absolute/`..`,
require prefix `memories/|knowledge/|skills/` + `.md`; delete requires the target to pre-exist.

### 2. Credential→branch binding fails open — **[FIXED 2026-07-24, hardened]**
*(code H2, arch F2)* `provider = provider_from_credential or arguments.get("provider")` — with no/unknown
credential the caller picks its own branch, and `CREDENTIAL_PROVIDER_MAP` falls back to guessable
in-source dev tokens. **Fix applied:** brain_capture now fails closed — no recognized credential →
refuse to route (no argument fallback). *(Note: the live ChatGPT tunnel was already branch-pinned via
its stdio `--credential` + env map, so that path wasn't wide-open — but the fail-open default is now
closed.)* **Recipe (not yet done):** delete the in-source dev-token defaults for real multi-user use.

### 3. No sensitivity tiering — the brain is all-or-nothing, and "all" goes to every provider *(privacy C1)*
There is no notion of a local-only item anywhere in the format, prompts, or hub. The Projects recipe
uploads *all* detail files; `brain_read`/`search`/`surface` serve any item to any connected provider;
`snapshot_publish` republishes the full surface everywhere. The **only** content gate is the
secret-scrub, which catches credential-*shaped* strings — "salary is 1.2M NOK", "therapy Tuesdays",
health/finance/relationships pass every gate by design. Net: Loreport can turn a per-provider silo into
a consolidated dossier synced to OpenAI **and** Anthropic **and** GitHub at once — a privacy *downgrade*
the docs never surface. **This is the #1 design fix. Recipe:** add `visibility: shared|local` frontmatter
(default `local` for health/finance/relationship items); enforce mechanically in publish/merge/read/upload
so `local` items never leave the machine (INDEX may list existence, never body); add one onboarding
question routing sensitive items to `local`. *(This review's manifest for Theie-Brain already applies
this split by hand; the field makes it durable.)*

### 4. `consolidate.md` uploads your entire brain to a cloud model to scrub it *(privacy C2)*
The monthly janitor says "I will give you my entire brain (pasted below)" — the scrub's *input* is the
single largest disclosure event in the design (pre-scrub secrets + every sensitive item → whichever
provider runs it), and nothing warns the user. **Recipe:** run consolidation on a filesystem/local
model; if pasting to a cloud host, state plainly that everything pasted is disclosed regardless of what
the plan later redacts. Add the missing row to the residual-retention table.

---

## HIGH

### 5. Concurrency: one shared working tree, no locking; the nightly merge can silently drop a capture
*(arch F3, code H3)* Three actors mutate one tree with no lock. `commit_block` does
`git checkout provider/<name>` and never restores. Worst case: step-7 `git branch -f provider/X main`
**discards** any capture committed to `provider/X` after it was merged — permanently, no quarantine, no
digest. Two concurrent captures can land A's file on B's branch (identity binding defeated at the data
layer) or die on `.git/index.lock`. A capture mid-merge can abort/corrupt the merge. **Recipe:**
worktree-per-op for writes (`git worktree add` → write/add/commit → remove; primary HEAD never moves) +
a repo `flock` around branch-force; make step-7 a compare-and-swap (only ff if `provider/X` still points
at the merged SHA).

### 6. Reads serve the working tree, not `main` — docstrings claim `main`, search lags up to 24h
*(code H1, arch F9)* `brain_read/search/surface` and `snapshot_publish` open `brain_dir` paths directly;
after any capture the tree is on a provider branch, so reads/publish can serve un-merged, un-scrubbed,
stale content while the packet footer falsely stamps `main@<hash>`. `INDEX.md` only rebuilds at the
nightly merge, so a capture→search within one session returns "(no matches)" — a model reads that as
"not saved" and re-captures. **Recipe:** read via `git show main:<path>` (plumbing, no checkout, no
race); either accept + document the search lag or live-scan frontmatter on INDEX miss. Orthogonal to #5
— needed even with perfect write locking.

### 7. Merge conflict handler forks "same item edited twice" into contradictory twins *(arch F4)*
Every non-PROFILE conflict takes `--ours` and renames theirs `<name>-2`. Correct for independent
coinage; wrong for the common case (two providers each `update` the *same* item) — it forks one item
into two indexed near-twins → memory drift. Plus `rename_with_suffix` always yields `-2` and
`open("w")` **silently overwrites** an existing `name-2` (data loss), and modify/delete conflicts
resurrect+duplicate the deleted item. **Recipe:** use merge stages to classify add/add vs update/update;
for update/update pick a documented winner + digest "N bytes discarded, recover at pre-merge tag";
uniqueness loop for real renames; handle modify/delete explicitly.

### 8. Trust-order inversion — the least-trusted provider wins PROFILE conflicts *(arch F6)*
HUB.md says openclaw is highest-trust and merges first, but PROFILE resolves `--theirs` (last writer
wins), so chatgpt — behind a No-Auth connector, most injectable — gets the last word on the identity
file pinned into every session everywhere. Items resolve `--ours` (earliest wins): the two precedence
rules contradict, neither documented as intentional, and the merge commits before any human sees it.
**Recipe:** PROFILE → `--ours` (consistent with trust order), or park incoming PROFILE in quarantine
until confirmed; document precedence per artifact class.

### 9. The "fail-closed three-layer scrub" is four regexes run three times *(both, F5/M4-priv)*
Identical `SECRET_PATTERNS` (sk-, ghp_, AKIA, `key:value`) in all three scripts. Misses fine-grained
PATs, Slack `xox*`, Google `AIza`, JWTs, PEM blocks, connection strings, any password with a special
char, and prose ("my password is hunter2"). Three "layers" = the same blind detector at three points.
**Recipe:** vendor a serious corpus (gitleaks regexes are MIT/stdlib-portable) + Shannon-entropy check
at the egress gate; state in security.md that regex scrubbing is best-effort and never-capture is the
real control.

### 10. Imperative-scan quarantines the format's own idioms *(both, F10/H5)*
`IMPERATIVE_TRIGGER_RE` matches `always|never|remember|from now on|ignore|disregard` anywhere; only
lines *starting* with "source says:" are rescued. So genuine first-person memories — "User always
prefers dark mode", the format's own mandated `**How to apply:** Always…`, even bootstrap's "**Never**
save secrets" — quarantine at a high rate, pushing honest traffic around the gate. Meanwhile "you
should henceforth…" passes. **Recipe:** scope to second-person/assistant-directed unattributed
imperatives; for `type: user|feedback` self-statements downgrade to a digest flag. *(Directly blocks
real-memory sync — the feedback memories are full of "always/never" — so this lands before Theie-Brain
ingest, or ingest bypasses the gate by committing to branches directly.)*

### 11. Adoption cliffs: ChatGPT instruction-field too small; manual multi-file saves *(privacy H4/H5)*
`bootstrap.md` (~3.5–4K chars) doesn't fit ChatGPT's ~1,500-char custom-instruction boxes, so the
Tier-1 loop silently becomes "repaste every new chat" — undocumented, and exactly where a non-expert
quits. Onboarding also hands back 10–20 fenced blocks the user must save to exact paths by hand
(impossible on mobile). **Recipe:** document real per-product field limits + a `bootstrap-mini`; and
either ship a 40-line stdlib `save_blocks.py` (not an "app") or promote "have a filesystem agent run
onboard.md — it writes the files itself" in the README quickstart.

### 12. HTTP transport replied to JSON-RPC notifications — **[FIXED 2026-07-24]**
*(code H4)* The `"id" not in req` guard existed only in `run_stdio`; the HTTP handler returned a `-32601`
error for `notifications/initialized`, desyncing strict clients. **Fix applied:** guard moved into
`handle_request` (returns "no response"); HTTP now emits 202 no-body for notifications.

---

## MEDIUM

- **13. Git failures misreported as "quarantined" + dirty tree left behind** *(code M1)* — a re-captured
  identical `update` → `git commit` "nothing to commit" (exit 1) → RuntimeError → reported quarantined
  but nothing quarantined, staged change poisons the next merge. Wrap `commit_block`; detect the no-op;
  clean state on failure.
- **14. Quarantine writes the *unmasked* secret to a tracked dir** *(code M2, arch F7)* — a secret-hit
  block is copied verbatim to `hub/quarantine/…`, which is **not** git-ignored; one `git add -A` (the
  docs' own snapshot ritual) commits the plaintext. **Recipe:** git-ignore `hub/quarantine/`, `hub/logs/`
  (and move quarantine outside the repo, or redact in the copy). *(Will apply the .gitignore fix to the
  live brain + brain-template.)*
- **15. Digest / "human confirmation" is prose, not mechanism** *(arch F8)* — the report goes to a log
  nobody reads; PROFILE overwrite + renames commit before anyone sees them. Write a `hub/digest-<date>.md`,
  exit nonzero / notify-hook on conflicts, end HUB.md's ritual pointing at a real file.
- **16. Update = full replacement with no fetch-first rule** *(arch F12)* — on paste hosts a model holds
  only the INDEX line, so a "full replacement" reconstructed from one line amputates the body. One
  sentence in bootstrap.md: never emit `update` for an item whose body you haven't read this session.
- **17. Token-frugality claim overreaches** *(arch F11)* — the pinned floor *is* linear in items (small
  constant), not constant; at ~300 items the packet hits custom-instruction char limits with no archive
  policy. Reword the claim; define an `## Archive` overflow policy; run the still-unverified Projects
  retrieval check (#6b) before recommending Projects as primary.
- **18. `brain_merge` scrub is fail-**open** under an exception** *(code M5)* — merges are already
  committed to `main` when the scan runs; a raised exception (vanished/permission-changed file) dies
  after commit, before the reset, leaving un-scrubbed content on `main`. Wrap the post-merge section so
  any exception triggers the rollback.
- **19. No subprocess/git timeouts** *(code M3)* — a stuck `index.lock` hangs the MCP request forever.
  Add `timeout=` everywhere; clean error on `TimeoutExpired`.
- **20. `--test-scrub` returns exit 1 for both PASS and FAIL** *(code M4)* — the self-test is
  unfalsifiable in CI. Exit 0 when the injected secret is caught.
- **21. Imperative/secret scans miss frontmatter + INDEX line** *(code M6)* — a directive in
  `description:` or the `INDEX:` line rides through. Scan the full `raw`.
- **22. Provider set hardcoded in three files; `source:` open-vocab vs closed branch enum** *(arch F13)* —
  adding Gemini means editing three scripts + knowing merge order encodes trust. One
  `hub/config/providers.json`; spec: `source` is free-form, branch-correspondence only for hub providers.
- **23. Tier terminology collision** *(privacy M1)* — Tier 1/2 = manual/hub in README, but Tier 1/2/3 =
  Paste/Filesystem/Projects in load-paths. Rename the load paths to capability names.
- **24. Provider re-absorption** *(privacy M2)* — with the brain loaded, native memory re-saves brain
  facts back into the silo Loreport meant to liberate. Recommend disabling native memory once Loreport
  is the system.
- **25. README→HUB.md "setup" is an operator ritual, not setup** *(privacy M5)* — no walk-through for
  creating `provider/*` branches or setting `MPB_*` tokens; the ChatGPT-connector dead-end is buried.
  Add a numbered Setup section.
- **26. Internal jargon leaks into user docs** *(privacy M6)* — `PD-11`, `design.md §D15/§D17`,
  `S-5/S-6/S-7` reference docs that don't ship. Grep `PD-|§D|S-[0-9]` and inline/delete.

## LOW (polish / YAGNI)
Five item-types is ~two too many (collapse or say "type is cosmetic"); `meta.yaml` duplicates SKILL.md
frontmatter (drop, triggers into description); INDEX "exactly two spaces" byte-rule (make "one or
more"); no PROFILE update path in consolidate (add a PROFILE-review step); HUB.md merge-order note
self-contradicts ("alphabetical" then claude→chatgpt); Tier-2 git ceremony heavy for one user (document
a single-branch "hub-lite" on-ramp); JSON-RPC batch arrays unhandled; NUL/`open()`-invalid chars in a
read name raise an unwrapped 500; `..`-substring rejects legit `foo..bar`; non-atomic packet publish
(temp+rename); `MEMORY_RE` fixes attribute order (file-then-action); skill upload naming collision
(several flat `SKILL.md` — upload as `<skill>-SKILL.md`); `consolidate.md` has no chunking mode at scale.

---

## Improvement recipe (prioritized)

**P0 — before the hub fronts a real brain over a live connector** *(security)*
1. [DONE] Path allowlist for all actions (#1); credential fail-closed (#2); HTTP notification conformance (#12).
2. Git-ignore `hub/quarantine/`, `hub/logs/`; move quarantine outside the repo or redact-in-copy (#14).
3. Delete in-source dev-token defaults; require real `MPB_*` tokens (finishes #2).

**P1 — correctness of the multi-provider story** *(reliability)*
4. Reads via `git show main:` (#6). 5. Worktree-per-op + flock + CAS fast-forward (#5). 6. Merge-stage
classifier for update/update vs add/add; uniqueness loop; modify/delete handling (#7). 7. PROFILE
precedence → ours / quarantine (#8). 8. Wrap `commit_block` + no-op detection (#13); wrap post-merge
scrub for fail-closed-on-exception (#18); subprocess timeouts (#19).

**P2 — the privacy redesign** *(the highest-value design change)*
9. `visibility: shared|local` frontmatter + mechanical enforcement in publish/merge/read/upload; default
sensitive categories to `local`; onboarding question; Projects recipe "never upload `local`" (#3).
10. Consolidation privacy warning + local-host guidance (#4). 11. Broaden secret corpus + entropy check
(#9). 12. Scope the imperative-scan; scan frontmatter+INDEX (#10, #21). 13. Reframe security.md Threat 3
to name personal data; add "never load a personal brain into a shared assistant; build a `public/` subset."

**P3 — adoption & honesty** *(UX/docs)*
14. Document real per-provider field limits + `bootstrap-mini`; ship/promote a file-writing fast path
(#11). 15. Fetch-before-update rule in bootstrap.md (#16). 16. Digest artifact + notify hook + nonzero
exit on conflicts (#15). 17. INDEX growth/archive policy + reword frugality claim; run #6b (#17).
18. `providers.json` unify (#22); fix tier-name collision (#23); disable-native-memory rec (#24);
HUB.md Setup section (#25); strip internal jargon (#26).

**P4 — polish** — the LOW cluster, as capacity allows.
