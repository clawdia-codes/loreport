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

# The companion primitive: _visibility_from_text deliberately cannot tell "absent"
# from "explicitly local", and the three callers that RELAX a control on `local` -- the
# publish gate, the secret-scrub split, and the skills-are-shared carve-out in the egress
# resolvers -- must. Same duplication rule, so same drift check.
SIG2 = "def _has_explicit_visibility(text):"
canon_exp = _func("hub/brain_merge.py", SIG2)
if canon_exp is None:
    bad.append("hub/brain_merge.py: _has_explicit_visibility not found")
else:
    for other in ["hub/snapshot_publish.py", "hub/mcp_server.py"]:
        if _func(other, SIG2) != canon_exp:
            bad.append(f"{other}: _has_explicit_visibility DRIFTED from hub/brain_merge.py")

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
# Run against BOTH copies of the script. examples/brain/make-surface.sh is the one a
# reader sees; brain-template/make-surface.sh is the one scripts/init-brain.sh installs
# into every real brain, and it was the only duplicated file in the repo with no checker
# of any kind — reverting its fail-closed rule left every gate here green. The two are
# byte-identical by intent, so the same fixture and the same assertions apply to each.
export BOOTSTRAP="$PWD/prompts/bootstrap.md"
for surface_src in examples/brain/make-surface.sh brain-template/make-surface.sh; do
tmp=$(mktemp -d)
cp -r examples/brain "$tmp/b"
cp "$surface_src" "$tmp/b/make-surface.sh"
chmod +x "$tmp/b/make-surface.sh"
# Copied out of the repo, the fixture can no longer find ../../prompts/bootstrap.md —
# which is exactly the case the BOOTSTRAP override exists for.
if ! (cd "$tmp/b" && ./make-surface.sh --host "CheckRunner" >/dev/null 2>&1); then
  echo "FAIL: $surface_src errored on examples/brain"; fail=1
elif grep -q 'set it here: `____`' "$tmp/b/surface.md"; then
  echo "FAIL: $surface_src --host did not fill the protocol's Host blank"; fail=1
fi
# a local item must never appear in a default surface
printf -- '---\nname: checkrunner-local\ndescription: probe\ntype: reference\nvisibility: local\n---\nprobe\n' > "$tmp/b/memories/checkrunner-local.md"
printf -- '- [[checkrunner-local]] — probe  (reference)\n' >> "$tmp/b/INDEX.md"
# ...and an UNMARKED item must not either. `visibility:` is required (format-spec.md §1);
# the filter is fail-closed, so an item nobody classified is withheld exactly like a
# `local` one. This case is the whole reason the old exact-line `grep -qi '^visibility:
# local$'` test was replaced: it included anything that did not match that one literal.
printf -- '---\nname: checkrunner-unmarked\ndescription: probe\ntype: reference\n---\nprobe\n' > "$tmp/b/memories/checkrunner-unmarked.md"
printf -- '- [[checkrunner-unmarked]] — probe  (reference)\n' >> "$tmp/b/INDEX.md"
# ...and neither must a SKILL a human marked local. The skills carve-out supplies a
# default for a key skills do not carry; unconditional, it OVERRODE the key and made
# `visibility: local` on a SKILL.md a control that reported success and still pasted the
# skill into a cloud assistant.
mkdir -p "$tmp/b/skills/checkrunner-local-skill"
printf -- '---\nname: checkrunner-local-skill\ndescription: probe\nvisibility: local\n---\nprobe\n' > "$tmp/b/skills/checkrunner-local-skill/SKILL.md"
printf -- '- [[checkrunner-local-skill]] — probe  (skill)\n' >> "$tmp/b/INDEX.md"
(cd "$tmp/b" && ./make-surface.sh >/dev/null 2>&1)
if grep -q 'checkrunner-local' "$tmp/b/surface.md"; then
  echo "FAIL: $surface_src leaked a visibility:local item into the default surface"; fail=1
fi
if grep -q 'checkrunner-unmarked' "$tmp/b/surface.md"; then
  echo "FAIL: $surface_src leaked an UNMARKED item into the default surface"; fail=1
fi
if grep -q 'checkrunner-local-skill' "$tmp/b/surface.md"; then
  echo "FAIL: $surface_src leaked a visibility:local SKILL into the default surface"; fail=1
fi
# The assertions above are satisfied by an EMPTY surface, which the fail-closed
# filter makes a live possibility rather than a theoretical one: get the rule wrong and
# make-surface silently withholds the entire brain instead of leaking it. So assert the
# positive too — a `visibility: shared` item and an UNMARKED skill (no `visibility:`
# field exists for skills) must both still be there.
if ! grep -q 'prefers-plain-language-answers' "$tmp/b/surface.md"; then
  echo "FAIL: $surface_src dropped a visibility:shared item from the default surface"; fail=1
fi
if ! grep -q 'distill-source-into-knowledge' "$tmp/b/surface.md"; then
  echo "FAIL: $surface_src dropped an unmarked skill from the default surface"; fail=1
fi
rm -rf "$tmp"
done

# --- 4b. report_build's visibility parser must AGREE with the canonical one ------
# report_build.py deliberately returns "private" instead of "local" (human wording),
# so byte-identity is the wrong check. Equivalence of the CLASSIFICATION is the thing
# that matters: a disagreement would mislabel an entry's privacy badge and its count.
python3 - <<'PYEQ' || fail=1
import importlib.util, sys
def load(name, path):
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m); return m
canon = load("bm", "hub/brain_merge.py")
rep   = load("rb", "hub/report_build.py")
aud   = load("ba", "hub/brain_audit.py")
CASES = ['visibility: local', 'visibility: "local"', "visibility: 'local'", 'visibility: Local',
         'visibility: LOCAL', 'Visibility: local', 'visibility: local  # note', 'visibility: shared',
         'visibility: nonsense', 'type: project']
bad = []
for line in CASES:
    text = f"---\nname: x\ndescription: d\ntype: project\n{line}\n---\nbody\n"
    a = canon._visibility_from_text(text)
    b = rep.visibility_from_text(text)
    b_norm = "local" if b == "private" else b
    if a != b_norm:
        bad.append(f"  {line!r}: brain_merge={a} report_build={b}")
    # hub/brain_audit.py is the CHECKER. A checker that classifies differently
    # from the publisher is worse than none — it certifies the wrong thing. It
    # must stay on the fail-closed rule and never drift to the whole-line
    # `^visibility:\s*local\s*$` match used by project.py / make-surface.sh /
    # doctor.sh: under that rule `visibility: "local"` reads as SHARED, and the
    # audit would report green on a real leak.
    c = aud.effective_visibility(text)
    if a != c:
        bad.append(f"  {line!r}: brain_merge={a} brain_audit={c}")
for text, label in (('no frontmatter at all', 'bare'), ('---\nname: x\n', 'unterminated')):
    a = canon._visibility_from_text(text); b = rep.visibility_from_text(text)
    b_norm = "local" if b == "private" else b
    if a != b_norm: bad.append(f"  {label}: brain_merge={a} report_build={b}")
    c = aud.effective_visibility(text)
    if a != c: bad.append(f"  {label}: brain_merge={a} brain_audit={c}")
if bad:
    print("FAIL: a visibility parser disagrees with hub/brain_merge.py:")
    print("\n".join(bad)); sys.exit(1)
print("visibility parsers agree across 12 cases (report_build + brain_audit)")
PYEQ

# --- 5. version-bump discipline -------------------------------------------------
# CHANGELOG.md is curated by hand, which means it drifts unless something checks.
# Objective rule, no judgement: if any commit AFTER the one that last touched VERSION
# also touched hub/ or prompts/, then shippable behaviour changed without being
# recorded. Uses committed history only, so the result is deterministic.
version_commit=$(git log -1 --format=%H -- VERSION 2>/dev/null || true)
if [ -z "$version_commit" ]; then
  echo "check-docs: VERSION not committed yet — skipping the bump gate"
elif [ ! -f CHANGELOG.md ]; then
  echo "FAIL: VERSION exists but CHANGELOG.md does not"; fail=1
else
  unrecorded=$(git log --format=%h "$version_commit"..HEAD -- hub/ prompts/ 2>/dev/null | wc -l)
  if [ "$unrecorded" -gt 0 ]; then
    echo "FAIL: $unrecorded commit(s) changed hub/ or prompts/ since VERSION was last bumped."
    echo "      Bump VERSION and add a CHANGELOG.md entry, or the change ships unrecorded:"
    git log --format='        %h %s' "$version_commit"..HEAD -- hub/ prompts/ 2>/dev/null | head -5
    fail=1
  fi
  # The version being shipped must actually appear in the changelog.
  ver=$(tr -d '[:space:]' < VERSION | sed -E 's/-(dev|rc[0-9]*)$//')
  if ! grep -q "^## \[$ver\]" CHANGELOG.md; then
    echo "FAIL: CHANGELOG.md has no '## [$ver]' section for the version in VERSION"; fail=1
  fi
fi

if [ "$fail" = "0" ]; then echo "check-docs: PASS"; else echo "check-docs: FAIL"; fi
exit "$fail"
