#!/usr/bin/env bash
# Assemble the operating surface into ONE file you can copy in a single action.
#
#   ./make-surface.sh                    -> surface.md  SHARED items only — safe anywhere
#   ./make-surface.sh --host ChatGPT     -> also fills the protocol's "Host:" blank, so
#                                           captures are stamped with where they came from
#   ./make-surface.sh --all              -> include `local` items (local hosts only)
#
# surface.md = the brain protocol + PROFILE.md + INDEX.md. That is the whole
# always-loaded footprint; detail files stay on disk until something needs them.
# Re-run this whenever PROFILE.md or INDEX.md changes.
#
# WHY SHARED-ONLY IS THE DEFAULT: pasting is how the brain reaches hosts that can't
# read your files — which in practice means cloud chat boxes. An item marked
# `visibility: local` must never get there, so it is dropped from the catalog unless
# you explicitly ask for --all. Use --all only for a host on your own machine.
set -euo pipefail
cd "$(dirname "$0")"

INCLUDE_LOCAL=0
HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --all)  INCLUDE_LOCAL=1; shift ;;
    --host) HOST="${2:-}"; [ -n "$HOST" ] || { echo "make-surface: --host needs a name" >&2; exit 1; }; shift 2 ;;
    *)      echo "make-surface: unknown option '$1' (use --all, --host NAME)" >&2; exit 1 ;;
  esac
done

# The protocol lives in the framework repo. If you copied this template out on its
# own, drop a copy of prompts/bootstrap.md beside it, or set BOOTSTRAP=/path/to/it.
BOOTSTRAP="${BOOTSTRAP:-}"
if [ -z "$BOOTSTRAP" ]; then
  for candidate in prompts/bootstrap.md ../prompts/bootstrap.md ../../prompts/bootstrap.md; do
    [ -f "$candidate" ] && BOOTSTRAP="$candidate" && break
  done
fi
if [ ! -f "${BOOTSTRAP:-/nonexistent}" ]; then
  echo "make-surface: can't find bootstrap.md — set BOOTSTRAP=/path/to/prompts/bootstrap.md" >&2
  exit 1
fi
for f in PROFILE.md INDEX.md; do
  [ -f "$f" ] || { echo "make-surface: $f not found in $(pwd)" >&2; exit 1; }
done

# Locate the file behind an INDEX line's [[name]], across the three item homes.
item_file() {
  for p in "memories/$1.md" "knowledge/$1.md" "skills/$1/SKILL.md"; do
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
  done
  return 1
}

dropped=0
index_out=$(
  while IFS= read -r line; do
    name=$(printf '%s' "$line" | sed -n 's/^[[:space:]]*-[[:space:]]*\[\[\([^]]*\)\]\].*/\1/p')
    if [ -z "$name" ] || [ "$INCLUDE_LOCAL" = "1" ]; then
      printf '%s\n' "$line"; continue          # section heading, blank line, or --all
    fi
    if ! file=$(item_file "$name"); then
      echo "make-surface: WARNING no file for [[$name]] — keeping its index line" >&2
      printf '%s\n' "$line"; continue
    fi
    if grep -qiE '^visibility:[[:space:]]*local[[:space:]]*$' "$file"; then
      dropped=$((dropped + 1))
    else
      printf '%s\n' "$line"
    fi
  done < INDEX.md
  printf '%s' "__DROPPED__$dropped"            # subshell can't export; smuggle the count
)
dropped=${index_out##*__DROPPED__}
index_out=${index_out%__DROPPED__*}

protocol=$(cat "$BOOTSTRAP")
if [ -n "$HOST" ]; then
  # The protocol carries a blank the capturing assistant stamps onto every item's
  # `source:`. Left empty it files captures as "____", so fill it when we know.
  protocol=$(printf '%s' "$protocol" | sed "s/set it here: \`____\`/set it here: \`$HOST\`/")
fi

{ printf '%s\n' "$protocol"; echo; cat PROFILE.md; echo; printf '%s' "$index_out"; } > surface.md

if grep -q 'set it here: `____`' surface.md; then
  echo "make-surface: NOTE the protocol's Host: blank is unfilled — captures will be" >&2
  echo "make-surface:      stamped 'source: ____'. Re-run with --host \"ChatGPT\" (etc.)." >&2
fi

if [ "$INCLUDE_LOCAL" = "1" ]; then
  echo "make-surface: wrote surface.md ($(wc -c < surface.md) bytes) — INCLUDES local items."
  echo "make-surface: do NOT paste this into a cloud assistant. Re-run without --all for that."
else
  echo "make-surface: wrote surface.md ($(wc -c < surface.md) bytes) — $dropped local item(s) withheld."
  echo "make-surface: safe to paste. Detail files are fetched on demand, never bulk-pasted."
fi
