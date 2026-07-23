# Verification — Loreport v1

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

Pinned floor measured: bootstrap 891 + PROFILE 164 + INDEX 147 = **1,202 tok** for the
6-item example brain (bootstrap now includes the provenance grammar + `Host` line); detail
bodies never pinned.

**Provenance (added 2026-07-23).** Each capture stamps `source:` (from the pinned `Host`) +
`captured:` (date). Verified via bare-chat probe: Host=`chatgpt` → `source: chatgpt` + correct
date, block well-formed, injection resistance intact. Slice change kept S-2 byte-identical.
See `format-spec.md` §1.

## Tier-2 hub — verified live (local instance, 2026-07-23)
Stood up a real hub over a separate brain repo (`~/projects/mpb-live-brain`: `main` +
`provider/{openclaw,claude,chatgpt}`) and exercised the full loop with the actual
`hub/*.py` tools:

| Check | What it verifies | Result |
|---|---|---|
| A-1 propagation | memory captured on `provider/chatgpt` → merge → publish → reaches `main`, the `provider/claude` branch, and the published packet; **`source: chatgpt` provenance preserved through the merge** | ✅ |
| A-2 concurrent + collision | distinct memories on two branches both land; a same-name collision is renamed `…-2` and retagged — **no loss** | ✅ |
| A-3 scrub (3 independent layers) | secret stopped at the **inbox gate**, the **publish egress scrub**, AND the **merge-time gate** (forced onto a branch → merge aborts, `main` untouched) | ✅ |
| MCP bridge | `mcp_server.py` stdio: initialize / tools/list / brain_surface / brain_capture — capture via MCP lands on the right branch with provenance | ✅ |
| S-5 / S-7 | INDEX byte-determinism · publish scrub self-tests | ✅ |

Not covered by the local instance: the real per-provider **connector wiring** (Claude MCP /
ChatGPT tunnel pointed at the endpoint) — integration config, not hub logic.

## Pending live verification (needs real accounts)
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
- **Tier-2 alignment A-1..A-3** — ✅ VERIFIED LIVE against a local hub instance (see the
  "Tier-2 hub" section above). What remains for your accounts is only the *connector wiring*
  (pointing real Claude/ChatGPT at the MCP endpoint), not the hub logic.

## Known soft notes (non-blocking, future polish)
- `bootstrap.md` is 891 tokens (808 before the `source`/`captured` provenance grammar +
  `Host` line). The design's internal ≤700 estimate was wrong; the repo asserts no budget.
  Further cuts would touch the S-4 security rules — not worth it. Documented, not a defect.
- `format-spec.md`'s `type: project` grammar mandates `**Why:**/**How to apply:**`, which
  reads awkwardly on pure project-*status* facts (vs feedback/corrections). Consider making
  those fields optional for `project`.
- **`inbox_ingest.py` imperative-scan is aggressive.** During A-2 a legitimately
  imperative-phrased preference ("Always dark mode.") was quarantined as an injection
  attempt. It's held safely (not lost), but consider softening — e.g. only quarantine
  imperatives in *pasted third-party* content, or require an attribution cue — so a user's
  own first-person preferences aren't false-positived.
