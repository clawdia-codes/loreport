# Loreport

A portable "brain" — your memories, knowledge, and skills as plain markdown files you
own — that runs in a bare chat box with nothing installed, and imports what your
provider's memory feature already has on you so you stop starting over. It's a folder,
not a service: zero-install, grab-and-go, no lock-in, and fully readable by a human with
no tooling at all.

---

## Quickstart

1. **Open [`prompts/onboard.md`](prompts/onboard.md) in any LLM chat** and paste it in.
   This runs the INITIALIZE + BRINGUP one-shot — PORT (bring in what you already have)
   → INTERVIEW (a short, one-question-at-a-time interview) → GRAB + MERGE (extract and
   merge whatever the current provider already holds about you) — and hands you back a
   set of ready-to-file blocks. After porting a provider's native memory feature
   (ChatGPT Memory, Gemini's "Saved info", etc.) this way, either keep it on **with the
   `capture-parity` skill** (native saves get mirrored into Loreport; recommended) or turn
   it off and let Loreport be the only store — see "Native memory: two supported modes"
   in [`docs/providers.md`](docs/providers.md).
2. **Save each block to its `file=` path**, inside a folder you control. A private git
   repo is the ideal home — you now have one self-contained brain folder, reloadable
   anywhere.
3. **Pin the operating surface** per [`docs/load-paths.md`](docs/load-paths.md): paste
   `prompts/bootstrap.md` + your `PROFILE.md` + your `INDEX.md` into your assistant's
   instructions field, and keep the detail files (`memories/`, `knowledge/`, `skills/`)
   at hand. The exact recipe depends on what your host can do — chat-box paste, an
   agent with filesystem access, or a Projects-style workspace with a custom-instructions
   field — `docs/load-paths.md` covers all three, and `docs/providers.md` maps common
   products to the right one.

## How it works

Three loops, running on top of one flat folder of markdown:

- **LOAD** — every session starts by reading `INDEX.md` first, then fetching only the
  detail files that are actually relevant — never a bulk dump of everything you know.
- **GROW** — as you work, durable facts get captured as one ready-to-file block per item
  (`emit-grammar v1`, defined once in `docs/format-spec.md`), each with its own
  `INDEX:` line. Third-party text (pasted articles, imports, skill sources) is always
  treated as untrusted — captured as an attributed claim, never obeyed as an instruction.
- **CLEAN** — run `prompts/consolidate.md` periodically (monthly is a reasonable
  default) to merge near-duplicates, drop stale items, and rebuild `INDEX.md` — see
  `examples/` for a worked example of exactly this.
- **RECONCILE** — say *"reconcile my memories"* in any assistant that has its own native
  memory: it diffs that store against the brain and repairs the difference — match =
  skip, missing = add, changed = **update the existing item** (never a duplicate). This is
  how a provider's own memory feature and the brain stay coherent instead of drifting
  apart; it is re-runnable, and a clean second run finds nothing.

## Shared vs. local — what leaves your machine

Every item carries a `visibility`: **`shared`** (the default) syncs to every connected
provider; **`local`** never leaves your machine — excluded from the published packet, and
refused outright to a cloud provider that asks for it. Health, finances, credentials,
employer details, other people's private information: mark them `local` and they stay on
your disk while remaining fully yours, backed up, and readable by your local agents.

Trust is per-credential, not per-product: a local host (an agent on your own machine) may
read everything and relabel any item; a cloud host reads only `shared` items and may
relabel only what it captured itself. Review and change any of it with the
`loreport_view_memory_settings` / `loreport_change_memory_settings` tools, or by asking
your assistant. Full spec: [`docs/visibility-design.md`](docs/visibility-design.md).

## Optional: go live with the sync hub

The loop above is manual by design — you paste, you save, you run the janitor. If you
run an always-on agent host, you can *add* the opt-in **Tier-2 sync hub**: it automates
filing captures, consolidation, `INDEX.md` rebuilds, and republishing your pinned surface
across providers. It's a strict superset — a hub outage or never adopting it degrades to
exactly Tier 1, never to broken. See [`hub/HUB.md`](hub/HUB.md) to set it up.

## Skills that ship with it

Skills are prose packages — they work on any host, and do more where a runtime exists:

- **`loreport-ops`** — the single entry point for operating the brain: status, reconcile,
  sweep, search, save, settings. Installed as `/loreport` in Claude Code and by name in
  openclaw; paste it anywhere else.
- **`capture-parity`** — the standing rule that keeps a provider's native memory and the
  brain coherent: mirror every native save into Loreport, and recall native-first for your
  own facts, Loreport-first for other assistants'.
- **`memory-reconcile`** — the repeatable native → Loreport diff behind "reconcile my
  memories".
- **`distill-source-into-knowledge`** — turn a pasted article or doc into one durable
  knowledge page, treating the source as untrusted.

## Security note

> Anything **shared** in your brain is readable by anyone who can prompt an assistant it's
> loaded into. The brain is designed to never contain secrets — keep it that way. Items
> marked `local` never reach a cloud provider at all, but that is a privacy boundary, not
> a secrets vault: credentials, API keys, and tokens belong in a password manager.

See [`docs/security.md`](docs/security.md) for the full threat model and the controls
(sanitize-before-confirm, the provenance rule, and the secret-scrub gate on the hub path)
that back this up.

## What's deliberately not here

No embeddings, vector store, or knowledge graph — a flat index plus wikilinks is enough
at the scale a single person's brain actually reaches, and it stays readable without
tooling. Both choices are recorded with their reasoning *and the conditions that should
reopen them* in [`docs/architecture-decisions.md`](docs/architecture-decisions.md) — they
are decisions taken under today's constraints, not claims about all time. No hosted
service and no account: the brain is a folder you keep, not a product you depend on. No
separate "append helper" app ships, ever — capture happens inside the conversation you're
already having. Planned for later: richer per-provider import recipes as providers change
their memory features, and Tier-2 hub hardening beyond the v1 scope above.

## How this compares

A few honest notes on prior art and alternatives, so you don't have to go find them:

- **Nothing found does both tiers.** Every zero-install "portable markdown brain" project
  (Karpathy's LLM Wiki and its cottage industry of clones) assumes an agent runtime with
  filesystem access — none work pasted into a bare browser chat with nothing installed.
  Every multi-provider shared-memory project found stores memory in a database (SQLite,
  a hosted store), not human-readable git-tracked markdown, and none use a
  branch-per-provider + scheduled-merge model with a fail-closed secret scrub. This
  combination is a real gap, not a marketing claim — but the corollary is Loreport is
  also less proven: single-instance-verified, no retrieval-quality benchmark, fewer
  providers covered out of the box.
- **Closest Tier-2 sibling:** [ai-memory-mcp](https://github.com/alphaonedev/ai-memory-mcp)
  (Apache 2.0) already bridges more providers (Claude, ChatGPT, Grok, Gemini, Codex,
  Cursor, openclaw) via MCP, ships a retroactive import tool, and has published retrieval
  benchmarks Loreport doesn't. It trades git-native, human-readable markdown for a local
  SQLite store — worth a look if a wider provider set matters more than owning your data
  as plain files.
- **Smoothest casual UX:** [mem0's OpenMemory](https://mem0.ai) browser extension
  auto-injects memory into ChatGPT/Perplexity/Grok/Gemini with minimal setup. It's a
  real-time bridge, not a periodic reconciliation, and doesn't document secret/PII
  filtering the way Loreport's three scrub layers do.

## Credits & license

The index-first, one-file-per-item, wikilinked shape is validated by prior art: Andrej
Karpathy's ["LLM Wiki" gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
independently converged on the same pattern, and this project adopts and credits it.

Licensed under **MIT** (`prompts/`, `hub/` Python code) and **CC BY 4.0** (`docs/`,
`brain-template/`, `examples/`). See [`LICENSE`](LICENSE) for the full text.

## Learn more

- [`docs/format-spec.md`](docs/format-spec.md) — the canonical item/index/skill schema.
- [`docs/load-paths.md`](docs/load-paths.md) — loading recipes per host capability.
- [`docs/providers.md`](docs/providers.md) — which recipe fits which product, and the two
  supported native-memory modes.
- [`docs/security.md`](docs/security.md) — threat model and controls, in full.
- [`docs/visibility-design.md`](docs/visibility-design.md) — `shared` vs `local`, trust
  tiers, and how enforcement works.
- [`docs/onboarding-runbook.md`](docs/onboarding-runbook.md) — bringing every platform onto
  one brain, in order.
- [`docs/architecture-decisions.md`](docs/architecture-decisions.md) — why wikilinks not a
  knowledge graph, substring search not embeddings, parity not disabling native memory —
  each with the conditions that should reopen it.
- [`prompts/`](prompts/) — `bootstrap.md`, `onboard.md`, `consolidate.md`.
- [`brain-template/`](brain-template/) — an empty skeleton to copy and fill in.
- [`examples/`](examples/) — a filled fixture brain, with a worked consolidation example.
- [`hub/`](hub/) — the opt-in Tier-2 sync hub.
