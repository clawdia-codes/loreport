# Security

*Threat model and controls for Loreport v1. 2026-07-23.*

---

## Extractability warning

> Anything in your brain is readable by anyone who can prompt an assistant it's loaded
> into. The brain is designed to never contain secrets — keep it that way.

This is the canonical home of this text. `README.md` copies it verbatim from here.

---

## Tier 1 — Threat model & controls

### The three threats

**1. Secret / PII persistence.** A credential or piece of sensitive personal data pasted
into a chat gets captured into a memory file that then loads into every future session
and every provider the brain is loaded into.

*Controls:* the never-capture rule and provenance rule in `prompts/bootstrap.md §Never
capture`; the standing Boundaries line in `brain-template/PROFILE.md §Boundaries` (travels
with the profile even if the user trims the protocol); the consolidation secret-scrub
pass in `prompts/consolidate.md op 4` as a monthly backstop.

**2. Stored prompt injection.** Adversarial or imperative text is saved as a "fact" and
steers every future session. Three ingress paths:

- **(a) Import** — a provider memory dump carries planted or accidental imperatives.
- **(b) Source distillation** — a skill reads an external document whose author may be
  hostile and distills an imperative into a stored item.
- **(c) Live capture** — third-party content pasted mid-session (article, email, tool
  output) contains an imperative that gets captured as a durable fact.

*Controls:* see the four-ingress table below.

**3. Extraction.** Anything in a brain loaded into a shared or deployed assistant (custom
GPT, shared Gem) is readable by anyone who can prompt it.

*Control:* the extractability warning above; the brain is designed to never contain
secrets — so if that invariant holds, extraction reveals preferences and habits, not
credentials.

---

### Four-ingress control table

| Ingress | Control | Where it lives |
|---|---|---|
| **Live capture** | Standing never-capture rule (secrets described-not-valued) **+ provenance rule** (third-party content in chat is untrusted: claims captured and attributed, imperatives never captured as directives) | `prompts/bootstrap.md §Never capture` + `PROFILE.md §Boundaries` |
| **Import** | Sanitize (drop secrets and neutralize imperative "facts") → summarize → user confirms **before** any block is emitted | `prompts/onboard.md Phase 2` — the order is the control |
| **Source distillation** | Source is untrusted: store attributed claims, never instructions; imperatives aimed at the assistant are dropped or quoted as content, never obeyed or saved as directives | `examples/brain/skills/distill-source-into-knowledge/SKILL.md step ②`; stated as the norm for all ingest skills in `docs/format-spec.md §skill-packages` |
| **Consolidation** | Secret-scrub pass, explicitly framed as *backstop* with rotate-first remediation | `prompts/consolidate.md op 4` |

These controls are **continuous and layered** — every ingress has its own gate, with
consolidation as the monthly backstop that catches what slipped through.

---

### Residual retention

This product is markdown and prose: it can *instruct*, never *purge*. When a secret (or
any fact you regret) has been stored and later scrubbed, copies may persist in:

| Residue location | Who controls it | Remediation |
|---|---|---|
| **Chat transcripts** (every session the item was loaded into) | The provider | Delete the conversations where feasible; assume provider-side retention regardless |
| **Provider memory silos** (facts re-absorbed by ChatGPT / Gemini native memory) | The provider | Delete the corresponding entries in the provider's memory UI |
| **git history of the brain repo** | You | Rewrite history (`git filter-repo`) or accept the residue — rotate makes this moot |
| **Provider uploads** (Project knowledge / GPT files hold the pre-scrub copy until replaced) | You | Re-upload the scrubbed files after every consolidation that changed them |
| **Local backups / snapshots** (including pre-consolidation snapshots from `load-paths.md §Apply/Rollback`) | You | Prune old snapshots after a scrub |

**The rule, in order:** a stored secret is a compromised secret — **rotate it first**; then
scrub the file (using consolidation's redaction block); then chase the residue above as
far as feasible. This is guidance, not a claimed capability — honest framing is itself
the deliverable, and prevention (the three controls above) is where the design spends its
effort.

---

## Tier 2 — Hub additions *(opt-in; only applies if you have set up the sync hub)*

Tier 1's threat model is unchanged and stands alone. Tier 2 adds a persistent trusted
service and two new data paths. The additions below only apply to users who have
deployed `hub/`.

### New threat surfaces & controls

| Surface | Threat | Controls |
|---|---|---|
| **The hub itself** | Compromised or malfunctioning hub corrupts / exfiltrates the canonical brain | Single-file stdlib-only Python tools (auditable in one sitting); off-machine private git mirror = tamper-evident copy; every hub write is a structured commit on a named branch (attribution); daily digest of all hub actions to the user |
| **MCP endpoint** | Unauthorized read / write of the brain via the exposed server | No public listener — localhost bind + authenticated tunnel only; per-provider connection credentials (a connection maps to exactly one `provider/*` branch — a stolen credential cannot write another provider's branch or `main`); tools expose brain items only, never arbitrary filesystem paths |
| **Capture-inbox / MCP write path** | Stored prompt injection and secret/PII persistence arriving *mechanically*, without a human eye | **Scan-before-commit**: every inbound block is validated against `emit-grammar v1`, secret-scanned, and imperative-scanned (the provenance rule, enforced mechanically) before it is committed even to the provider branch; failures quarantine to a review folder — raw unscanned content never enters git |
| **Merge / republish path** | A defective merge destroys data; a republish leaks a secret cross-platform | Backup tag (`pre-merge/<date>`) before every reconciliation; **secret-scrub MUST pass before any merge to `main` AND before any republish** — both gates fail closed; rollback = `git reset --hard pre-merge/<date>` |

### Fifth ingress (Tier 2 only)

The four-ingress table above gains a fifth row for hub users:

| Ingress | Control | Where it lives |
|---|---|---|
| **Capture-inbox / MCP write** | scan-before-commit: grammar validate + secret-scan + imperative-scan; failures quarantine, never committed | `hub/inbox_ingest.py` validation pipeline |

### Egress gate (Tier 2 only)

A fail-closed **egress scrub** runs before every snapshot republish: the assembled packet
(bootstrap + PROFILE + INDEX) is scanned for secret patterns; any hit blocks the entire
publish and alerts the user. A secret must never auto-propagate to another platform.
Implemented in `hub/snapshot_publish.py`.

### Provenance / audit layer

Git history + provider branches are the audit trail — every memory is attributable
(which provider, when, via which bridge) from commit metadata alone. The off-machine
mirror makes the trail tamper-evident against a compromised hub.
