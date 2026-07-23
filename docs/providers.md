# Provider Map

*Product → capability tier → setup notes. v1, 2026-07-23.*

*Provider capabilities change. Treat Notes as current best-knowledge, not guarantees.
See `docs/load-paths.md` for the full recipe per tier.*

---

## Capability-tier definitions (summary)

| Tier | What the host can do |
|---|---|
| **Paste** | Chat box only; no persistent file access or retrieval |
| **Filesystem** | Agent reads/writes local files directly |
| **Projects** | Persistent workspace: custom-instructions field + uploaded knowledge |

---

## Product → tier mapping

| Product | Tier | Notes |
|---|---|---|
| **Gemini** (web, Gems) | Paste | Surface goes in Gem instructions. Gemini's native "Save info" / "Saved info" imports *into Google's silo* — this recipe extracts *out* into files you own. |
| **Kimi** | Paste (upload-friendly) | File upload works for detail items; Kimi converts docs to agent skills natively — friendly to `skills/` packages. Surface still goes in the system prompt. |
| **Claude Projects** (claude.ai) | Projects | Custom-instructions field = the surface; detail files as project knowledge. Retrieval behavior is undocumented and variable — the PD-11 recipe never depends on it. |
| **ChatGPT Projects / custom GPT** | Projects | Same pattern as Claude Projects. Both the ChatGPT Memory dump and Custom Instructions boxes are import *sources* in `onboard.md`. |
| **ChatGPT** (without Projects) | Paste | Surface as first message or in Custom Instructions (Settings → Personalization). |
| **Claude Code** | Filesystem | Include or copy the surface into `CLAUDE.md`; model reads detail files directly. |
| **Codex** | Filesystem | Include the surface in `AGENTS.md` or project root instructions. |
| **Cursor** | Filesystem | `.cursorrules` or system prompt; model reads files from the workspace. |
| **Gemini CLI** | Filesystem | Point at the brain folder; include surface in the agent's standing instructions. |
| **OpenCode / openclaw** | Filesystem | Native filesystem + git access; model reads and can commit detail files directly. |
| **Any other CLI-class host** | Filesystem | New filesystem-capable hosts default to this tier. |

---

## Notes

**Adding a new product:** add one table row with Product, Tier, and a brief Notes entry.
The three tier recipes in `load-paths.md` cover all cases; no new recipe needed.

**Gemini native import:** Gemini offers a built-in "Import memory" or "Save info" feature
that stores facts in Google's cloud. This is the opposite direction from what this brain
does — use the onboarding prompt (`prompts/onboard.md`) to *extract* what Gemini already
holds and port it into files you control. After porting, the brain runs on Gemini via the
Paste tier recipe above.

**ChatGPT Memory:** ChatGPT's Memory feature stores facts in OpenAI's cloud. `onboard.md`
Phase 2 walks through extracting those facts verbatim, sanitizing them, and converting
them to portable brain items. Once ported, ChatGPT is loaded via the Projects or Paste
tier recipe.
