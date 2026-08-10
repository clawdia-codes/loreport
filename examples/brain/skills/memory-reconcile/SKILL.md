---
name: memory-reconcile
description: Diff this assistant's native memory against Loreport and repair the differences — skip what matches, add what's missing, update what changed. Run at onboarding and any time the user says "reconcile my memories". Repeatable and safe to re-run.
---

# Memory reconcile

Bring Loreport up to date with everything durable in your (this assistant's) native
memory. This is the same operation as onboarding's import — made repeatable. Running it
twice in a row should find nothing to do the second time.

## ① Dump your native memory

List everything you currently hold about the user: saved memories, custom-instruction
facts, project knowledge — whatever your host persists across chats. Work from the full
list, not highlights. If your host has no native memory, say so and stop; there is
nothing to reconcile.

## ② Fetch Loreport's view

Get the brain's current catalog: `loreport_load_context` (or the pasted PROFILE + INDEX
on a paste-mode host), plus `loreport_search_memories` per topic as you triage. Search
hits are marked `[source: …]` — your own past captures are included; you need them here,
they are exactly what you're diffing against.

## ③ Triage each native fact — three outcomes

Go fact by fact through the native dump. For each one, exactly one of:

- **Match** — Loreport already holds it and the substance agrees (wording may differ;
  judge the fact, not the phrasing). → **Skip.** Say nothing, add nothing. Near-duplicate
  items are how brains rot.
- **Missing** — Loreport has nothing covering it. → **Add**: `loreport_save_memory` (or
  emit the block) as `action="new"`, normal frontmatter, `source:` = you, `captured:` =
  today.
- **Conflict** — Loreport holds the fact but the truth has changed (moved city, switched
  jobs, project renamed, preference reversed). → **Update, not new**: fetch the existing
  item first (fetch-before-update rule), then `action="update"` on the **same** item name
  with the full replacement text. Never add a second item for the same fact — one fact,
  one item, or consolidation inherits a graveyard of stale variants. If the *old* state
  matters historically, keep one line of it in the updated body ("previously: …").

When you genuinely can't tell whether your native fact or Loreport's version is current —
ask the user; that answer is itself the update.

## ④ Filter — durable knowledge only

Native memory accumulates junk. Do **not** import: greetings and rapport notes, one-off
reminders, transient tasks, obsolete plans, anything conversational rather than durable.
The test: *will this still be useful in six months?* When a native fact fails the test,
skip it silently — reconciliation is not an archive of everything, it is a repair of the
durable record.

Visibility on every add/update: always state it — `shared`, or `visibility: local` when
the fact touches health, finances, credentials, or another person's private details. An
item that omits the field is withheld from every provider, not shared. Never write
secrets, credentials, or API keys to the brain at all.

## ⑤ Report

Close with a compact summary: `N matched (skipped) · N added · N updated · N filtered
out`, listing the added/updated item names one line each. If anything was ambiguous and
you asked the user, note how it resolved. The user should be able to audit the whole run
from this summary.

## Direction and scope

This skill reconciles **native → Loreport** only. It never writes to your native memory
and never deletes anything from either store — deletions are a human decision, made
directly or at consolidation. Loreport items from *other* assistants are out of scope
here: you diff against them (a fact captured by another assistant still counts as
**Match**), but you never modify them from a reconcile run.
