# Loreport v1.1 — Taxonomy & Lifecycle

Design spec for the "Taxonomy & Lifecycle" sprint. Extends `format-spec.md` (v1). Origin:
ChatGPT's "Migrating ChatGPT Memory into Loreport" proposal (2026-07-24), filtered against
Loreport's frictionless-capture / token-frugal / no-knowledge-graph constraints.

**Ethos guard (load-bearing):** capture stays a *single auto-classified emit block*. The
model infers `type` and lifecycle; the user never fills a form. Every addition below inherits
`visibility` tiering — a `decision` or entity page about Coach/Portia/finance, or one naming a
real person, defaults `local` and never reaches a cloud provider.

**Status (2026-08-04, S2):** item 1's enum change has *shipped ahead of this spec* — S2
(`design-wiki-parity.md` §4) added BOTH `decision` and `person` to `ITEM_TYPES` in
`inbox_ingest.py` / `brain_merge.py`, to the prompt grammars, and to `project.py`'s
truncation ranking. So item 1 below is done and its "concrete landmine" is closed; read
"the ONLY new type this sprint" as historical. Item 2 (`lifespan`) and everything after
it remain unbuilt.

---

## Schema delta (the whole change, precisely)

1. **`type` enum gains `decision`.** New value:
   `type: user | feedback | project | reference | knowledge | decision`
   Decisions live in `memories/`. This is the ONLY new type this sprint.
   **Concrete landmine (confirmed):** `inbox_ingest.py:63` holds
   `ITEM_TYPES = {"user","feedback","project","reference","knowledge"}` and rejects anything
   else (line ~204). `decision` MUST be added there or every decision capture is quarantined.
2. **New optional field `lifespan`.**
   `lifespan: permanent | active | temporary` — absent = `permanent`.
   - `permanent` — identity, long-term goals, durable knowledge, architecture, decisions.
   - `active` — current projects, ongoing experiments, present responsibilities.
   - `temporary` — travel, reminders, transient context that should expire.
3. **New optional field `expires`.** `expires: <YYYY-MM-DD>` — the *mechanical* archival
   trigger for `temporary` items (see Phase 2). Absent on `permanent`/`active`.
4. **Body conventions (prose, not enforced fields):**
   - **Decisions** carry: `**Decision:**` / `**Rationale:**` / `**Alternatives:**` /
     `**Consequences:**`, and optionally `**Supersedes:** [[old-decision]]`. Date = existing
     `captured`.
   - **Entities** (People/Organizations) are `type: reference` pages, naming convention
     `person-<slug>` / `org-<slug>`, reachable by `[[wikilinks]]`. Body: relationship, role,
     linked projects, notable interactions. **No graph engine — wikilinks only.**
5. **New files / conventions:**
   - `INDEX-ARCHIVE.md` — the cold shelf (was a deferred design note in `format-spec.md`
     "Archive"). Now implemented, scoped to lifecycle expiry.
   - `knowledge/timeline.md` — a single milestone log (`type: knowledge`), NOT a per-item type.

Only `reference`/`knowledge`/`project` *semantics* are unchanged; `type` enum and two
optional fields (`lifespan`, `expires`) are the only frontmatter additions.

**Why `decision` earns a type but entities don't (the asymmetry, justified):** the
discriminator is *catalog-level scanability*. Decisions are a category you **scan for**
("what did we decide about X?"), so they earn an INDEX marker `(decision)` that makes them
visible in the always-loaded catalog. Entities are **lookup targets reached by wikilink**
from other items — you rarely scan "list all people" — so they need no catalog visibility
and stay a `reference` convention. If that ever flips (you start scanning for entities),
promote them the same way.

---

## Phase 1 — Decisions

The highest-value idea in the proposal: a decision record is durable where the discussion
that produced it is transient.

- Add `decision` to the enum in `format-spec.md` §1, `emit-grammar v1`, `rules-compact v1`
  (Appendix A) — and the byte-identical embedded copies in `bootstrap.md`, `onboard.md`,
  `consolidate.md`. Slice-sync discipline: extracted ranges must `diff` to zero.
- INDEX display: `- [[name]] — hook  (decision)` under `## Memories`.
- Decision body template (prose lead-ins, mirrors the `**Why:**/**How to apply:**` pattern):
  ```
  **Decision:** <what was decided>
  **Rationale:** <why>
  **Alternatives:** <what else was considered, why rejected>
  **Consequences:** <what this commits us to / tradeoffs>
  ```
- `bootstrap.md` capture heuristic: *"When a choice is made **with a stated reason**, emit a
  `type: decision`. Revisiting a past decision → `action="update"` the same item and add
  `**Supersedes:**` if it replaces a different decision."*
- **Gate:** a mid-chat decision emits a well-formed `decision` block with the four lead-ins;
  INDEX marker is `(decision)`; a Coach/finance decision defaults `local`.

## Phase 2 — Lifecycle

- Add `lifespan` + `expires` to §1, emit-grammar, rules-compact (+ embedded copies). Default
  absent = `permanent`; capture stamps `active`/`temporary` only when clearly warranted, and
  stamps `expires: <date>` on a `temporary` item whenever a natural expiry is knowable
  (a travel date, a deadline).
- **Mechanical archival trigger (fix — not model-guessed):** the primary trigger is the
  `expires` date. `consolidate.md` is handed **today's date** (the session already knows it)
  and archives any item whose `expires` is **before today** — a simple before/after
  comparison, not duration math. `temporary` items *without* `expires` fall back to a
  model-judged "past its useful window" review. `active` items that have gone quiet get a
  re-confirm/downgrade nudge; `permanent` is untouched. (If the before/after check ever
  proves unreliable in practice, a tiny hub lint that lists expired candidates is the
  deferred follow-up — no new code ships for it now.)
- **Cold shelf (`INDEX-ARCHIVE.md`):** consolidation moves an archived item's INDEX line into
  `INDEX-ARCHIVE.md`; the file body stays on disk, only the catalog line leaves the hot
  `INDEX.md`. On a **filesystem host** the model fetches `INDEX-ARCHIVE.md` on demand.
- **Cloud-packet seam (fix — the sharp one):** cloud providers get the published *packet*,
  not the files, and cannot lazy-fetch. So archival is a **hot/cold split of the local
  `INDEX.md` only** — `snapshot_publish.py` MUST include archived-**shared** items
  (`INDEX.md` + `INDEX-ARCHIVE.md`, `local` still excluded) in the packet, so a `shared`
  item is **never silently lost** from the cloud view by being archived. The only thing ever
  excluded from the packet stays `local` items — invariant unchanged. Net: leaner hot index
  for filesystem hosts; full shared catalog still reaches the cloud.
- **Gate:** a `temporary` item with a past `expires` gets archived (line moves to
  `INDEX-ARCHIVE.md`, body preserved); `permanent` items never touched; local `INDEX.md`
  shrinks; a `shared` archived item still appears in the published packet (cloud can still
  read it); a `local` archived item still does not.

## Phase 3 — Entities (People / Organizations)

- No new type. `docs/format-spec.md` + `onboard.md` document the `person-*` / `org-*`
  `reference`-page convention and its wikilink usage.
- **Visibility default:** an entity page naming a real colleague/customer/family member is
  personal data → defaults `local`. Public orgs the user has no private relationship with may
  be `shared`. Bootstrap/onboard state this.
- **Gate:** a `person-*` page is a valid `reference` item, linkable via `[[person-…]]`, and a
  real-individual page defaults `local` (cloud-invisible).

## Phase 4 — Migration recipe + timeline

- `onboard.md` import section gains the **phased extraction recipe**:
  1. Identity → `PROFILE.md`. 2. Projects → `type: project`. 3. **Historical decisions** →
  `type: decision`, extracting the durable decision and *ignoring the surrounding discussion*.
  4. Repeated explanations → `type: knowledge`. 5. Milestones → `knowledge/timeline.md`.
- Reinforce the existing "what NOT to import" filter (greetings, jokes, transient reminders,
  obsolete plans, raw transcripts).
- **Gate:** running the recipe against a pasted ChatGPT-memory dump yields ≥1 `decision` item
  and a `timeline.md`, with casual/transient content correctly dropped.

## Phase 5 — Guardrails + verification

- `consolidate.md`: enforce the reject-list (drop casual/transient/obsolete on consolidation).
- Document, in `taxonomy-lifecycle-design.md` "Deferred" below, *why* confidence-scores and
  conflict-coexistence are out (they fight dedup; revisit only with a real runtime).
- **Full re-verification on the LIVE brain** the live brain repo:
  `brain_merge.py` stays **exit-0**; local items stay cloud-invisible; INDEX rebuilds; the
  daily backup still pushes. No regression to the shipped visibility enforcement.

---

## Deferred (explicit YAGNI cuts)

- **Knowledge-graph infrastructure** — rejected (Karpathy-validated). Wikilinks give the
  linking value without a runtime we don't have.
- **`Ideas` / `Working Context` as first-class types** — fold ideas into `memories/` (or a
  single backlog doc); `Working Context` is expressed by `lifespan: temporary`, not a type.
- **Confidence scores + conflicting-copies-coexist** — fights consolidation's dedup and needs
  a reasoning runtime to resolve; revisit in a Tier-1 runtime, not now.

---

## Cross-cutting constraints (unchanged, must hold)

- Slice-sync: every embedded `spec-slice` stays byte-identical to Appendix A.
- Secret-scrub scoping unchanged (shared/PROFILE/skills fail-closed; local warn-only).
- `visibility` enforcement unchanged; new types/fields inherit it.
- Token-frugality: pinned floor stays `PROFILE.md` + `INDEX.md`; `lifespan` archival *reduces*
  the hot index over time.
