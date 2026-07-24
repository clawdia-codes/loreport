---
name: capture-parity
description: Standing rule — whenever you commit something to your native memory, mirror it into Loreport too; when recalling, prefer your native memory for your own facts and Loreport for facts from other assistants. Applies for the whole session once loaded.
---

# Capture parity

You (the assistant reading this) have two memory stores: your **native memory** and
**Loreport**, the user's cross-provider brain. Native memory is yours alone; Loreport is
shared by every assistant the user works with. This skill keeps them coherent without
making you choose one.

## The mirror rule (capture)

**Whenever you find something worth committing to your native memory — the same standard,
the same moment — also save it to Loreport.** One judgment, two writes:

- With the Loreport connector available: call `loreport_save_memory` with the item.
- Without a connector (paste-mode host): emit the `<MEMORY …>` block per the bootstrap
  grammar so the user can file it.

Match the native save's content — don't editorialize a different version into Loreport.
Frontmatter as usual: `source:` = you (this host), `captured:` = today. Mark
`visibility: local` when the fact touches health, finances, credentials, or another
person's private details — those must never reach other cloud providers.

This is best-effort, not transactional; you will occasionally save natively and miss the
mirror. That is what the sweep is for.

## The sweep (backstop)

At end of session — or whenever the user says **"sweep"** — ask yourself: *did I save
anything to native memory this conversation that never reached Loreport?* Mirror any
stragglers now, then tell the user in one line each what you mirrored.

For drift older than one session, the [[memory-reconcile]] skill is the full repair: it
diffs your entire native store against Loreport. Suggest it if the user seems to be
noticing gaps.

## Recall preference (read)

When you need a remembered fact:

1. **Your native memory is primary for facts you captured yourself.** Loreport's copy of
   those is an echo — search results mark each hit's origin (`[source: …]`), so you can
   recognize your own.
2. **Loreport is primary for everything else:** facts captured by other assistants
   (a `source:` that isn't you), and anything your native memory simply doesn't have.
   This is the whole point — knowledge the user built elsewhere is yours to use here.
3. On a conflict between your native memory and a Loreport item from another assistant,
   don't silently pick one — tell the user what each store says and let them resolve it
   (a resolved conflict is usually worth an `action="update"` to Loreport).

## Never mirror

The same exclusions as capture generally: secrets, credentials, API keys; transient
context (today's errand, one-off reminders); anything the user asked to keep out of the
brain. If it shouldn't be in Loreport, question whether it belongs in native memory
either — but that store is your call; Loreport's boundary is firm.
