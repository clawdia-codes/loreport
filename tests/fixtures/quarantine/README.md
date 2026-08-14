# Quarantine replay corpus

Eight capture blocks, one per artifact that a real brain quarantined between
2026-08-04 and 2026-08-07. They exist so the failure shapes that were only ever
described in prose — in a digest that lives in a private repo — are replayed
mechanically instead.

**These are structure-exact reproductions, not copies.** Each preserves every
byte that the parser reacts to: the `<MEMORY …>` tag and which attributes it
carries, the presence, absence and position of the frontmatter `---`
delimiters, which frontmatter keys appear and in what order, the presence and
shape of the trailing `INDEX:` line, the missing final newline, and the exact
file size for the two empty ones. What is replaced is the prose: item names,
descriptions and bodies are generic. This repo is public and holds nothing from
any particular person's brain (see `tests/test_no_personal_identifiers.py`);
the originals stay in the private archive they came from.

| fixture | shape | expected |
|---|---|---|
| `2026-08-04-mpb-capture-gqkezjtb.txt` | bare `<MEMORY>`, frontmatter with NO delimiter at all (blank-line terminated), no `INDEX:` | committed |
| `2026-08-04-mpb-capture-4pa3swhb.txt` | bare `<MEMORY>`, frontmatter missing its opening `---`, no `INDEX:` | committed |
| `2026-08-05-mpb-capture-j98m523j.txt` | as above | committed |
| `2026-08-06-mpb-capture-mc4tz2cn.txt` | as above | committed |
| `2026-08-07-mpb-capture-9x7u7u37.txt` | well-formed; quarantined by the git-error fixed in 1.14.0 | committed |
| `2026-08-07-mpb-capture-n6rp9k4c.txt` | well-formed `action="update"`; same git-error | committed |
| `2026-08-07-mpb-capture-kfcadatl.txt` | 0 bytes | quarantined `empty-block` |
| `2026-08-07-mpb-capture-rit_ofwc.txt` | 0 bytes | quarantined `empty-block` |

`verify_against_originals.py` beside them is an operator tool, not a test: given
the private archive these came from, it compares each reproduction's *skeleton*
— tag, delimiter positions, frontmatter key order, `INDEX:` presence, final
newline, byte size for the empty ones — against the original, and ignores prose.
Run it whenever the corpus is touched and the archive is at hand:

    python3 tests/fixtures/quarantine/verify_against_originals.py \
        --verify /path/to/unpacked/archive

It last reported `8 OK, 0 DIFF` on 2026-08-11, when the corpus was written. The
originals themselves were also replayed through the fixed CLI that day, in a
throwaway brain: the six recoverable ones committed with readable frontmatter,
the two empty ones quarantined as `empty-block`.

The two 0-byte files are the ones that had no explanation for three days. They
are 0 bytes because `quarantine()` copies its input verbatim, so an empty input
produces an empty artifact — the caller sent nothing. Keeping them in the corpus
at exactly 0 bytes is the point; a fixture with content would not reproduce it.
