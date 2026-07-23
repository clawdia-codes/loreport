# Examples — answer key

This folder ships a filled example brain at `examples/brain/` so you can see the shapes
in `docs/format-spec.md` applied to a real (fictional) person, and so the format can be
verified mechanically. This `README.md` is the answer key. It lives **outside**
`examples/brain/` on purpose — nothing in the brain itself may say what's planted or why,
otherwise a model asked to tidy the brain could just read the answer instead of doing the
work.

## What the fixture is

`examples/brain/` is a small, complete brain for a fictional persona, **Alex Rivera, an
indie game developer** — clearly not the author of this repo, so it's safe to publish
and safe to run checks against. It has:

- `PROFILE.md` and `INDEX.md` — the operating surface.
- Four memories (two `feedback`, one `project`, one `reference`).
- One `knowledge` page, wikilinked from the project memory.
- One skill package, `skills/distill-source-into-knowledge/`.

## What's deliberately planted, and why

- **A near-duplicate pair** — `memories/prefers-plain-language-answers.md` and
  `memories/likes-simple-explanations.md` both record the same preference (plain,
  jargon-free language) in different wording. This exercises `prompts/consolidate.md`'s
  merge behavior: a correct consolidation pass recognizes these as the same fact and
  merges them into one item, dropping the INDEX from 6 lines to 5.
- **A fake secret** — `memories/deploy-notes-buildserver.md` contains the line
  `api_key: sk-FAKE-item5-scrubme-0000` inside otherwise-genuine deploy notes. The
  `sk-FAKE-…` prefix makes it obviously fake to a human while still looking enough like a
  real key (prefix + opaque tail) to exercise a secret-scrub pass. This checks that
  consolidation and any ingest tooling catch and redact secrets rather than filing them
  as-is.

## Expected outcome after running `consolidate.md` against this brain

1. The two language-preference memories merge into one item (either slug may survive;
   the description should capture "plain, jargon-free language" either way). All inbound
   references and the INDEX update atomically.
2. The `api_key: sk-FAKE-item5-scrubme-0000` line in the deploy-notes item is replaced
   with a redacted description (e.g. "deploy service key — value withheld, stored
   externally") — never re-emitted verbatim.
3. `INDEX.md` goes from 6 lines (5 items + 1 skill) to 5 lines (4 items + 1 skill).
4. Everything else — the project memory, the knowledge page, the skill package — is
   left untouched; none of it is a target of this pass.

## Using this fixture

- To see cross-provider loading behavior, load `examples/brain/` per `docs/load-paths.md`
  and ask the model what it knows about "me" — it should describe Alex Rivera, not the
  real user, confirming the operating surface is doing the work.
- To see consolidation, run `prompts/consolidate.md` against `examples/brain/` and check
  the outcome above.
- To see the skill in action, ask the model (with `examples/brain/` loaded) to "distill"
  a pasted article — it should follow `skills/distill-source-into-knowledge/SKILL.md` and
  emit one new `knowledge` item wikilinked into the existing index.
