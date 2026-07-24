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
