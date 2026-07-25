# Setup — the whole walkthrough

Everything you need to get a brain running and keep it running, in order. You should not
need to open another file to finish; the links here are for going deeper, not for
completing a step.

**Start where you are:**

- **No brain yet** → Step 1.
- **Brain exists, adding an assistant to it** → skip to Step 3.
- **Brain exists, just want it loaded in a new chat** → Step 2.

---

## Step 1 — Create the brain

Open [`prompts/onboard.md`](../prompts/onboard.md), copy the whole file, and paste it into
any LLM chat. It interviews you, imports what your existing assistants already know about
you, and hands back ready-to-file blocks.

**Fastest path — let the script do it:**

```
./scripts/init-brain.sh
```

It asks for a name and a location, copies the skeleton, initialises the git repo, and
offers to create a **private** GitHub backup. It reads the privacy setting back from the
GitHub API and refuses to upload anything unless GitHub confirms the repo is private — a
public brain is the one mistake it will not let you make. Add `--with-hub` if you already
know you want the Tier-2 sync hub; `--no-github` to stay entirely local. Don't want
GitHub at all? It prints instructions for any git remote you own, including a bare repo
on another machine.

Either way, you end up with this, and onboarding fills it:

```
brain/
├── PROFILE.md         # who you are — always loaded
├── INDEX.md           # the catalog, one line per item — always loaded
├── memories/          # one fact per file
├── knowledge/         # one topic per file
├── skills/            # prose skill packages
└── make-surface.sh    # builds the one file you paste (Step 2)
```

**Prefer to do it by hand?** Copy [`brain-template/`](../brain-template/) whole — it's the
same skeleton the script uses, with `make-surface.sh` and the protocol already inside, so
it works standalone. Then `git init`, and add a `.gitignore` containing `surface.md`
(it's generated and can contain private items). Keep the repo private.

**Sensitive topics.** Onboarding asks which topics stay on your machine, offering these
five as defaults you can add to or remove from: **health, finances, relationships, credentials/security, employer**.
Matching items get `visibility: local` and never reach a cloud provider; everything else is
`shared`. You can change any item's visibility later — see
[`visibility-design.md`](visibility-design.md).

---

## Step 2 — Load it into an assistant

### First: build the surface file

The brain travels as one small **operating surface** — the protocol, plus `PROFILE.md`,
plus `INDEX.md`. Detail files stay on disk until something needs them, which is what keeps
the per-session cost flat as the brain grows.

Assemble it into a single file so you only ever copy one thing:

```
./make-surface.sh --host "ChatGPT"    # shared items only — safe to paste anywhere
./make-surface.sh --all               # include local items — local hosts only, never a cloud chat
```

`--host` names the assistant you're about to paste into. The protocol carries a `Host:`
blank that stamps every capture's `source:`, so another assistant can later tell where a
memory came from; leave it out and captures get filed as `source: ____`. The script warns
you when it's unfilled.

The default withholds every `visibility: local` item from the catalog, and tells you how
many it dropped. That default is the safe one on purpose: pasting is how the brain reaches
hosts that can't read your files, which in practice means cloud chat boxes. Reach for
`--all` only when the host is on your own machine.

Re-run it whenever `PROFILE.md` or `INDEX.md` changes. If you run the [sync
hub](../hub/HUB.md), it publishes the same shared-only surface automatically to
`hub/published/packet.md` — use that instead.

### Then: pick the recipe that matches what your host can do

| Your host can… | Recipe |
|---|---|
| Only chat — no files, no uploads | **Paste** |
| Read and write local files | **Filesystem** |
| Hold a persistent workspace with uploaded knowledge | **Projects** |

**Paste** — put `surface.md` into the instructions field (Gem instructions, custom
instructions, system prompt) or send it as the first message of a fresh chat. Keep your
brain folder open; when the assistant says *"Please paste [[name]]"*, open that file and
paste it.

**Filesystem** — point the agent at the brain folder, then reference `surface.md` from its
standing-instructions file (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`) — inline it or use an
include directive. The agent fetches detail files itself and can apply capture blocks
directly, so the growth loop is nearly automatic.

**Projects** — paste `surface.md` into the **custom-instructions field**. Upload *only*
detail files (`memories/*.md`, `knowledge/*.md`, `skills/*/SKILL.md`) as project knowledge.

> **Never upload `PROFILE.md` or `INDEX.md` as knowledge files.** Uploaded files sit behind
> semantic retrieval, so an uploaded catalog is only found when a query happens to surface
> it — which silently breaks the index-first read order everything else depends on. The
> catalog must be in the always-in-context instructions field.

### Which recipe for which product

| Product | Recipe | Notes |
|---|---|---|
| **ChatGPT** (Projects or custom GPT) | Projects | Its Memory dump and both Custom Instructions boxes are import *sources* during Step 1. |
| **ChatGPT** (no Project) | Paste | Surface goes in Settings → Personalization → Custom Instructions. |
| **Claude Projects** (claude.ai) | Projects | Retrieval behaviour is undocumented and variable; the instructions-field rule above never depends on it. |
| **Claude Code** | Filesystem | Include the surface in `CLAUDE.md`. |
| **Gemini** (web, Gems) | Paste | Surface goes in Gem instructions. Gemini's "Saved info" imports *into* Google's silo — Step 1 extracts *out* of it. |
| **Kimi** | Paste | File upload works for detail items; it converts docs to agent skills natively, which suits `skills/`. |
| **Codex · Cursor · Gemini CLI · OpenCode · openclaw** | Filesystem | Point at the brain folder; surface goes in the agent's standing instructions. |
| **Any other CLI-class host** | Filesystem | The default for anything with file access. |

Provider capabilities change — treat this table as current best knowledge, not a guarantee.
Adding a product is one row; the three recipes cover every case.

---

## Step 3 — Add an assistant to an existing brain

Two steps, not another interview.

### 3a. Pin the standing instruction

Paste this into the assistant's persistent instructions field. It is what makes the rule
survive into every future chat:

<!-- spec-slice: standing-instruction v1 — verbatim copy; canonical text: docs/setup.md Step 3a -->
```
Loreport is my portable memory brain, reachable via the loreport_* tools.
- Whenever you save something to your own memory, ALSO save it to Loreport
  (loreport_save_memory). Same judgment, same moment.
- Prefer your native memory for facts you captured yourself; prefer Loreport for
  facts from other assistants and anything you don't already hold.
- When I say "reconcile my memories", fetch the [[memory-reconcile]] skill
  (loreport_read_memory) and follow it.
- When I say "sweep", check for anything durable from this chat not yet saved.
```
<!-- /spec-slice -->

| Platform | Where it goes | What it can see |
|---|---|---|
| openclaw | `~/.openclaw/workspace/AGENTS.md` | everything; may relabel any item |
| Claude Code | `~/.claude/CLAUDE.md`, or per-project | everything; may relabel any item |
| ChatGPT | Project instructions, or Settings → Personalization → Custom Instructions | **`shared` only**; may relabel only its own items |
| claude.ai | Project instructions, or Settings → Profile preferences | **`shared` only**; same |
| Gemini | Gem instructions | paste recipe — no connector |

**No connector?** The `loreport_*` tools come from the [sync hub](../hub/HUB.md). Without
it the same instruction still works — drop the two tool names and the assistant hands you
`<MEMORY>` blocks to file by hand instead of writing them itself. Nothing else changes.

### 3b. Reconcile

Say **"reconcile my memories"**. The assistant dumps its own native memory, diffs it
against the brain, and repairs the difference: match = skip, missing = add, changed =
**update the existing item** (never a duplicate). It is re-runnable by design — a clean
second run finds nothing, which is how you know the loop converged.

**Do platforms in this order: openclaw → Claude Code → ChatGPT → Gemini.** Local hosts
first: they read the brain directly, so any problem shows up where it's cheapest to debug,
before a cloud host and its trust wall are involved.

| Platform | How to run it | Check it worked |
|---|---|---|
| **openclaw** | Say *"reconcile my memories"* — the `loreport-ops` skill is installed in `~/.openclaw/workspace/skills/`. It reads the brain from disk. | An item it added appears in `INDEX.md`. |
| **Claude Code** | `/loreport reconcile`. Its native memory is a real file store (`~/.claude/projects/*/memory/`), so this is the one platform where the diff is exact rather than a model-recited dump. | `/loreport status` shows the new count. |
| **ChatGPT** | **Connector must be live first** — check the MCP tunnel service is running, or reconcile silently has nothing to diff against. Then paste the standing instruction into Project instructions and say *"reconcile my memories"*. | It proposes adds/updates, and the wall holds (below). |
| **Gemini / no connector** | Paste `surface.md` (or `hub/published/packet.md`) into the instructions field. Captures come back as `<MEMORY>` blocks you file by hand. | The blocks are well-formed and you can save them. |

**Verify the wall on the first cloud host.** Ask ChatGPT something only a `local` memory
could answer. It should come up empty. That is the privacy guarantee working, not a bug.

**What "done" looks like across the round:**

- Every platform carries the standing instruction.
- Every platform has completed one reconcile, and a **second** reconcile there reports
  ~all matches and no new adds — proof the loop converges instead of duplicating.
- `local` items remain invisible to every cloud host.
- A fact captured in one assistant is readable in another. That is the whole point.
- If you run the hub: its sync completes clean and the backup is in sync.

### Native memory: two supported modes

- **Parity mode (recommended).** Leave native memory on. The standing instruction above
  plus the `capture-parity` skill mirror every native save into the brain, and recall goes
  native-first for the assistant's own facts, brain-first for everything else. You keep
  native auto-capture; the brain stays the cross-provider record. Drift is repaired any
  time by reconciling.
- **Single-store mode.** After Step 1 has ported a provider's memory, turn the native
  feature off and let the brain be the only store. Simplest possible model — one store,
  zero divergence — but you lose the provider's auto-capture reflex, which is more
  reliable than a mirrored tool call. Reasonable on providers where you rarely capture
  anything new.

Reasoning behind this choice: ADR-003 in
[`architecture-decisions.md`](architecture-decisions.md).

---

## Step 4 — Keep it healthy

**Every session** the assistant captures durable facts as they come up, and sweeps for
missed ones before you close the tab (say **"sweep"** to force it).

**Monthly**, run the janitor: copy [`prompts/consolidate.md`](../prompts/consolidate.md)
into a chat with the brain loaded. It merges near-duplicates, prunes stale items, repairs
dangling links, and rebuilds `INDEX.md`. It produces a **change plan** you apply by hand —
and because merges and deletions permanently lose text, snapshot first:

```
git add -A && git commit -m "pre-consolidation snapshot"   # or: cp -r brain/ brain-backup/
```

Apply the plan top to bottom:

1. **Secret rotations first** — those are external actions, do them before touching files.
2. **Merges and fixes** — save each `<MEMORY action="update">` block to its `file=` path.
3. **Deletions** — remove the named files and their INDEX lines.
4. **Replace `INDEX.md`** wholesale with the rebuilt one.
5. Re-run `./make-surface.sh`.

If it looks wrong afterwards: `git checkout HEAD~1` (or restore the backup folder) and run
consolidation again on the restored snapshot.

---

## Going further

- **Automate all of it** — the opt-in [sync hub](../hub/HUB.md) files captures, merges
  provider branches, rebuilds the index, and republishes the surface on a timer. It's a
  strict superset: never adopting it, or an outage, degrades to exactly the manual loop
  above.
- **See it worked before you trust it** — [`examples/`](../examples/) ships a filled brain
  with a planted duplicate and a planted fake secret, plus the expected outcome.
- **Reference** — [`format-spec.md`](format-spec.md) (the schema),
  [`visibility-design.md`](visibility-design.md) (`shared` vs `local` and trust tiers),
  [`security.md`](security.md) (threat model), [`architecture-decisions.md`](architecture-decisions.md)
  (why it's built this way, and what should reopen each choice).
