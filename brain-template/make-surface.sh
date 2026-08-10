#!/usr/bin/env bash
# Assemble the operating surface into ONE file you can copy in a single action.
#
#   ./make-surface.sh                    -> surface.md  SHARED items only — safe anywhere
#   ./make-surface.sh --host ChatGPT     -> also fills the protocol's "Host:" blank, so
#                                           captures are stamped with where they came from
#   ./make-surface.sh --all              -> include `local` items (local hosts only)
#
# surface.md = the brain protocol + PROFILE.md + INDEX.md (+ INDEX-ARCHIVE.md when the
# brain has archived anything). That is the whole always-loaded footprint; detail files
# stay on disk until something needs them.
# Re-run this whenever PROFILE.md or either index changes.
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

# Is this item file allowed into a surface? FAIL CLOSED: yes ONLY on an explicit
# `visibility: shared` inside the frontmatter block. An unmarked item, a malformed
# value, a quoted or comment-trailed value, or a file with no frontmatter at all
# all answer no and are withheld.
#
# This used to be the inverse test -- `grep -qiE '^visibility:[[:space:]]*local[[:space:]]*$'`,
# include unless that exact line appeared -- which meant surface.md carried items the
# published packet withheld, because hub/*.py has always parsed this fail-closed.
# surface.md is pasted into a cloud assistant, so the looser rule was on the wrong side.
#
# Skills are the one exception, and not a new one: a skill is a package, not an item,
# and carries no `visibility:` field at all (docs/format-spec.md section 1).
item_is_shared() {
  case "$1" in
    skills/*) return 0 ;;
  esac
  awk '
    NR == 1 {
      sub(/^\357\273\277/, "")                     # strip a UTF-8 BOM
      if ($0 !~ /^---[ \t]*$/) exit 1                # no frontmatter block -> withhold
      next
    }
    /^---[ \t]*$/ { exit (seen == "shared") ? 0 : 1 }
    {
      i = index($0, ":")
      if (i == 0) next
      k = substr($0, 1, i - 1)
      v = substr($0, i + 1)
      gsub(/^[ \t]+|[ \t]+$/, "", k)
      if (tolower(k) != "visibility") next
      j = index(v, "#")
      if (j > 0) v = substr(v, 1, j - 1)
      gsub(/^[ \t]+|[ \t]+$/, "", v)
      gsub(/^["\047]|["\047]$/, "", v)
      gsub(/^[ \t]+|[ \t]+$/, "", v)
      seen = tolower(v)
    }
    END { exit (seen == "shared") ? 0 : 1 }           # unterminated frontmatter -> same rule
  ' "$1"
}

# THE ARCHIVE SEAM, again — a paste host cannot lazy-fetch a cold shelf any more
# than a cloud provider can. surface.md is the whole catalog such a host will ever
# see, so an archived SHARED item must appear here exactly like it appears in the
# published packet; otherwise the day an item expires it silently disappears from
# every paste host, with nothing reporting it. Only `visibility: local` is ever
# withheld. INDEX-ARCHIVE.md is absent on a brain that has never archived anything,
# which is normal and not an error.
indexes=(INDEX.md)
[ -f INDEX-ARCHIVE.md ] && indexes+=(INDEX-ARCHIVE.md)

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
    if item_is_shared "$file"; then
      printf '%s\n' "$line"
    else
      dropped=$((dropped + 1))
    fi
  done < <(cat "${indexes[@]}")
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
