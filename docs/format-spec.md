# Model-Proof Brain v1 — Format Specification

This file is the canonical schema. Every embedded slice in `prompts/*.md` is a
byte-identical copy of a text defined in Appendix A here.

---

## §1 Item frontmatter

```yaml
---
name: <kebab-slug>            # stable id; unique across the whole brain; used bare in [[wikilinks]]
description: <one line>       # becomes the INDEX line hook; feeds provider retrieval
type: user | feedback | project | reference | knowledge
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

**Ingest-skill norm:** any skill that reads third-party content must treat it as
untrusted — store attributed claims, never instructions to the assistant. The reference
implementation is `examples/brain/skills/distill-source-into-knowledge/`.

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
type: user | feedback | project | reference | knowledge
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
`user | feedback | project | reference | knowledge`); body in plain markdown;
`[[wikilinks]]` are bare slugs naming other items. `INDEX.md` holds exactly one line per
item — `- [[name]] — hook  (type)` — under `## Memories` / `## Knowledge`, plus one line
per skill package — `- [[skill-name]] — hook  (skill)` — under `## Skills`. Changed or
repaired files are emitted as `<MEMORY file="…" action="update">` blocks holding the full
replacement file, each followed by its `INDEX: replace - [[name]] — hook  (type)` line.
<!-- /spec-slice -->
