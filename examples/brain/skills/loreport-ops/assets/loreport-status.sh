#!/usr/bin/env bash
# loreport-status.sh — compact health report for a Loreport brain.
#
# Per the Loreport invariant, brain content is read from the `main` branch
# (via `git show main:<path>`), never from the working tree.
set -uo pipefail

BRAIN_DIR="/home/nvidia/projects/loreport-oyvind-theie"

usage() {
  cat <<'EOF'
Usage: loreport-status.sh [--brain-dir DIR]

Print a compact health report for a Loreport brain.

  --brain-dir DIR   Path to the brain git repo
                     (default: /home/nvidia/projects/loreport-oyvind-theie)
  --help            Show this help and exit
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --brain-dir)
      [ $# -ge 2 ] || { echo "loreport-status: --brain-dir requires an argument" >&2; exit 2; }
      BRAIN_DIR="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "loreport-status: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ ! -d "$BRAIN_DIR" ]; then
  echo "loreport-status: no such directory: $BRAIN_DIR" >&2
  exit 1
fi

if ! git -C "$BRAIN_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "loreport-status: not a git repo: $BRAIN_DIR" >&2
  exit 1
fi

if ! git -C "$BRAIN_DIR" show-ref --verify --quiet refs/heads/main; then
  echo "loreport-status: $BRAIN_DIR has no local 'main' branch" >&2
  exit 1
fi

g() { git -C "$BRAIN_DIR" "$@"; }

echo "Loreport brain status — $BRAIN_DIR"
echo "======================================================================"

# --- Items: memories/ + knowledge/ on main, read via git show (batched ls-tree) ---
files=$(g ls-tree -r --name-only main -- memories knowledge 2>/dev/null | grep -E '\.md$' || true)
total=0
shared=0
local_n=0
declare -A type_counts

if [ -n "$files" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    total=$((total + 1))
    fm=$(g show "main:$f" 2>/dev/null | awk '/^---$/{n++; next} n==1')
    vis=$(printf '%s\n' "$fm" | grep -m1 '^visibility:' | sed 's/^visibility:[[:space:]]*//')
    typ=$(printf '%s\n' "$fm" | grep -m1 '^type:' | sed 's/^type:[[:space:]]*//')
    [ -z "$typ" ] && typ="(untyped)"
    if [ "$vis" = "local" ]; then
      local_n=$((local_n + 1))
    else
      shared=$((shared + 1))
    fi
    type_counts["$typ"]=$(( ${type_counts["$typ"]:-0} + 1 ))
  done <<< "$files"
fi

echo "Items: $total total  ($shared shared / $local_n local)"
for t in "${!type_counts[@]}"; do
  printf '  - %-12s %d\n' "$t" "${type_counts[$t]}"
done | sort

# --- Skills: directories under skills/ that contain SKILL.md ---
skill_files=$(g ls-tree -r --name-only main -- skills 2>/dev/null | grep '/SKILL\.md$' || true)
skill_count=0
skill_names=""
if [ -n "$skill_files" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    name=$(basename "$(dirname "$f")")
    skill_count=$((skill_count + 1))
    skill_names="${skill_names}${skill_names:+, }$name"
  done <<< "$skill_files"
fi
echo "Skills: $skill_count ($skill_names)"

# --- INDEX.md ---
if idx=$(g show main:INDEX.md 2>/dev/null); then
  idx_lines=$(printf '%s\n' "$idx" | wc -l)
  echo "INDEX.md: $idx_lines lines"
else
  echo "INDEX.md: not found on main"
fi

# --- Last commit on main ---
last=$(g log -1 --format='%h %s (%cr)' main 2>/dev/null)
echo "Last commit (main): $last"

# --- Sync state vs origin/main (no network fetch; existing refs only) ---
if g show-ref --verify --quiet refs/remotes/origin/main; then
  read -r ahead behind <<< "$(g rev-list --left-right --count main...origin/main 2>/dev/null)"
  if [ "$ahead" = "0" ] && [ "$behind" = "0" ]; then
    echo "Sync vs origin/main: in sync"
  else
    echo "Sync vs origin/main: $ahead ahead, $behind behind"
  fi
else
  echo "Sync vs origin/main: no remote"
fi

# --- Provider branches vs main ---
providers=$(g for-each-ref --format='%(refname:short)' 'refs/heads/provider/*' 2>/dev/null)
if [ -n "$providers" ]; then
  echo "Provider branches:"
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    read -r ahead behind <<< "$(g rev-list --left-right --count "main...$p" 2>/dev/null)"
    if [ "$behind" = "0" ]; then
      state="merged into main"
    else
      state="NOT fully merged"
    fi
    echo "  - $p: $state ($ahead ahead / $behind behind main)"
  done <<< "$providers"
else
  echo "Provider branches: none"
fi

# --- Packet freshness (working tree file; compared against last main commit) ---
packet="$BRAIN_DIR/hub/published/packet.md"
if [ -f "$packet" ]; then
  packet_mtime=$(date -r "$packet" '+%Y-%m-%d %H:%M:%S %z')
  packet_epoch=$(date -r "$packet" '+%s')
  main_epoch=$(g log -1 --format='%ct' main 2>/dev/null)
  echo "Packet: hub/published/packet.md (mtime: $packet_mtime)"
  if [ -n "$main_epoch" ] && [ "$packet_epoch" -lt "$main_epoch" ]; then
    echo "  WARNING: packet is older than the last main commit — run sync.sh"
  fi
else
  echo "Packet: hub/published/packet.md MISSING"
fi
