# Loreport — Engine / Brain split

Design spec for the boundary between **this repo (the engine)** and **a user's brain repo**.
Complements `setup.md` (how a user installs) and `architecture-decisions.md`.

**The rule, one line:** the engine holds everything generic; a brain holds only that user's
memories, their state, and that instance's config. Nothing generic in a brain; nothing personal
in the engine.

**The test for any file:** *would another user's brain repo have this file too?*
Yes → engine. Only true of one user's → brain.

**Status (2026-08-07):** designed, not implemented. Written from a survey of a real long-running
brain instance (~76 items, 199 commits, ~2 weeks of nightly syncs), which is where the evidence
below comes from.

---

## Two problems, one boundary

### 1. The engine ships no orchestration

`hub/` contains every part of the pipeline — `inbox_ingest.py`, `brain_merge.py`,
`snapshot_publish.py`, `project.py` — and nothing that runs them in order. In the surveyed
instance the driver was a `sync.sh` living **in the user's private brain repo**, with absolute
paths and a personal notifier hardcoded.

Consequences:

- Cloning this repo gives you the instruments and no score. There is no supported way to run a
  daily cycle end-to-end.
- Every user must reinvent the ordering, the failure handling, and the push step — and get the
  merge/publish/project sequence right by reading source.
- The single most operationally important asset in the system is the one asset the project does
  not ship.

**Decision:** the engine ships the conductor as `bin/loreport-sync`, reading a small config file
that lives in the brain. Everything instance-specific — brain path, engine path, notifier
command, host-bridge targets — is config, not code.

### 2. Brains accumulate drifting engine copies

The surveyed instance's `sync.sh` copied engine files *into the brain's tracked tree* on every
run: `doctor.sh`, `make-surface.sh`, and the three `prompts/*.md`. All five were byte-identical
to their engine originals.

This buys no independence — the heavy engine (`brain_merge.py`, `project.py`,
`inbox_ingest.py`) was never copied, only invoked from the engine checkout. So the brain was
already engine-dependent; the copying produced a second, drifting copy of five files and
nothing else.

It also actively caused harm, in two compounding ways:

- **Silent drift.** The copy was taken from the engine's *working tree*, so a brain received
  whatever branch happened to be checked out when the timer fired. The surveyed brain was
  running a `doctor.sh` from an unreleased feature branch, 22 lines diverged from the release.
- **Silent halt.** The sync refused to proceed on a dirty tree — correct as a safeguard, but
  the copying is itself a source of dirtiness, and the refusal was quiet. Six commits sat
  unpushed for two days before anyone noticed.

**Decision:** the engine is never copied into a brain. A brain records **which engine version
produced its current state** (`ENGINE-VERSION`) and invokes the engine in place. Updating the
engine is one `git pull` in one repo.

**Rejected alternatives.** *Git submodule:* correct and auditable, but detached HEADs and
`--recurse-submodules` are a persistent trip hazard for humans and coding agents alike; a
version pin conveys the same fact without the failure modes. *Keep copies but gitignore them:*
removes the dirty-tree halt with minimal change, but makes drift invisible and unreviewable —
strictly worse than not copying. *Fork/upstream-merge:* engine and brain have unrelated
histories; grafting them is heavy for no gain.

---

## Where skills belong

`format-spec.md` makes `skills/` a first-class **brain** content type, alongside `memories/`
and `knowledge/`. That stays true. But it does not follow that every skill sitting in a brain
belongs to that brain.

Split by **what the skill is about**, not where it currently lives:

| Skill is about | Home |
|---|---|
| operating Loreport itself | engine — one canonical copy, shipped to every user |
| the user's own domain, work, or life | that user's brain, always |

The surveyed instance held three skills — mirroring native memory into Loreport, operating the
brain, and reconciling native memory against it. All three were purely Loreport-operating with
no personal content, i.e. engine material that had accumulated brain-side.

**One canonical copy, not one per side.** An earlier draft had the engine ship its *own* copies
as template seed content while brains kept theirs. That recreates precisely the drifting-
duplicate problem this document exists to remove.

A brain keeps its `skills/` directory regardless — it is the home for personal skills, whether
or not it currently holds any.

### Delivery: bridge them, don't just catalogue them

A skill listed in `INDEX.md` is reachable only if the assistant *chooses* to fetch it. A skill
installed into the host's own skills directory is always in front of the model. These are not
equivalent, and the difference decides whether a skill ever runs.

In the surveyed instance all three skills were catalogued, but only one was bridged to the
host. The two that were not — both concerned with keeping native memory and Loreport in step —
had most likely never executed.

**Decision:** `loreport-sync` bridges every engine-owned skill to the configured host targets.
The acceptance test is that the skill **resolves in a fresh session**, not that the file exists
on disk.

---

## A brain must be portable from its own repo

The paste-surface (`make-surface.sh` output) embeds the full capture grammar and is genuinely
self-sufficient — it is the artifact that makes a brain usable in any assistant with no install.

In the surveyed instance it was **gitignored**. Cloning the brain elsewhere therefore yielded
the memories, no paste-surface, and no generator — because the generator is engine code the
brain no longer carries.

That defeats the project's central promise.

**Decision:** a brain tracks its `scope: shared` paste-surface as a committed artifact, and the
sync regenerates and commits it each run. This is also what lets `prompts/` leave a brain
cleanly: the grammar travels *baked into the surface*, as data, rather than as a copied file.

Only the `shared`-scope surface is tracked. It withholds every `visibility: local` item by
construction, so a brain repo — private or not — never gains exposure it did not already have.
Implementations should verify that property at commit time rather than trusting it.

---

## Target layout

```
loreport/                        (this repo — public, generic)
  bin/loreport-sync              the conductor; reads --config
  hub/*.py                       the pipeline
  prompts/                       canonical capture grammar
  skills/                        Loreport-operating skills, one copy
  brain-template/                what `setup.md` seeds a new brain from
  docs/

<user>-brain/                    (private, per user)
  memories/                      the memories
  knowledge/
  skills/                        personal skills only (may be empty)
  PROFILE.md
  INDEX.md                       derived catalog
  hub/surface-<host>.md          tracked paste-surface — portability
  hub/projection-targets.json    where THIS brain projects
  hub/published/packet.md        current published surface
  loreport.conf                  paths, notifier, bridge targets
  ENGINE-VERSION                 which engine produced this state
```

A brain holds no scripts, no prompts, and no dated derivative history. Git already stores every
past state; a second dated copy of it is redundant.

---

## Migration, for an existing instance

Each step is independently revertible. Do them one at a time, verifying between.

1. Add `loreport.conf` to the brain. Nothing consumes it yet.
2. Generalize the instance's `sync.sh` into `bin/loreport-sync` here, reading `--config`.
   **Parameterize before it lands** — an instance's driver contains absolute paths and personal
   hooks, and this repo is public.
3. Make the halt alarm unconditional: any refusal to proceed notifies. Test by deliberately
   dirtying the tree and confirming the alert *arrives*.
4. Cut the timer over to `loreport-sync --config`; delete the brain's copy.
5. Remove the propagate block; delete the copied scripts and `prompts/` from the brain; add
   `ENGINE-VERSION`.
6. Track the shared paste-surface; have the sync regenerate and commit it.
7. Move any engine-development notes out of the brain.
8. Untrack dated derivative artifacts — `git rm --cached` **and** a `.gitignore` entry, or the
   sync's `git add -A` restores them on the next run.
9. Move Loreport-operating skills here, parameterized, and bridge all of them.

**Verification gate after every step:** a full sync run completes *and* the health check reports
no failures. Both conditions, every time.

After step 6, prove portability rather than assuming it: clone the brain to a scratch directory
and confirm the paste-surface is present and usable **with no engine checkout**.

---

## Why this matters beyond tidiness

Every failure described above was silent. A copy drifted quietly; a sync halted quietly; a skill
was catalogued but never delivered; a paste-surface was ignored by git. None of them raised an
error, and each was found only by someone going to look.

The boundary removes the *causes*. It does not remove silence — that is what step 3 is for. Both
halves are needed, and once the boundary is drawn the alarm is cheap enough that there is no
reason to choose between them.
