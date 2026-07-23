# Load Paths

*How to load the brain into any LLM host. v1, 2026-07-23.*

The brain has one **operating surface**: bootstrap protocol + `PROFILE.md` + `INDEX.md`. That
is what always travels with you — fixed-size, always in context. Detail files (`memories/`,
`knowledge/`, `skills/`) are lazy: fetched when needed, never bulk-uploaded.

Which loading recipe to follow depends on what the host can do, not which product it is.
`docs/providers.md` maps products to tiers.

---

## Tier 1 — Paste

**Host capability:** Chat box only; no files, no persistent retrieval.

**Setup:**
1. Open `prompts/bootstrap.md`, copy its full text.
2. Paste it — followed by a blank line, then your `PROFILE.md` contents, then your `INDEX.md`
   contents — into the instructions field (Gem instructions, system-prompt box, custom
   instructions, or as the very first message in a fresh chat).
3. Keep your brain folder open locally. When the model says *"Please paste [[name]]"*,
   open the named file and paste it into the chat.

**Why:** The operating surface is small enough to fit in any instructions field. Detail files
stay off-context until the model needs them — one paste per item, on demand.

---

## Tier 2 — Filesystem

**Host capability:** Agent reads and writes local files (Claude Code, Codex, Cursor,
Gemini CLI, OpenCode, and any CLI-class host).

**Setup:**
1. Point the agent at your brain folder — either add the brain folder to the project root
   or set it as the working directory.
2. In the agent's standing-instructions file (`CLAUDE.md`, `AGENTS.md`, or equivalent),
   either paste the operating surface inline or add an `@include` / read directive pointing
   to `prompts/bootstrap.md`, `PROFILE.md`, and `INDEX.md`.
3. The model fetches detail files itself (`read memories/name.md`). On filesystem hosts
   it can also apply emit blocks directly — no manual paste required.

**Why:** The agent already has filesystem access; it doesn't need you to paste items one
by one. The growth loop is nearly automatic.

---

## Tier 3 — Projects / Retrieval

**Host capability:** Persistent workspace with uploaded knowledge and a custom-instructions
field (Claude Projects, ChatGPT Projects, custom GPTs).

**Setup — PD-11 requirement:**

> **Required:** paste the operating surface (bootstrap + `PROFILE.md` + `INDEX.md`)
> into the **custom-instructions field**. Upload **only detail files** (`memories/*.md`,
> `knowledge/*.md`, `skills/*/SKILL.md`) as project knowledge.
>
> **Never upload `PROFILE.md` or `INDEX.md` as knowledge files.**

**Why this is required:** On retrieval-based hosts, uploaded files may be gated behind
semantic search rather than always-in-context. If `INDEX.md` is uploaded as a file, the
model can only retrieve it when the query happens to surface it — silently breaking the
index-first read order the bootstrap protocol depends on. The catalog *must* be in the
always-in-context custom-instructions field, not in the retrieval pool.

**Detail:**
1. Open `prompts/bootstrap.md`; copy its text.
2. In the custom-instructions field: paste bootstrap text + blank line + PROFILE.md
   contents + blank line + INDEX.md contents.
3. Upload your `memories/*.md` and `knowledge/*.md` files as project knowledge.
   Upload `skills/*/SKILL.md` (and `meta.yaml`) as project knowledge.
4. When the model references an item by `[[name]]`, the retrieval system should surface
   it; if it doesn't, paste the file into the chat.

---

## Apply / Rollback Ritual (for consolidation)

`prompts/consolidate.md` produces a **change plan** — a set of `<MEMORY>` blocks and
deletions that you apply by hand. Because the plan is destructive (merges and deletions
permanently lose text), always snapshot first.

### Step 1 — Snapshot

Choose the variant that fits how you store your brain:

- **Git (recommended):** `git add -A && git commit -m "pre-consolidation snapshot $(date +%Y-%m-%d)"`
- **Folder copy:** duplicate your brain folder: `cp -r brain/ brain-backup-$(date +%Y-%m-%d)/`

### Step 2 — Apply

Work through the change plan top to bottom:

1. **Secret rotations first** — any "rotate this secret" instruction is an external action;
   do it before touching files.
2. **Merges and fixes** — for each `<MEMORY action="update">` block, save the block's
   content to the file path shown in `file=`.
3. **Deletions** — remove the named files and delete their INDEX lines.
4. **Replace INDEX.md** — paste the plan's rebuilt `INDEX.md` wholesale, replacing the old file.

### Step 3 — Rollback

If anything looks wrong after applying:

- **Git:** `git checkout HEAD~1` (or `git reset --hard <commit-hash>`)
- **Folder copy:** delete the modified folder; rename `brain-backup-<date>/` back to `brain/`

Then re-run consolidation on the restored snapshot.
