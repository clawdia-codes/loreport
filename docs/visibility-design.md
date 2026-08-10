# Visibility tiering — design spec

Addresses REVIEW.md finding #3 (no sensitivity tiering). Lets a single brain hold both
memories that sync to every provider and memories that never leave the machine, with the
choice baked into onboarding and adjustable via an MCP call.

## 1. The field

Every item gains one frontmatter field:

```yaml
visibility: shared | local
```

- **`shared`** — participates in the published packet and is readable by all
  connected providers over MCP. Global across providers.
- **`local`** — never leaves the machine: excluded from the published packet, and cloud
  provider reads refuse it. Only local/trusted hosts (openclaw, Claude Code) can read it.

The field is **required** on every item. An item is `shared` only on an explicit
`visibility: shared`; the field absent, a value that doesn't parse, or no frontmatter block
at all all read as `local` (`_visibility_from_text`, five copies across `hub/`, plus the
equivalent rule in `hub/project.py` and `make-surface.sh`).

**Changed 2026-08 — this used to say "absent ⇒ `shared`, back-compatible with existing
brains".** Back-compatibility was the wrong thing to optimise: it made an unclassified item
not merely unfiltered but positively *published*, the one fail-open default in an otherwise
fail-closed engine, and three batches leaked that way on 2026-08-07. Now an unclassified
item is withheld, and `hub/snapshot_publish.py` refuses to publish at all while any exists
(§6) so the withholding cannot quietly accumulate.

Skills are exempt: a skill is a package, not an item, and carries no `visibility:` field at
all (`docs/format-spec.md` §1). Skills are always shared. The carve-out lives in the
resolvers that know a path is a skill, never in the parser, which only ever sees text.

## 2. Trust tiers (independent of provider)

Reads/writes are gated by a **trust tier** carried by the credential, not by provider name
(because "claude" spans both Claude Code-local and claude.ai-web-cloud):

| Credential | provider | trust |
|---|---|---|
| openclaw hub token | openclaw | **local** |
| Claude Code filesystem token | claude | **local** |
| ChatGPT connector token | chatgpt | cloud |
| claude.ai web connector token | claude | cloud |

Config lives in `hub/config/providers.json` (also fixes REVIEW #22 — provider set was
hardcoded across three scripts): `{name, token_env, trust, merge_order}` per provider.

## 3. Enforcement (mechanical, fail-closed)

- **`snapshot_publish.py`** — the published packet + its INDEX include only `shared` items.
  `local` items never appear in anything that a provider can pull.
- **`loreport_read_memory` / `loreport_search_memories` / `loreport_load_context`** — become trust-aware:
  - trust=local caller → sees everything.
  - trust=cloud caller → `local` items are refused on read, omitted from search results,
    and absent from the surfaced packet. (INDEX on-disk still lists them for local hosts.)
- **`brain_merge.py`** — visibility is a normal, preserved field; merge doesn't leak because
  egress is the publish/read layer, which already filters.

## 4. Changing visibility — MCP tools

**Naming:** all tools use the `loreport_<verb>_<noun>` scheme (renamed from the internal
`brain_*`): `loreport_save_memory`, `loreport_read_memory`, `loreport_search_memories`,
`loreport_load_context` (the pinned packet), plus the two settings tools below.

- **`loreport_view_memory_settings()`** → lists every item with its current setting. A cloud
  caller sees `shared` items normally and `local` items as existence-only (name,
  "(local — hidden)"), never the body.
- **`loreport_change_memory_settings(name, visibility)`** → flips one item. Gating:
  - trust=local caller → may change **any** item.
  - trust=cloud caller → may change **only items it authored** (`source:` == its provider);
    everything else is refused. So a cloud provider can share/unshare its own memory but
    can neither read nor promote a `local` item it doesn't own (no exfiltration path).
  - The frontmatter field is the source of truth, so hand-editing a file works identically.

## 5. Onboarding (explicit global-vs-local)

`onboard.md` gains a sensitivity step that:
1. Explains in one line: **shared = every provider sees it; local = this machine only.**
2. Asks which topics are always-local (defaults offered: health, finances, relationships,
   credentials/security, employer). These become a small always-local rule set.
3. During **import** (pulling in each provider's existing memory), classifies every item
   shared/local — matching an always-local topic ⇒ `local`, else `shared` — and shows the
   user the split for **explicit confirmation before saving** (this is the "manifest," now
   produced by the interview instead of by hand).

`bootstrap.md` capture grammar: the model must state `visibility:` on every capture, and
should choose `local` for a capture that obviously touches an always-local topic.

## 6. Default & migration

- Every capture states `visibility:` explicitly. A capture that omits it is not rejected —
  it is filed and read as `local`, so nothing is lost and nothing leaks.
- An item with no `visibility:` reads as `local` everywhere: publish, MCP read/search,
  and the projected surfaces. It is withheld, not deleted, and a local-trust caller still
  sees it.
- **The withholding is reported, not silent.** `hub/snapshot_publish.py` enumerates
  `memories/`/`knowledge/` on `main` before building anything and refuses the whole
  republish while any item lacks the field, naming each one to stdout and to
  `hub/quarantine/digest.md`. `bin/loreport-sync` already treats a nonzero
  `snapshot_publish` as "publish failed" and raises an alert, so the gate reuses a signal
  path that is known to reach a human. The last good `packet.md` stays on disk meanwhile,
  so providers keep working from a slightly stale index rather than losing the brain.
- A pre-existing brain therefore migrates by classification, not by trust: the first
  publish after upgrading names every unclassified item, and a one-shot
  `loreport_change_memory_settings` pass (or hand-edit) clears the list.

## 7. Verification gates (objective, non-gameable)

1. A `local` item is **unreadable** by a cloud-trust caller via read/search/surface, and
   **absent** from the published packet — but **readable** by a local-trust caller. (proves §3)
2. A cloud caller's `loreport_change_memory_settings` on an item it does **not** own is refused; on its
   **own** item succeeds. (proves §4 gating)
3. A shared item still round-trips end-to-end (capture→merge→publish→cloud read). (no regression)
4. Onboarding run produces items carrying correct `visibility` per the always-local rules,
   with the confirmation step present. (proves §5)
