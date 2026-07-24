# Onboarding runbook — bringing every platform onto one brain

For a brain that **already has content** (the common case after the first setup). A fresh
empty brain instead starts with `prompts/onboard.md` pasted into any one assistant.

With an existing brain, "onboarding a platform" is two steps, not an interview:

1. **Pin the standing instruction** — so the host mirrors captures and knows the triggers.
2. **Run `reconcile`** — import what that host holds natively that the brain lacks; update
   what has changed. Re-runnable, dedupes by design.

Do the platforms in this order: **openclaw → Claude Code → ChatGPT → (Gemini/other)**.
Local hosts first: they can read the brain directly, so any problem surfaces where it is
cheapest to debug, before a cloud host with the trust wall is involved.

---

## The standing instruction (same text everywhere)

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

Where it goes:

| Platform | Location | Trust |
|---|---|---|
| openclaw / Clawdia | `~/.openclaw/workspace/AGENTS.md` | local — may read + relabel any item |
| Claude Code | `~/.claude/CLAUDE.md` (or per-project) | local — same |
| ChatGPT | Project instructions, or Settings → Personalization → Custom Instructions | **cloud — never sees `local` items** |
| claude.ai | Project instructions, or Settings → Profile preferences | cloud — same |
| Gemini | Gem instructions | paste tier (no connector) |

---

## Per-platform sequence

### 1. openclaw (local)
The `loreport-ops` skill is installed in `~/.openclaw/workspace/skills/`. Say
*"reconcile my memories"*. openclaw has filesystem access to the brain, so it reads
`main` directly. Verify: an item it adds appears in `INDEX.md`.

### 2. Claude Code (local)
Run `/loreport reconcile`. Its native memory is `~/.claude/projects/*/memory/` — a real
file store, so this is the one platform where the diff can be exact rather than a
model-recited dump. Verify: `/loreport status` shows the new count.

### 3. ChatGPT (cloud)
Connector must be live (the MCP tunnel service). Paste the standing instruction into the
Project instructions, then say *"reconcile my memories"*. ChatGPT dumps its saved Memory,
diffs against what the connector serves, and proposes adds/updates.
**Expect and verify the wall:** it must not see `local` items. Ask it something only a
local memory would answer — it should come up empty. That is the privacy guarantee
working, not a bug.

### 4. Gemini / anything without a connector
Paste `hub/published/packet.md` (bootstrap + PROFILE + shared INDEX) into the
instructions field. Captures come back as `<MEMORY>` blocks to file by hand.

---

## After the round

1. Run the brain's `sync.sh` — merges every provider branch into `main`, republishes the
   packet, propagates prompts + skill bridges, pushes the private backup.
2. `loreport-status.sh` — confirm the item count grew, the shared/local split is right,
   and no provider branch is left unmerged.
3. Spot-check the cross-provider promise: a fact captured in one assistant should be
   readable in another. That is the whole point of the system.

## What "done" looks like

- Every platform carries the standing instruction.
- Every platform has completed one reconcile, and a **second** reconcile there reports
  ~all matches and no new adds (proof the loop converges instead of duplicating).
- `local` items remain invisible to every cloud host.
- `sync.sh` runs clean and the backup is in sync.
