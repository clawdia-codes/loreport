#!/usr/bin/env bash
# Documentation consistency gate. Run from the repo root: ./scripts/check-docs.sh
#
# Several things in this repo are deliberately duplicated so that a pasted file is
# self-contained. Duplication without a checker is just future drift, so this script
# is the checker. It verifies:
#
#   1. Every `spec-slice` block is byte-identical to its canonical source.
#   2. brain-template/prompts/bootstrap.md matches prompts/bootstrap.md (the template
#      ships a copy so a copied skeleton works standalone).
#   3. Every relative markdown link resolves to a real file.
#   4. make-surface.sh actually runs against the example brain and withholds `local`.
#
# Exits nonzero on any failure. No dependencies beyond python3 + bash.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

python3 - <<'PY' || fail=1
import re, pathlib, sys
root = pathlib.Path(".")
bad = []

# --- 1. spec slices ------------------------------------------------------------
SLICE = re.compile(r'<!-- spec-slice: ([a-z-]+ v\d)[^>]*canonical text: ([^ ]+) ([^>]*?) -->\n(.*?)<!-- /spec-slice -->', re.S)
canonical = {}
found = []
for f in root.rglob("*.md"):
    if ".maestro" in str(f) or ".git/" in str(f):
        continue
    for m in SLICE.finditer(f.read_text()):
        found.append((f, m.group(1), m.group(2), m.group(4)))

for f, name, src, body in found:
    src_path = root / src if (root / src).exists() else root / "docs" / src
    if not src_path.exists():
        bad.append(f"slice '{name}' in {f}: canonical source {src} not found")
        continue
    if body.strip() not in src_path.read_text():
        bad.append(f"slice '{name}' in {f} DRIFTED from canonical {src_path}")

if not found:
    bad.append("no spec-slice blocks found at all — the checker is probably broken")

# --- 2. bootstrap copy in the template -----------------------------------------
canon = (root / "prompts/bootstrap.md").read_text()
tmpl = root / "brain-template/prompts/bootstrap.md"
if not tmpl.exists():
    bad.append("brain-template/prompts/bootstrap.md missing — a copied skeleton can't build its surface")
elif canon not in tmpl.read_text():
    bad.append("brain-template/prompts/bootstrap.md DRIFTED from prompts/bootstrap.md")

# --- 2b. skill name agreement --------------------------------------------------
# A host resolves a skill by its DIRECTORY name; the INDEX links it by slug. If the
# directory, SKILL.md's `name:` and meta.yaml's `name:` disagree, the skill silently
# fails to resolve. (The two `description` fields are deliberately different — a
# dispatch trigger vs a one-line catalog hook; see docs/format-spec.md. Not checked.)
import re as _re
for skill_dir in sorted(root.glob("*/*/skills/*/")) + sorted(root.glob("*/skills/*/")):
    if not (skill_dir / "SKILL.md").exists():
        continue
    slug = skill_dir.name
    def _name_of(p):
        if not p.exists():
            return None
        m = _re.search(r'^name:\s*(\S+)', p.read_text(), _re.M)
        return m.group(1) if m else None
    s_name = _name_of(skill_dir / "SKILL.md")
    m_name = _name_of(skill_dir / "meta.yaml")
    if s_name != slug:
        bad.append(f"skill {skill_dir}: SKILL.md name '{s_name}' != directory '{slug}'")
    if (skill_dir / "meta.yaml").exists() and m_name != slug:
        bad.append(f"skill {skill_dir}: meta.yaml name '{m_name}' != directory '{slug}'")

# --- 2c. duplicated security primitives across the single-file hub scripts -------
# Each hub/*.py is deliberately standalone (no cross-imports) so it stays auditable
# in one sitting, which means SECRET_PATTERNS and the visibility parser are copied.
# Extracting them to shared config would fail OPEN if the config went missing, so the
# duplication stays -- but drift between copies must not be silent. brain_merge.py is
# canonical.
def _block(path, start_marker, end_marker):
    t = (root / path).read_text()
    i = t.find(start_marker)
    if i < 0:
        return None
    j = t.find(end_marker, i + len(start_marker))
    return t[i:j] if j > 0 else None

canon_pat = _block("hub/brain_merge.py", "SECRET_PATTERNS = [", "]")
for other in ["hub/inbox_ingest.py", "hub/snapshot_publish.py"]:
    if not (root / other).exists():
        continue
    if _block(other, "SECRET_PATTERNS = [", "]") != canon_pat:
        bad.append(f"{other}: SECRET_PATTERNS DRIFTED from hub/brain_merge.py")

def _func(path, signature):
    """Extract exactly one top-level function body: from its `def` line until the
    first non-blank line back at column 0. Do NOT delimit on the next `def` --
    the following function differs per file, which produces false drift reports."""
    lines = (root / path).read_text().splitlines()
    try:
        i = next(n for n, l in enumerate(lines) if l.startswith(signature))
    except StopIteration:
        return None
    out = [lines[i]]
    for l in lines[i + 1:]:
        if l.strip() and not l[:1].isspace():
            break
        out.append(l)
    return "\n".join(out).rstrip()

SIG = "def _visibility_from_text(text):"
canon_vis = _func("hub/brain_merge.py", SIG)
if canon_vis is None:
    bad.append("hub/brain_merge.py: _visibility_from_text not found (privacy parser missing?)")
else:
    for other in ["hub/mcp_server.py", "hub/snapshot_publish.py", "hub/inbox_ingest.py"]:
        if _func(other, SIG) != canon_vis:
            bad.append(f"{other}: _visibility_from_text DRIFTED from hub/brain_merge.py")

# --- 3. relative links ---------------------------------------------------------
for f in root.rglob("*.md"):
    if ".maestro" in str(f) or ".git/" in str(f):
        continue
    for m in re.finditer(r'\]\(([^)#]+?)\)', f.read_text()):
        link = m.group(1)
        if link.startswith(("http", "mailto:")):
            continue
        if not (f.parent / link).resolve().exists():
            bad.append(f"broken link in {f}: {link}")

for b in bad:
    print("FAIL:", b)
print(f"checked {len(found)} spec slices")
sys.exit(1 if bad else 0)
PY

# --- 4. make-surface behaviour --------------------------------------------------
tmp=$(mktemp -d)
cp -r examples/brain "$tmp/b"
# Copied out of the repo, the fixture can no longer find ../../prompts/bootstrap.md —
# which is exactly the case the BOOTSTRAP override exists for.
export BOOTSTRAP="$PWD/prompts/bootstrap.md"
if ! (cd "$tmp/b" && ./make-surface.sh --host "CheckRunner" >/dev/null 2>&1); then
  echo "FAIL: make-surface.sh errored on examples/brain"; fail=1
elif grep -q 'set it here: `____`' "$tmp/b/surface.md"; then
  echo "FAIL: --host did not fill the protocol's Host blank"; fail=1
fi
# a local item must never appear in a default surface
printf -- '---\nname: checkrunner-local\ndescription: probe\ntype: reference\nvisibility: local\n---\nprobe\n' > "$tmp/b/memories/checkrunner-local.md"
printf -- '- [[checkrunner-local]] — probe  (reference)\n' >> "$tmp/b/INDEX.md"
(cd "$tmp/b" && ./make-surface.sh >/dev/null 2>&1)
if grep -q 'checkrunner-local' "$tmp/b/surface.md"; then
  echo "FAIL: make-surface.sh leaked a visibility:local item into the default surface"; fail=1
fi
rm -rf "$tmp"

if [ "$fail" = "0" ]; then echo "check-docs: PASS"; else echo "check-docs: FAIL"; fi
exit "$fail"
