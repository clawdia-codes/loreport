# Loreport — Architecture Decision Record (ADR)

A running log of the *significant, potentially-reversible* architecture choices behind
Loreport, and the reasoning at the time we made them.

**How to read this file.** Each entry is **a decision we took at a specific time, under
specific constraints** — not a claim of eternal truth. The "Revisit when" clause on each is
the load-bearing part: it names the conditions under which the decision should be
re-opened. If those conditions arrive, the strongest architecture may well be different.
Record the reasoning, not a dogma.

---

## ADR-001 — Wikilinks, not a knowledge graph

**Date:** 2026-07-24 · **Status:** Accepted (point-in-time)

**Decision.** Represent relationships between brain items as inline bare-slug
`[[wikilinks]]` in prose. Do **not** build a knowledge-graph layer (nodes/edges, a
triple-store, a graph DB, or a query engine). People and organizations are `reference`
pages reached by wikilink, not graph nodes.

**Rationale (true as of the decision date):**
1. **No runtime to traverse it.** A KG earns its keep only when something *queries* it —
   multi-hop traversal like "all decisions affecting projects involving person X." That
   needs a graph engine + query layer at read time. Loreport's reader is an LLM loading
   markdown into a context window; it has no such runtime. Wikilinks give that reader what
   it can actually use — "here's a related item, fetch it if relevant" — one-hop, lazy,
   zero infrastructure.
2. **Token-frugality is the hard constraint.** The design guarantees a fixed pinned floor
   (`PROFILE.md` + `INDEX.md`) that grows one line per item. A graph's edge set is another
   structure; loaded into context it costs tokens proportional to edge count — the exact
   "cost proportional to brain size" the design forbids.
3. **Portability + graceful degradation.** The brain must load into ChatGPT / Claude /
   Gemini / Kimi by paste or upload. `[[wikilink]]` is universally legible and degrades to
   a harmless slug in prose. A graph needs a per-provider query interface most chat UIs
   can't call; without its engine the graph is inert JSON. Wikilinks degrade; a KG doesn't.
4. **Maintenance cost.** A graph demands edge integrity on every edit (dangling-edge
   detection, an edge-type schema, referential consistency) that rots fast under hand/LLM
   maintenance. Wikilinks are text; a lint pass flags dangling links and the consolidation
   janitor keeps them clean, with none of a graph DB's bookkeeping.
5. **Independent validation.** Karpathy's "LLM Wiki" converged on the same stance —
   one-file-per-item, flat index read first then drilled into, wikilinks, and explicitly
   *no embedding/RAG/graph infra* at moderate scale.
6. **The value gap is small at this scale.** A KG's edge over wikilinks is *automated
   multi-hop traversal*. At personal-brain scale (dozens–low-hundreds of items), the LLM
   reading the INDEX and following one or two wikilinks already does that traversal
   in-context, with judgment a static graph can't apply.

**Revisit when** BOTH hold: (a) Loreport grows a real Tier-1 runtime that can *execute*
queries (a CLI/MCP retrieval layer), **and** (b) the brain reaches a scale (thousands of
items) where multi-hop retrieval demonstrably beats an index scan. At that point a
lightweight graph — or embeddings — may become worth its complexity. Until both are true,
a KG is pure cost. **See ADR-004 for the concrete, measurable trigger and the rung-by-rung
path from here to a graph.**

---

## ADR-002 — Substring search over the INDEX, not embeddings/RAG

**Date:** 2026-07-24 · **Status:** Accepted (point-in-time)

**Decision.** `loreport_search_memories` does a **case-insensitive substring scan over
`INDEX.md`'s one-line hooks** on `main`, returning matching catalog lines (trust-filtered
so a cloud caller never sees `local` items); the model then calls `read_memory` on the hits
for full bodies. No embeddings, no vector store, no semantic retrieval.

**Rationale.** Same family of reasons as ADR-001: embeddings need a runtime to compute and
a store to query — infrastructure Tier-0/Tier-1 doesn't have — and they'd break portability
(the retrieval couldn't move across providers) and transparency (retrieval becomes opaque).
Substring-over-INDEX is dumber (no synonym recall: "car" won't find "vehicle") but is
simple, portable, transparent, and preserves the shared/local privacy wall. Mitigations:
descriptive INDEX hooks; the model can issue multiple queries or `load_context` and scan the
whole INDEX with its own judgment (often better than embedding recall at this scale).

**Context-cost note (for the record):** via the MCP connector, Loreport does **not** double
a provider's context usage. The only always-present cost is the fixed MCP tool schemas
(independent of brain size); item content enters context only when a tool is called.
Native provider memory (e.g. ChatGPT's) is a *separate* always-injected store — running both
is additive; how the two coexist is governed by ADR-003.

**Revisit when** the same conditions as ADR-001 (a Tier-1 runtime + a scale where lexical
recall measurably fails users). Embeddings are a v2-runtime option, deferred not rejected.
**See ADR-004 for the concrete trigger and the rung-by-rung path — embeddings are Rung 2,
reached only after Rung 1 (SQLite FTS5) proves insufficient.**

---

## ADR-003 — Coexist with native memory via capture parity, not by disabling it

**Date:** 2026-07-24 · **Status:** Accepted (point-in-time) · *Supersedes the earlier
"disable native memory" recommendation (fable review #24, Phase E).*

**Decision.** The recommended default is **parity mode**: the provider's native memory
stays ON, and a standing prose rule (the `capture-parity` skill) instructs the assistant
to (a) **mirror** every native-memory save into Loreport at the same moment, and
(b) **recall with a preference order** — native memory primary for facts the assistant
captured itself, Loreport primary for facts from other assistants and anything native
lacks. Loreport serves everything to everyone (no echo *filtering*); search hits are
annotated with their capture `source:` so an assistant can recognize its own echoes.
Drift is repaired by the repeatable `memory-reconcile` skill: dump native → diff against
Loreport → match = skip, missing = add, changed = **update the existing item** (never a
duplicate). Disabling native memory remains a documented alternative ("single-store
mode"), no longer the default.

**Rationale (true as of the decision date):**
1. **No cloud provider exposes a native-memory read/write API** (ChatGPT, claude.ai,
   Gemini — all sealed). True store-to-store synchronization is impossible; the only
   levers are prose instructions to the model and the shared store itself. Parity mode is
   the strongest sync available under that constraint.
2. **Native auto-capture is more reliable than a mirrored tool call.** The provider's
   own memory reflex is first-class and always-on; an MCP save is something the model
   must *choose* to do. Disabling native memory therefore *lowers* total capture
   reliability; keeping it and piggybacking on its "worth remembering" judgment raises it.
3. **Divergence is managed, not prevented** — mirror (live) + sweep (end-of-session) +
   reconcile (on demand) form three nested backstops. Best-effort at each layer is
   acceptable because the layers compose.
4. **Suppression was rejected deliberately:** filtering an assistant's own items out of
   Loreport reads would make native memory load-bearing — if the provider wipes or
   rewrites it (native stores are lossy), the assistant couldn't recover its own facts
   from the brain, breaking Loreport's founding promise of surviving provider-store loss.
   Preference-with-provenance keeps the benefit (less echo noise) without the wall.

**Revisit when** any provider ships a real native-memory read/write API (then true sync
becomes possible and the mirror rule can become mechanical), or if parity-mode drift in
practice proves too large for the sweep + reconcile backstops to contain (then
single-store mode returns as the default recommendation).

---

## ADR-004 — Retrieval graduation ladder (when and how to add a graph or embeddings)

**Date:** 2026-07-26 · **Status:** Accepted (point-in-time)

**Decision.** Do **not** build graph or embedding retrieval now. Record instead a concrete,
measurable trigger for *when* to graduate, and a cheapest-first, three-rung path for *how* —
so ADR-001 and ADR-002's "revisit when… thousands of items" becomes an actionable plan
rather than an open worry. This entry is the shared detail those two clauses point to.

**Why not now (measured, not asserted).** At the decision date the brain is 46 entries and
`INDEX.md` is **2,406 tokens for 50 entries** (~48 tok/entry). The flat index stays cheap
well into the low thousands (≈24K tokens at 500 entries, ≈96K at 2,000). At this scale the
LLM reading the *whole* flat index **is** the semantic search — and it beats embeddings,
which are a lossy approximation of the synonym/intent understanding the model already
applies natively. Embeddings only start to win once the index no longer fits comfortably in
the always-loaded context; that crossover is the low thousands of entries, ~40× away.

Empirical backstop: two older Karpathy-style wikis on the same machine had the richer
taxonomy (`concepts/entities/syntheses`) and it sat near-empty (1/1/1 against 109 raw
sources) while the automated ingest kept dumping unread snapshots — structure that needs
hand-curation decays. This is ADR-001 confirmed on disk.

**The trigger (BOTH required — a count alone is not enough):**
1. `INDEX.md` in the always-loaded surface crosses **~30–50K tokens** (≈ 1–2k entries at
   today's ~48 tok/entry), **and**
2. a **demonstrated** retrieval failure — real queries where substring-over-INDEX misses
   relevant entries. Baseline for the comparison: 2,406 tokens / 50 entries.

A large index with search that still works is not a trigger; a shown failure is.

**The rungs (cheapest first — take them in order, stop as soon as retrieval is good enough):**

- **Rung 0 — derived link-graph (free, available any time).** Parse the `[[wikilinks]]`
  already authored in prose (~109 today) into an adjacency at index-build time. The one
  genuinely graph-shaped feature that does **not** decay, because the edges already exist in
  the text — no new authoring, no schema to maintain. Keeps paste-anywhere portability.
  Enables "what's related to X" and a `related` view/tool. Not built by this entry; recorded
  as the low-cost first move whenever it's wanted.
- **Rung 1 — SQLite FTS5 ranked search (hub-only).** `sqlite3` is Python stdlib and FTS5 is
  compiled in (verified). Ships as a file in the repo, gives BM25 ranking instead of
  substring. Tier-1 paste still uses the flat index; only the connector gets better ranking.
  This is the **first** graduation step — reach here before considering embeddings.
- **Rung 2 — embeddings + vector search (connector-only).** Needs a model + vector store, so
  it **breaks the paste-anywhere invariant** and splits retrieval quality by tier (cloud
  callers get grep, local get semantic). This is the true endpoint of ADR-001/002's
  "revisit," taken only if Rung 1 proves insufficient at real scale, and only with the
  cloud-vs-local inconsistency accepted explicitly.

**The lever that matters before any of this: density, not structure.** At personal-brain
scale the scalability win is **distillation** — turning raw memories into denser
`knowledge/` synthesis pages (currently 0) — not taxonomy or graph. The wikis prove
structure-without-density is just more folders to grep. Distillation needs no
infrastructure and is the right move now; the ladder above is for later.

**Revisit when** the Rung-1 trigger above is met, or if a provider ships a retrieval
runtime that changes the portability calculus (e.g. native vector search a pasted brain
could call), which would move Rung 2's cost down.
