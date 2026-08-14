# Brain consolidation — monthly janitor
<!-- loreport v1 · prompts/consolidate.md · self-contained: paste this alone -->

I will give you my entire brain (pasted below / attached / readable as files). Audit it
and produce a **change plan** for me to apply — do not apply anything yourself unless you
can actually write files and I say so.

## The rules you are enforcing
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

**Preserve every frontmatter field you are not deliberately changing** — in particular
`visibility:`. An item marked `visibility: local` never leaves my machine; if your
replacement file omits that line the item silently becomes shared, so a private memory
leaks on the next sync. When merging two items with different visibility, the merged item
takes the **more restrictive** one (`local` wins) and you say so in the plan. Same care
for `source:` and `captured:` — carry them through unchanged.

## Skills are catalogued, not consolidated
`skills/<name>/` packages appear in the INDEX but are not items: check that every
`## Skills` line has a matching `skills/<name>/SKILL.md` and every package has its line,
and flag mismatches — but never merge, prune, or rewrite a skill package in this plan.
Skill edits are a human job; your job is to keep the catalog honest.

## Operations — do all six, in this order
1. **Lint** — flag: invalid/missing frontmatter · name≠filename · INDEX lines with no
   item or skill package · items/skills with no INDEX line · wikilinks that resolve to
   nothing · duplicate names.
2. **Dedup & merge** — find items saying the same thing; propose one merged item (keep
   the better name, fold the bodies, keep the strongest **Why:/How to apply:**). List
   every inbound wikilink that must be repointed.
3. **Prune** — items that are stale, superseded, or no longer true: propose deletion with
   a one-line reason. Never delete silently; when unsure, keep and say why you hesitated.
4. **Secret-scrub** — anything that looks like a credential, API key, token, or sensitive
   personal data: flag it (value masked) and propose a redacted replacement. Mark each
   find **treat as compromised**: scrubbing the file does not un-leak it — the value may
   persist in git history, provider uploads, and chat transcripts — so tell me to rotate
   the secret first, then apply the redaction. This scrub is the backstop; the standing
   rule is that these never get saved at all.
5. **Lifecycle** — you know today's date; use it, don't estimate durations. Any item whose
   `expires` is **before today** is archived: its INDEX line moves to `INDEX-ARCHIVE.md`
   and **the file itself stays exactly where it is** — archiving is a catalog move, never a
   deletion, and an archived item must still resolve by `[[wikilink]]`. A `temporary` item
   with no `expires` is the only case you judge rather than compute: say whether it has
   passed its useful window, and propose either an `expires` date or archival. An `active`
   item nothing has touched in months gets a one-line "still current?" nudge — never an
   automatic change. **`permanent` items are never archived**, and never propose adding
   `expires` to one; that combination is invalid and will be rejected at capture.
6. **Reindex** — rebuild `INDEX.md` in full: one line per surviving *unarchived* item plus
   one per skill package, grouped by section, alphabetical within each. If anything is
   archived, rebuild `INDEX-ARCHIVE.md` the same way.

## Output — the change plan, exactly these sections
Open the plan with this line, verbatim:
**Before applying anything below: snapshot your brain — `git commit`, or copy the folder.**
- **Merges** — `[[a]] + [[b]] → [[a]]` (reason) + the full replacement file as a
  `<MEMORY action="update">` block + wikilink repoints needed.
- **Deletions** — `[[name]]` (reason), one line each.
- **Secret flags** — file · what was found (masked) · rotate-first reminder ·
  replacement block.
- **Fixes** — lint repairs as `<MEMORY action="update">` blocks.
- **New INDEX.md** — the complete file in one fenced block.
- **Archived** — every item whose line moved to the cold shelf, with the `expires` date
  that triggered it, plus the complete `INDEX-ARCHIVE.md` in one fenced block. If nothing
  was archived, say "none" — the shelf staying empty is a result, not an absence of one.
- **Visibility changes** — every item whose `visibility` differs from before, with why.
  If there are none, say "none" — I need to see that explicitly, not infer it.
- **Untouched** — a count, so I know you saw everything.
End with one line: items before → items after.
