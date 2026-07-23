# Verification — Model-Proof Brain v1

Verified 2026-07-23 (repo `main`). This records what has been checked and exactly what a
maintainer still needs to run against live provider accounts / a running Tier-2 hub.

**Method note.** The Tier-1 behavioral checks below were run as **Sonnet bare-chat
proxies** — a fresh model instance told to follow each prompt as if pasted into a tool-less
chat — plus mechanical measurement. They prove the prompts reliably elicit the required
behavior; they are **not** a substitute for running the flow in real Claude / ChatGPT,
which is the final confirmation (see "Pending live verification").

## Structural checks (repo-only) — PASS
| Check | What it verifies | Result |
|---|---|---|
| S-1 | each prompt self-contained (works pasted alone) | ✅ |
| S-2 | embedded spec-slices byte-identical to `format-spec.md` Appendix A | ✅ (re-verified after the bootstrap trim) |
| S-3 | `examples/` fixture integrity + exact INDEX | ✅ |
| S-4 | example skill degrades gracefully from `SKILL.md` prose alone | ✅ |
| S-5 | hub branch-merge determinism | ✅ |
| S-6 | hub inbox-ingest gate (valid commits; secret + imperative quarantined) | ✅ |
| S-7 | hub snapshot publish (clean passes; poisoned packet blocked) | ✅ |

## Tier-1 behavioral checks — PASS (bare-chat proxies)
| Check | What it verifies | Result |
|---|---|---|
| #1 | cold-start onboarding → valid PROFILE (4 headings) + 3–7 seeds + INDEX (3 fixed headings) | ✅ 3/3 |
| #2 | import: drop secrets, neutralize injected imperatives, confirm-before-emit | ✅ |
| #3 | load-and-answer grounded in a stored item (not a guess) | ✅ *(single-provider proxy — cross-provider pending)* |
| #4a/b/c | live emit · end-of-session sweep · **injection resistance** | ✅ 3/3 |
| #5 | consolidation: snapshot-first · dup merge · secret mask + rotate-first · INDEX N−1 · skill untouched · totals | ✅ 6/6 |
| #6a | token-frugality (paste): pinned floor = bootstrap+PROFILE+INDEX only, grows 1 line/item | ✅ (mechanical) |

Pinned floor measured: bootstrap 808 + PROFILE 164 + INDEX 147 = **1,119 tok** for the
6-item example brain; detail bodies never pinned.

## Pending live verification (needs real accounts / a running hub)
Run these against your actual Claude + ChatGPT + openclaw; record pass/fail here.

- **#3 cross-provider.** Load the same `examples/brain/` into real Claude (Project:
  PROFILE+INDEX in custom-instructions per `docs/load-paths.md`, detail files as knowledge)
  *and* ChatGPT (paste PROFILE+INDEX). Ask both one question answerable only from one
  memory (e.g. the Godot export-template question). **PASS** iff both answer from the brain.
- **#6b Projects retrieval (the one real unknown).** In a Claude Project, pin PROFILE+INDEX
  in custom-instructions (PD-11) and upload ≥~15 detail files to force retrieval mode. Ask
  a question answerable from one detail file. **PASS** iff the model (a) still demonstrably
  holds the pinned INDEX (e.g. resolves a `[[wikilink]]` it wasn't just shown) **and** (b)
  answers from the right file. This is what PD-11 exists to guarantee.
- **Tier-2 alignment A-1..A-3** (needs `hub/` running + accounts wired; see `hub/HUB.md`).
  - **A-1 propagation:** write a memory on one provider's branch (via the inbox), run the
    hub merge + publish, confirm it appears in another provider's loaded snapshot.
  - **A-2 concurrent writes:** write different memories on two provider branches, merge,
    confirm both survive (no loss).
  - **A-3 scrub-before-publish:** plant a secret in an inbox capture; confirm the hub scrubs
    it before it reaches any republished snapshot.

## Known soft notes (non-blocking, future polish)
- `bootstrap.md` is 808 tokens vs the design's internal ≤700 estimate. The estimate was
  wrong; the repo asserts no budget. Further cuts would touch the S-4 security rules — not
  worth it. Documented, not a defect.
- `format-spec.md`'s `type: project` grammar mandates `**Why:**/**How to apply:**`, which
  reads awkwardly on pure project-*status* facts (vs feedback/corrections). Consider making
  those fields optional for `project`.
