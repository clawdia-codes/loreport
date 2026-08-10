# Loreport

A portable "brain" — your memories, knowledge, and skills as plain markdown files you
own — that runs in a bare chat box with nothing installed, and imports what your
provider's memory feature already has on you so you stop starting over. It's a folder,
not a service: zero-install, grab-and-go, no lock-in, and fully readable by a human with
no tooling at all.

---

## Quickstart

1. **Run `./scripts/init-brain.sh`.** It creates your brain folder, initialises git, and
   optionally sets up a **private** GitHub backup — verifying privacy with the API before
   it uploads anything. Then **copy [`prompts/onboard.md`](prompts/onboard.md) into any LLM
   chat**: it interviews you, pulls in what your existing assistants already know about
   you, and hands back ready-to-file blocks. Save each to the path in its `file=` line.
2. **Run `./make-surface.sh` in that folder** to assemble `surface.md`, then paste that
   one file into your assistant's instructions field. Done — the brain is loaded. Anything
   you marked `local` is withheld from that file, so it's safe to paste into a cloud chat.

Everything past that (which recipe suits your host, adding a second assistant, monthly
upkeep) is one file: **[`docs/setup.md`](docs/setup.md)**. You shouldn't need to open a
third.

> **This repo is the tooling; your brain is a separate, private repo of your own.**
> Not a fork of this one, not a branch in it. `init-brain.sh` creates it fresh and
> refuses to place it inside any existing git repository. Nothing you capture ever
> comes back here.

## How it works

Four loops, running on top of one flat folder of markdown:

- **LOAD** — every session starts by reading `INDEX.md` first, then fetching only the
  detail files that are actually relevant — never a bulk dump of everything you know.
- **GROW** — as you work, durable facts get captured as one ready-to-file block per item
  (`emit-grammar v1`, defined once in [`docs/format-spec.md`](docs/format-spec.md)), each
  with its own `INDEX:` line. Third-party text (pasted articles, imports, skill sources)
  is always treated as untrusted — captured as an attributed claim, never obeyed as an
  instruction.
- **CLEAN** — run [`prompts/consolidate.md`](prompts/consolidate.md) monthly to merge
  near-duplicates, drop stale items, and rebuild `INDEX.md`.
- **RECONCILE** — say *"reconcile my memories"* in any assistant that has its own native
  memory: it diffs that store against the brain and repairs the difference — match = skip,
  missing = add, changed = **update the existing item** (never a duplicate). This is how a
  provider's memory feature and the brain stay coherent instead of drifting apart; it's
  re-runnable, and a clean second run finds nothing.

**Why it stays cheap:** only `surface.md` (protocol + `PROFILE.md` + `INDEX.md`) is ever
always-loaded. It grows one line per item, not one file per item.

## Shared vs. local — what leaves your machine

Every item carries a `visibility`, and it is required: **`shared`** syncs to every
connected provider; **`local`** never leaves your machine — excluded from the published
packet, and refused outright to a cloud provider that asks for it. There is no shared-by-
default: an item that omits the field is treated as `local`, and publishing refuses until
you say which you meant, so forgetting to classify something can never expose it. Onboarding offers five always-local topics
by default — **health, finances, relationships, credentials/security, employer** — and you can add your own.
Those items stay on your disk while remaining fully yours, backed up, and readable by
your local agents.

Trust is per-credential, not per-product: a local host (an agent on your own machine) may
read everything and relabel any item; a cloud host reads only `shared` items and may
relabel only what it captured itself. Review and change any of it with the
`loreport_view_memory_settings` / `loreport_change_memory_settings` tools, or by asking
your assistant. Full spec: [`docs/visibility-design.md`](docs/visibility-design.md).

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

## Optional: go live with the sync hub

The loop above is manual by design — you paste, you save, you run the janitor. If you
run an always-on agent host, you can *add* the opt-in **Tier-2 sync hub**: it automates
filing captures, consolidation, `INDEX.md` rebuilds, and republishing your surface
across providers. It's a strict superset — a hub outage or never adopting it degrades to
exactly the manual loop, never to broken. See [`hub/HUB.md`](hub/HUB.md) to set it up.

## Checking in on it

Two things you can ask any connected assistant:

- **`loreport_status`** — the version you're running *and* whether the packet each
  provider reads is still current with `main`. That second half is the point: a version
  string alone stays green while a provider quietly reads a stale copy of your memories.
- **`loreport_whats_changed`** — what happened lately, in both halves: software changes,
  and per-provider activity. It lists every configured provider even at zero, because a
  connector that has silently stopped working looks exactly like one you haven't used.

And if you run the hub, a **browsable HTML report** of the whole brain — dashboard,
search, every entry with a privacy badge — rebuilt daily and served on your own private
network. See [`hub/HUB.md`](hub/HUB.md).

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

Honest notes on prior art and how Loreport differs — including where the alternatives are
genuinely better — are in [`docs/comparison.md`](docs/comparison.md).

## Credits & license

The index-first, one-file-per-item, wikilinked shape is validated by prior art: Andrej
Karpathy's ["LLM Wiki" gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
independently converged on the same pattern, and this project adopts and credits it.

Licensed under **MIT** (`prompts/`, `hub/` Python code) and **CC BY 4.0** (`docs/`,
`brain-template/`, `examples/`). See [`LICENSE`](LICENSE) for the full text.

## Repo map

| Path | What it is |
|---|---|
| [`docs/setup.md`](docs/setup.md) | **The walkthrough** — create, load, add assistants, maintain. |
| [`prompts/`](prompts/) | The three pasteables: `onboard.md`, `bootstrap.md`, `consolidate.md`. |
| [`scripts/init-brain.sh`](scripts/init-brain.sh) | **Start here** — creates a new brain: folder, git repo, private GitHub backup. |
| [`brain-template/`](brain-template/) | The skeleton it copies, if you'd rather do it by hand. |
| [`examples/`](examples/) | A filled fixture brain with a worked consolidation example. |
| [`hub/`](hub/) | The opt-in Tier-2 sync hub. |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each release, written to be read. |
| [`docs/format-spec.md`](docs/format-spec.md) | Canonical item / index / skill schema. |
| [`docs/visibility-design.md`](docs/visibility-design.md) | `shared` vs `local`, trust tiers, enforcement. |
| [`docs/security.md`](docs/security.md) | Threat model and controls, in full. |
| [`docs/architecture-decisions.md`](docs/architecture-decisions.md) | Why it's built this way — each with what should reopen it. |
| [`docs/comparison.md`](docs/comparison.md) | Prior art and alternatives, honestly. |
