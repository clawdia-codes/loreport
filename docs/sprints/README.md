# notes/ — working documents, not brain items

This directory holds sprint plans, journals and post-mortems. It is **not** part of the
brain's item set and is deliberately invisible to every piece of Loreport machinery:

- `hub/brain_merge.py` claims only `ITEM_PROVENANCE_DIRS = ("memories/", "knowledge/")`
  and skips any path outside them — so nothing here is merged, reverted or indexed.
- `make-surface.sh` builds `surface.md` from `PROFILE.md` + `INDEX.md` only.
- `hub/snapshot_publish.py` builds the cloud packet from those same files.

Nothing here reaches a cloud provider, and nothing here needs frontmatter.

## Why it lives in this repo

These documents used to sit in `~/projects/loreport-sprint*`, which were not git repos and
were not backed up. They are here because this repo is private, versioned, and already
covered by `~/.config/restic/personal-include.txt`.

They must **not** go in `~/projects/loreport` — that repo is public. They must also not sit
inside any tree MAESTRO runs against: its snapshot/restore deletes foreign files under
`projectRoot`, which is how `.sprint/PLAN.md` and `JOURNAL.md` were lost once already
(see `sprints/loreport-sprint/MAESTRO_POSTMORTEM.md` §8).

## Convention

One directory per sprint, copied in **after the sprint finishes**. Do not mirror a live
sprint's working directory here — it forks the state and the copy goes stale mid-run.
