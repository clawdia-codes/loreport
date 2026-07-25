# Your brain

You copied this skeleton — it's now yours. Three things to know:

**1. Fill it.** The fastest way is to paste [`prompts/onboard.md`](../prompts/onboard.md)
into any LLM chat and let it interview you; it emits blocks you save straight into
`memories/`, `knowledge/`, and `skills/`, and writes your `PROFILE.md` and `INDEX.md`. You
can also just start writing files by hand — it's markdown, nothing is magic.

**2. Load it.** Run `./make-surface.sh` to assemble `surface.md` (the brain protocol +
`PROFILE.md` + `INDEX.md`), then paste that one file into your assistant's instructions
field. Re-run it whenever `PROFILE.md` or `INDEX.md` changes. It withholds every
`visibility: local` item by default, so the file is safe to paste into a cloud assistant;
`--all` includes them, for hosts on your own machine only.

**3. Keep it.** Make this folder a private git repo. That gives you history, backup, and
the sync path if you later turn on the hub.

Everything else — which loading recipe suits your host, adding a second assistant, monthly
upkeep — is in [`docs/setup.md`](../docs/setup.md).

## What each part is for

| Path | Holds |
|---|---|
| `PROFILE.md` | Who you are — identity, goals, preferences, boundaries. Always loaded. |
| `INDEX.md` | The catalog: one line per item. Always loaded. This is what keeps sessions cheap. |
| `memories/` | One fact per file. |
| `knowledge/` | One topic or reference page per file. |
| `skills/` | One package per subdirectory (`SKILL.md` + `meta.yaml` + optional `assets/`). |
| `make-surface.sh` | Assembles `surface.md` — the one file you paste (shared items only unless `--all`). |
| `doctor.sh` | Self-test: layout, git, index integrity, and the privacy wall. Run it any time. |

Item and index formats are specified once in [`docs/format-spec.md`](../docs/format-spec.md).
Mark anything sensitive `visibility: local` in its frontmatter and it never leaves this
machine — see [`docs/visibility-design.md`](../docs/visibility-design.md).
