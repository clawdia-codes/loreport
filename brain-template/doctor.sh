#!/usr/bin/env bash
# Post-setup self-test. Run this INSIDE your brain folder, any time:
#
#   ./doctor.sh                # structure, integrity, and the privacy wall
#   ./doctor.sh --providers    # also probe each configured provider credential
#
# Exits nonzero if anything failed. It reads only, except that it rebuilds surface.md
# to test the privacy wall — an existing surface.md is preserved and restored.
set -uo pipefail
cd "$(dirname "$0")"
BRAIN="$(pwd)"

PROBE_PROVIDERS=0
[ "${1:-}" = "--providers" ] && PROBE_PROVIDERS=1

pass=0; fail=0; warn=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; pass=$((pass+1)); }
no()   { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=$((fail+1)); }
hmm()  { printf '  \033[33m!\033[0m %s\n' "$*"; warn=$((warn+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. layout ---
head_ "Layout"
for f in PROFILE.md INDEX.md make-surface.sh prompts/bootstrap.md; do
  [ -f "$f" ] && ok "$f" || no "$f is missing"
done
for d in memories knowledge skills; do
  [ -d "$d" ] && ok "$d/" || no "$d/ is missing"
done
[ -x make-surface.sh ] || hmm "make-surface.sh is not executable (chmod +x make-surface.sh)"
grep -q '<your name or handle' PROFILE.md 2>/dev/null && \
  hmm "PROFILE.md still has template placeholders — run prompts/onboard.md to fill it"

# ------------------------------------------------------------------- 2. git ---
head_ "Git"
if git rev-parse --git-dir >/dev/null 2>&1; then
  top=$(git rev-parse --show-toplevel)
  if [ "$top" != "$BRAIN" ]; then
    no "this folder is INSIDE another git repo ($top) — your brain must be its own repository"
  else
    ok "its own git repository"
  fi
  br=$(git symbolic-ref --short -q HEAD || echo DETACHED)
  [ "$br" = "main" ] && ok "on main" || hmm "on '$br', expected main"
  grep -qx 'surface.md' .gitignore 2>/dev/null && ok "surface.md is gitignored" \
    || no "surface.md is NOT gitignored — a generated file with your catalog could be committed"
  if remote=$(git remote get-url origin 2>/dev/null); then
    ok "backup remote: $remote"
    if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
      slug=$(printf '%s' "$remote" | sed -E 's#^.*github\.com[:/]##; s#\.git$##')
      case "$remote" in
        *github.com*)
          priv=$(gh repo view "$slug" --json isPrivate --jq .isPrivate 2>/dev/null)
          if [ "$priv" = "true" ]; then ok "backup repo is PRIVATE"
          elif [ "$priv" = "false" ]; then no "BACKUP REPO IS PUBLIC — your memories are exposed. Make $slug private now."
          else hmm "could not confirm $slug is private (check it by hand)"; fi ;;
      esac
    else
      hmm "gh not available — confirm your backup repo is private by hand"
    fi
    [ -n "$(git log --oneline @{u}..HEAD 2>/dev/null)" ] && hmm "unpushed commits — your backup is behind"
  else
    hmm "no backup remote configured (fine if deliberate; the brain works offline)"
  fi
else
  no "not a git repository — you lose history and rollback (run: git init -b main)"
fi

# --------------------------------------------------------------- 3. items ----
head_ "Items"
TYPES="user feedback project reference knowledge"
items=0; bad_fm=0
for f in memories/*.md knowledge/*.md; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in README.md) continue ;; esac
  items=$((items+1))
  stem=$(basename "$f" .md)
  fm_name=$(sed -n '2,/^---$/p' "$f" | sed -n 's/^name: *//p' | head -1)
  fm_type=$(sed -n '2,/^---$/p' "$f" | sed -n 's/^type: *//p' | head -1)
  fm_vis=$(sed -n  '2,/^---$/p' "$f" | sed -n 's/^visibility: *//p' | head -1)
  [ "$(head -1 "$f")" = "---" ] || { no "$f: no frontmatter (it will be invisible to the index)"; bad_fm=$((bad_fm+1)); continue; }
  [ "$fm_name" = "$stem" ] || { no "$f: name '$fm_name' != filename '$stem' (lookups by name will fail)"; bad_fm=$((bad_fm+1)); }
  case " $TYPES " in *" $fm_type "*) ;; *) no "$f: type '$fm_type' not one of: $TYPES"; bad_fm=$((bad_fm+1)) ;; esac
  if [ -n "$fm_vis" ] && [ "$fm_vis" != "shared" ] && [ "$fm_vis" != "local" ]; then
    no "$f: visibility '$fm_vis' is neither 'shared' nor 'local' — it will be treated as PRIVATE (fail-closed)"
    bad_fm=$((bad_fm+1))
  fi
done
[ "$bad_fm" = "0" ] && ok "$items items, all with valid frontmatter"

# INDEX <-> files, both directions
missing_file=0; missing_line=0
while read -r name; do
  [ -z "$name" ] && continue
  [ -f "memories/$name.md" ] || [ -f "knowledge/$name.md" ] || [ -f "skills/$name/SKILL.md" ] \
    || { no "INDEX lists [[$name]] but no such item exists"; missing_file=$((missing_file+1)); }
done < <(grep -oP '(?<=^- \[\[)[^]]+' INDEX.md 2>/dev/null)
for f in memories/*.md knowledge/*.md; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in README.md) continue ;; esac
  n=$(basename "$f" .md)
  grep -q "^- \[\[$n\]\]" INDEX.md 2>/dev/null || { no "$f has no INDEX line — it is invisible to search"; missing_line=$((missing_line+1)); }
done
[ "$missing_file" = "0" ] && [ "$missing_line" = "0" ] && ok "INDEX matches the files on disk, both directions"

# Dangling wikilinks. A link to an item you haven't written yet is FINE by design --
# it marks something worth writing later. So these are reported as one summary line,
# not one warning per link. The exception worth acting on is a link that would
# resolve if underscores were hyphens: that is a naming-migration leftover, not an
# intentional forward reference.
dangling=0; slugfix=0
for f in memories/*.md knowledge/*.md skills/*/SKILL.md; do
  [ -f "$f" ] || continue
  while read -r l; do
    [ -z "$l" ] && continue
    if [ ! -f "memories/$l.md" ] && [ ! -f "knowledge/$l.md" ] && [ ! -f "skills/$l/SKILL.md" ]; then
      dangling=$((dangling+1))
      alt=$(printf '%s' "$l" | tr '_' '-')
      if [ -f "memories/$alt.md" ] || [ -f "knowledge/$alt.md" ] || [ -f "skills/$alt/SKILL.md" ]; then
        slugfix=$((slugfix+1))
        [ "${DOCTOR_VERBOSE:-0}" = "1" ] && echo "      $f: [[$l]] -> [[$alt]]"
      else
        [ "${DOCTOR_VERBOSE:-0}" = "1" ] && echo "      $f: [[$l]] (no such item)"
      fi
    fi
  done < <(grep -oP '(?<=\[\[)[^]]+' "$f" 2>/dev/null | sort -u)
done
if [ "$slugfix" -gt 0 ]; then
  no "$slugfix wikilink(s) use underscores where the item uses hyphens — a naming-migration leftover"
  echo "      Re-run with DOCTOR_VERBOSE=1 to list them."
fi
if [ "$((dangling-slugfix))" -gt 0 ]; then
  ok "$((dangling-slugfix)) forward reference(s) to items not written yet (fine by design)"
fi
[ "$dangling" = "0" ] && ok "no dangling [[wikilinks]]"

# ------------------------------------------------------- 4. the privacy wall --
head_ "Privacy wall"
localn=$(grep -lx 'visibility: local' memories/*.md knowledge/*.md 2>/dev/null | wc -l)
ok "$localn item(s) marked local, $((items-localn)) shared"
# Rebuilding surface.md is the only way to test the wall end to end. Preserve any
# existing one so a doctor run never destroys the file the user is about to paste.
# mktemp, never a predictable /tmp/<name>.$PID: a world-writable directory plus a
# guessable name lets a local attacker pre-place a symlink, which would redirect the
# capture below and -- worse -- let the restore step move attacker-controlled content
# INTO the brain.
_dtmp=$(mktemp "${TMPDIR:-/tmp}/loreport-doctor.XXXXXX") || { echo "doctor: cannot create a temp file" >&2; exit 1; }
_dsurf=""
if [ -f surface.md ]; then
  _dsurf=$(mktemp "${TMPDIR:-/tmp}/loreport-surface.XXXXXX") || { echo "doctor: cannot create a temp file" >&2; exit 1; }
  cp surface.md "$_dsurf"
fi
if ./make-surface.sh >"$_dtmp" 2>&1; then
  leaked=0
  while read -r n; do
    [ -z "$n" ] && continue
    for p in "memories/$n.md" "knowledge/$n.md"; do
      [ -f "$p" ] && grep -qx 'visibility: local' "$p" && { no "LEAK: local item [[$n]] appears in surface.md"; leaked=1; }
    done
  done < <(grep -oP '(?<=^- \[\[)[^]]+' surface.md 2>/dev/null)
  [ "$leaked" = "0" ] && ok "surface.md contains no local item (safe to paste into a cloud assistant)"
  grep -q 'set it here: `____`' surface.md && hmm "surface.md's Host: blank is unfilled — use ./make-surface.sh --host \"ChatGPT\""
else
  no "make-surface.sh failed: $(tail -1 "$_dtmp")"
fi
rm -f "$_dtmp" surface.md 2>/dev/null
if [ -n "$_dsurf" ] && [ -f "$_dsurf" ]; then
  mv "$_dsurf" surface.md
fi

# obvious secrets in anything that can leave the machine
secrets=0
for f in PROFILE.md $(grep -Lx 'visibility: local' memories/*.md knowledge/*.md 2>/dev/null) skills/*/SKILL.md; do
  [ -f "$f" ] || continue
  grep -qiE '(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(password|api[_-]?key|secret|token) *[:=] *[A-Za-z0-9/+_-]{12,})' "$f" \
    && { no "possible secret in $f — this file CAN reach a cloud provider; remove the value and rotate it"; secrets=$((secrets+1)); }
done
[ "$secrets" = "0" ] && ok "no obvious secrets in anything shareable"

# ------------------------------------------------------------ 5. tier-2 hub ---
if [ -d hub ] || git show-ref --quiet refs/heads/provider/openclaw 2>/dev/null; then
  head_ "Sync hub (Tier 2)"
  for p in openclaw claude chatgpt; do
    git show-ref --quiet "refs/heads/provider/$p" && ok "provider/$p branch exists" \
      || hmm "no provider/$p branch (fine if you don't bridge that provider)"
  done
  if [ -f hub/published/packet.md ]; then
    pleak=0
    while read -r n; do
      [ -z "$n" ] && continue
      for p in "memories/$n.md" "knowledge/$n.md"; do
        [ -f "$p" ] && grep -qx 'visibility: local' "$p" && { no "LEAK: local item [[$n]] is in the published packet"; pleak=1; }
      done
    done < <(grep -oP '(?<=^- \[\[)[^]]+' hub/published/packet.md 2>/dev/null)
    [ "$pleak" = "0" ] && ok "published packet contains no local item"
  fi
fi

# --------------------------------------------------------- 6. live providers --
if [ "$PROBE_PROVIDERS" = "1" ]; then
  head_ "Provider probes (live)"
  FW="${LOREPORT_FRAMEWORK:-$(dirname "$BRAIN")/loreport}"
  if [ ! -f "$FW/hub/mcp_server.py" ]; then
    hmm "framework not found at $FW — set LOREPORT_FRAMEWORK=/path/to/loreport to probe providers"
  else
    LOC=$(grep -lx 'visibility: local' memories/*.md 2>/dev/null | head -1 | xargs -r -n1 basename | sed 's/\.md$//')
    SHA=$(grep -Lx 'visibility: local' memories/*.md 2>/dev/null | head -1 | xargs -r -n1 basename | sed 's/\.md$//')
    # The credential goes in the ENVIRONMENT, never argv: an argv value is readable
    # by any local user via `ps aux` for as long as the probe runs.
    probe() { # probe <credential> <item>  -> prints "ok" or "refused"
      printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"loreport_read_memory","arguments":{"name":"%s"}}}\n' "$2" \
        | LOREPORT_CREDENTIAL="$1" timeout 25 python3 "$FW/hub/mcp_server.py" --brain-dir "$BRAIN" 2>/dev/null \
        | grep -q '"isError": true' && echo refused || echo ok
    }
    creds=$(python3 -c "
import json,sys
try:
    d=json.load(open('$FW/hub/config/providers.json'))
    for k,v in d.get('credentials',{}).items():
        print(v.get('provider',''), v.get('trust',''), __import__('os').environ.get(k) or v.get('default',''))
except Exception: pass" 2>/dev/null)
    [ -z "$creds" ] && hmm "no credentials found in $FW/hub/config/providers.json"
    while read -r prov trust tok; do
      [ -z "$tok" ] && continue
      if [ -n "$SHA" ]; then
        [ "$(probe "$tok" "$SHA")" = "ok" ] && ok "$prov ($trust): can read a shared item" \
                                            || no "$prov ($trust): CANNOT read a shared item — connector may be broken"
      fi
      if [ -n "$LOC" ]; then
        r=$(probe "$tok" "$LOC")
        if [ "$trust" = "cloud" ]; then
          [ "$r" = "refused" ] && ok "$prov (cloud): correctly REFUSED a local item" \
                               || no "$prov (cloud): READ A LOCAL ITEM — the privacy wall is broken"
        else
          [ "$r" = "ok" ] && ok "$prov (local): can read a local item" \
                          || no "$prov (local): cannot read a local item"
        fi
      fi
    done <<< "$creds"
  fi
fi

# ------------------------------------------------------------------ summary ---
printf '\n\033[1m%s\033[0m\n' "Summary: $pass passed, $fail failed, $warn warning(s)"
if [ "$fail" -gt 0 ]; then
  echo "Fix the ✗ lines above. Anything mentioning a LEAK or a PUBLIC repo is urgent."
  exit 1
fi
[ "$warn" -gt 0 ] && echo "No failures. The ! lines are worth a look but nothing is broken."
echo
echo "What this cannot check — do these by hand once per assistant:"
echo "  · Ask it something only a SHARED memory answers  -> it should know."
echo "  · Ask a cloud assistant about a LOCAL topic      -> it should come up empty."
echo "  · Tell it a durable fact, then say \"sweep\"       -> it should offer to save it."
echo "  · Say \"reconcile my memories\" twice             -> the second run should find ~nothing."
exit 0
