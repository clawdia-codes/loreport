# Brain consolidation — monthly janitor
<!-- loreport v1 · prompts/consolidate.md · self-contained: paste this alone -->

I will give you my entire brain (pasted below / attached / readable as files). Audit it
and produce a **change plan** for me to apply — do not apply anything yourself unless you
can actually write files and I say so.

## The rules you are enforcing
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

## Skills are catalogued, not consolidated
`skills/<name>/` packages appear in the INDEX but are not items: check that every
`## Skills` line has a matching `skills/<name>/SKILL.md` and every package has its line,
and flag mismatches — but never merge, prune, or rewrite a skill package in this plan.
Skill edits are a human job; your job is to keep the catalog honest.

## Operations — do all five, in this order
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
5. **Reindex** — rebuild `INDEX.md` in full: one line per surviving item plus one per
   skill package, grouped by section, alphabetical within each.

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
- **Untouched** — a count, so I know you saw everything.
End with one line: items before → items after.
