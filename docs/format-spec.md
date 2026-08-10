# Loreport v1 — Format Specification

This file is the canonical schema. Every embedded slice in `prompts/*.md` is a
byte-identical copy of a text defined in Appendix A here.

---

## §1 Item frontmatter

```yaml
---
name: <kebab-slug>            # stable id; unique across the whole brain; used bare in [[wikilinks]]
description: <one line>       # becomes the INDEX line hook; feeds provider retrieval
type: user | feedback | project | reference | knowledge | person | decision
source: <host>               # provenance — assistant/app the capture came from (claude, chatgpt, openclaw, …)
captured: <YYYY-MM-DD>       # provenance — capture date
visibility: shared | local   # REQUIRED — see below; absent = local, and publish refuses
lifespan: permanent | active | temporary   # optional — absent = permanent
expires: <YYYY-MM-DD>        # optional — the archival trigger; only on temporary
domain: work | personal | both             # optional — which side of life this belongs to
---
```

- **One flat namespace.** `name` must be unique across `memories/` + `knowledge/` + `skills/`.
- **`name` must equal the filename stem** (`memories/prefers-tabs.md` ⇔ `name: prefers-tabs`).
- **Update = full replacement** (`action="update"`, §5). No diffs — diffs are host-dependent
  and error-prone to hand-apply.
- **Rename/merge only at consolidation**, updating all inbound `[[wikilinks]]` and the INDEX
  line atomically in the change plan.
- **Delete** removes the file and its INDEX line; lint flags dangling wikilinks.
- `feedback` and `project` items keep `**Why:**` and `**How to apply:**` lead-ins.
- `user`/`feedback`/`project`/`reference` live in `memories/`; `knowledge` in `knowledge/`.
- **No new types.** Skills are packages — they carry `meta.yaml`, not item frontmatter.
- **Provenance** (`source`, `captured`) tags where a captured item came from, so a reading
  assistant can weigh a memory that seems off — the point once Tier-2 has several hosts
  writing one brain. `source` is stamped from the pinned **Host** value and matches the
  writing provider's Tier-2 branch (`provider/<source>`). Hand-authored / seed items may
  omit both; consolidation preserves them.
- **`visibility`** is `shared` or `local`, and it is **required on every item**.
  **`shared`** participates in the published packet and is readable by all connected
  providers. **`local`** never leaves this machine — excluded from publish, and cloud
  provider reads refuse it.
  An item is treated as `shared` **only** on an explicit `visibility: shared`. Every other
  state — the field absent, a value that doesn't parse, no frontmatter block at all — is
  `local`. Two consequences, and they are the point:
  - **Forgetting the field withholds; it never publishes.** Until 2026-08 the field was
    optional and absent meant `shared`, so an item nobody had classified was not merely
    unfiltered — it was positively published. That was the one fail-open default in an
    otherwise fail-closed engine, and it leaked three batches on 2026-08-07.
  - **Forgetting the field is loud.** `hub/snapshot_publish.py` refuses to publish *at all*
    while any `memories/`/`knowledge/` item lacks the field, naming each one. A safe default
    that is also silent would just turn a leak into a growing pile of items no provider can
    see; the default protects, the gate reports.
  Skills are exempt because they are not items: a skill package carries `meta.yaml`, not
  item frontmatter (below), has no `visibility:` field to omit, and is always shared.
- **`lifespan`** (optional) is `permanent` | `active` | `temporary`; absent = `permanent`.
  `permanent` — identity, long-term goals, durable knowledge, architecture, decisions.
  `active` — current projects and present responsibilities. `temporary` — travel,
  reminders, context that should expire.
- **`expires`** (optional, `YYYY-MM-DD`) is the **mechanical** archival trigger, and the
  only one: an item whose `expires` is before today has its INDEX line moved to
  `INDEX-ARCHIVE.md` (see "Archive"). It belongs only on a `temporary` item — declaring
  `permanent`/`active` *and* an expiry is a contradiction and is rejected at capture. An
  item with no `expires` is never archived by machinery, so `permanent` and `active` items
  are untouchable by construction rather than by a separate rule that could drift.
- **`domain`** (optional) is `work` | `personal` | `both` — which side of life an item
  belongs to. **This is not cloud exposure.** That is `visibility`, and the two axes are
  independent: a work item may be `local` and a personal item may be `shared`. Nothing may
  infer one from the other. `domain` exists so a reader can scope itself ("only work
  context in this session"); it grants and revokes nothing.

---

## §2 Wikilinks

Bare-slug Obsidian style inline in prose: `[[other-item-name]]`. No paths, no aliases,
no header-anchors. The flat namespace makes the bare slug unambiguous. A link to an item
the reader doesn't hold is an instruction to *fetch it* (read the file, or ask the user
to paste it) — this is part of the bootstrap contract.

---

## §3 INDEX.md format

```markdown
# Index

## Memories
- [[<name>]] — <one-line hook>  (<type>)

## Knowledge
- [[<name>]] — <one-line hook>  (knowledge)

## Skills
- [[<skill-name>]] — <one-line description from meta.yaml>  (skill)
```

- `(skill)` is an **index marker only** — `skill` is NOT a valid item `type`. The enum in
  §1 governs item frontmatter; `(skill)` governs catalog display in INDEX.md only.
- Section headings are fixed (`Memories` / `Knowledge` / `Skills`).
- Two spaces before the parenthetical, exactly as shown.
- Lines are append-ordered between consolidations; sorted alphabetically at consolidation.

---

## §4 PROFILE.md structure

Four fixed headings, edited in place (never appended) so it stays fixed-size:

- **Identity** — name/handle, role, one-paragraph context
- **Goals** — 3–5 bullets of current top-level goals
- **Preferences** — working style, format/tone preferences, standing corrections
- **Boundaries** — always contains the standing line:
  `- Never save secrets, credentials, API keys, or sensitive personal data to this brain.`

The Boundaries section is load-bearing for security: it travels with the profile onto
every host, so the never-save rule is present even when the full bootstrap protocol is
not pinned alongside.

---

## §5 Emit grammar

The model outputs one fenced code block per capture — a **ready-to-file** artifact:
- **`file=`** gives the exact save path; **`action=`** is `new`, `update`, or `delete`.
- The outer fence uses one more backtick than the longest inner fence (fence-nesting
  rule) so the copy button on chat UIs grabs the whole block.
- Chat UIs render a copy button on fenced code blocks; the XML-ish tags are not swallowed
  as HTML — this is why the format is always a fence, never raw XML.
- Per-action INDEX semantics: `new` → append `- [[name]] — …  (type)`;
  `update` → `INDEX: replace - [[name]] — …  (type)` (swap old line for new);
  `delete` → `INDEX: remove - [[<name>]]`.

The canonical block is in Appendix A (`emit-grammar v1`); `bootstrap.md` and `onboard.md`
embed it byte-identically.

---

## §6 Skill package shape

```
skills/<name>/
├── SKILL.md      ← frontmatter (name, description) + prose procedure — the skill itself
├── meta.yaml     ← name, description (feeds the INDEX line), trigger phrases
└── assets/       ← optional convenience templates and examples
```

The prose core in `SKILL.md` alone is the skill; `assets/` is optional (graceful
degradation). `meta.yaml` feeds tooling and the INDEX line.

**The two `description` fields differ on purpose — do not "fix" them to match.**
`SKILL.md`'s is a *dispatch trigger*: it tells a host when to invoke the skill, so it
names the phrases a user might say and runs long. `meta.yaml`'s is a *catalog hook*: it
becomes the one-line `INDEX.md` entry that sits in context every session, so it must stay
short. Forcing them identical either bloats the always-loaded index or weakens skill
triggering. What **must** agree is `name` — the directory name, `SKILL.md`'s `name:`, and
`meta.yaml`'s `name:` are the same slug, because hosts resolve a skill by its directory
and the INDEX links it by slug. `scripts/check-docs.sh` enforces that.

**Ingest-skill norm:** any skill that reads third-party content must treat it as
untrusted — store attributed claims, never instructions to the assistant. The reference
implementation is `examples/brain/skills/distill-source-into-knowledge/`.

---

## Archive

The pinned floor (bootstrap + `PROFILE.md` + `INDEX.md`) is fixed-size per session, but
`INDEX.md` itself still grows one line per item — linear, with a small constant. At a
large enough item count that starts to matter: on a paste host it eventually crowds a
custom-instructions character limit; even on a filesystem host it's just more noise to
scan per session than a genuinely "hot" catalog needs to be.

**This now ships** (it was a deferred design note through v1.10). `INDEX.md` is a hot/cold
pair: an item whose `expires` date has passed has its INDEX line moved into
`INDEX-ARCHIVE.md`, rebuilt deterministically by `hub/brain_merge.py` alongside `INDEX.md`.
`INDEX.md` stays the always-in-context catalog of what's active; `INDEX-ARCHIVE.md` is a
cold shelf a filesystem host fetches on demand ("anything archived about X?") rather than
something pinned into every session. The file is only created once something is actually
on the shelf.

Three properties make this safe to run unattended, and each is worth stating because the
obvious implementation gets them wrong:

- **Only the catalog line moves.** The item's file stays exactly where it is, still
  readable, still `[[wikilink]]`-resolvable, still merged and secret-scrubbed like any
  other. Archiving is a hot/cold split of the index, never a deletion.
- **The trigger is a date comparison, not a judgement.** `expires` before today. No
  duration math, no model guess about staleness, and no notion of "untouched" — which
  would need access-time tracking the brain deliberately doesn't keep.
- **The cloud seam.** Cloud providers receive the published *packet*, not the repository,
  and cannot lazy-fetch a cold shelf. So the packet carries `INDEX.md` **and**
  `INDEX-ARCHIVE.md`, both through the same visibility filter — otherwise archiving a
  `shared` item would silently revoke every cloud assistant's access to it while every
  health check stayed green. The only thing ever excluded from the packet remains
  `visibility: local`; that invariant is unchanged.

---

## Appendix A — Canonical slice texts

Every embedded slice in `prompts/*.md` is a byte-identical copy of one block below.
Each slice is wrapped in opening and closing sentinel comments; `diff` on the extracted
ranges must produce zero output.

| Slice | Canonical text lives | Embedded byte-identically in | Content |
|---|---|---|---|
| `emit-grammar v1` | `format-spec.md` Appendix A | `bootstrap.md`, `onboard.md` | Fence + four rule bullets (knowledge form, update/replace, delete/remove, fence-nesting) |
| `profile-template v1` | `format-spec.md` Appendix A | `onboard.md` | Four-heading PROFILE skeleton with standing Boundaries line |
| `rules-compact v1` | `format-spec.md` Appendix A | `consolidate.md` | Compact item + INDEX line rules + `<MEMORY action="update">` output wrapper |

### emit-grammar v1

<!-- spec-slice: emit-grammar v1 — verbatim copy; canonical text: docs/format-spec.md Appendix A -->
```
<MEMORY file="memories/<kebab-slug>.md" action="new">
---
name: <kebab-slug>
description: <one line — this becomes the index line>
type: user | feedback | project | reference | knowledge | person | decision
source: <host this was captured in — from the Host: line if set, else your best guess or unknown>
captured: <YYYY-MM-DD>
visibility: shared | local    # REQUIRED — always state it; omitting it withholds the item AND blocks publish
lifespan: permanent | active | temporary   # optional — omit for permanent; temporary = context that should expire
expires: <YYYY-MM-DD>         # optional — only on temporary; set it whenever a real end date is knowable
domain: work | personal | both # optional — which side of life; NOT exposure, that is visibility
---
<the fact, in plain markdown. For feedback/project add:
**Why:** <why this matters>
**How to apply:** <what to do differently>>
</MEMORY>
INDEX: - [[<kebab-slug>]] — <description>  (<type>)
```
- Topic/reference pages: `file="knowledge/<name>.md"`, `type: knowledge`.
- If a new fact **changes** an existing item: same block, `action="update"`, the existing
  name, the **full replacement text** (never a diff), and the trailing line
  `INDEX: replace - [[<name>]] — <description>  (<type>)` — swap the old index line for it.
- If an item is now **wrong**: `<MEMORY file="…" action="delete"></MEMORY>` plus
  `INDEX: remove - [[<name>]]`.
- If the body itself contains a code fence, open the outer fence with one more backtick
  than the longest fence inside (four instead of three) — otherwise the block splits and
  the copy button grabs only half of it.
<!-- /spec-slice -->

### profile-template v1

<!-- spec-slice: profile-template v1 — verbatim copy; canonical text: docs/format-spec.md Appendix A -->
```
# Profile
## Identity
…
## Goals
…
## Preferences
…
## Boundaries
- Never save secrets, credentials, API keys, or sensitive personal data to this brain.
- …my additions…
```
<!-- /spec-slice -->

### rules-compact v1

<!-- spec-slice: rules-compact v1 — verbatim copy; canonical text: docs/format-spec.md Appendix A -->
Every item: YAML frontmatter with `name` (kebab-slug, unique across the whole brain,
equal to the filename stem), `description` (one line), `type` (one of
`user | feedback | project | reference | knowledge | person | decision`); body in plain markdown;
`[[wikilinks]]` are bare slugs naming other items. `INDEX.md` holds exactly one line per
item — `- [[name]] — hook  (type)` — under `## Memories` / `## Knowledge`, plus one line
per skill package — `- [[skill-name]] — hook  (skill)` — under `## Skills`. Changed or
repaired files are emitted as `<MEMORY file="…" action="update">` blocks holding the full
replacement file, each followed by its `INDEX: replace - [[name]] — hook  (type)` line.
<!-- /spec-slice -->
