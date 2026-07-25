#!/usr/bin/env bash
# Create a new Loreport brain: a local folder, a git repo, and (optionally) a
# PRIVATE GitHub backup.
#
#   ./scripts/init-brain.sh                          # interactive
#   ./scripts/init-brain.sh --name loreport-ada --path ~/brains --no-github
#   ./scripts/init-brain.sh --with-hub               # also create provider/* branches
#
# Safe to inspect before running; it does nothing irreversible without asking.
# A brain holds personal memories, so the GitHub repo is created PRIVATE and this
# script REFUSES TO PUSH unless it has confirmed privacy from the API first.
set -uo pipefail

FRAMEWORK="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$FRAMEWORK/brain-template"

NAME=""; PARENT=""; GITHUB="ask"; WITH_HUB=0; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name)      NAME="${2:-}"; shift 2 ;;
    --path)      PARENT="${2:-}"; shift 2 ;;
    --github)    GITHUB="yes"; shift ;;
    --no-github) GITHUB="no"; shift ;;
    --with-hub)  WITH_HUB=1; shift ;;
    --yes|-y)    ASSUME_YES=1; shift ;;
    -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "init-brain: unknown option '$1' (try --help)" >&2; exit 1 ;;
  esac
done

die()  { echo "init-brain: $*" >&2; exit 1; }
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask()  { # ask <prompt> <default>; honours --yes
  local reply
  if [ "$ASSUME_YES" = "1" ]; then printf '%s' "$2"; return; fi
  read -r -p "$1" reply </dev/tty || true
  printf '%s' "${reply:-$2}"
}
confirm() { # confirm <question>  -> 0 if yes
  [ "$ASSUME_YES" = "1" ] && return 0
  local r; r=$(ask "$1 [y/N] " "n"); case "$r" in [yY]*) return 0 ;; *) return 1 ;; esac
}

[ -d "$TEMPLATE" ] || die "can't find brain-template/ next to this script ($TEMPLATE)"
command -v git >/dev/null || die "git is required"

# --- 1. name and location ------------------------------------------------------
if [ -z "$NAME" ]; then
  suggested="loreport-$(git config user.name 2>/dev/null | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | sed 's/^-*//;s/-*$//')"
  [ "$suggested" = "loreport-" ] && suggested="loreport-$(whoami)"
  NAME=$(ask "Brain name [$suggested]: " "$suggested")
fi
case "$NAME" in ""|*[!a-zA-Z0-9._-]*) die "name '$NAME' must be a plain slug (letters, digits, . _ -)";; esac

if [ -z "$PARENT" ]; then
  PARENT=$(ask "Create it under [$HOME/projects]: " "$HOME/projects")
fi
PARENT="${PARENT/#\~/$HOME}"
BRAIN="$PARENT/$NAME"

[ -e "$BRAIN" ] && [ -n "$(ls -A "$BRAIN" 2>/dev/null)" ] && \
  die "$BRAIN already exists and is not empty — refusing to overwrite it"

# Your brain must be its OWN repository. If the target sits inside an existing git
# work tree -- most likely this framework clone -- then `git init` would nest one
# repo inside another and your personal memories could end up committed to, or
# pushed to, the PUBLIC framework repo. That is the worst outcome this script can
# produce, so it is a hard stop rather than a warning.
mkdir -p "$PARENT" 2>/dev/null || die "cannot create $PARENT"
enclosing=$(git -C "$PARENT" rev-parse --show-toplevel 2>/dev/null || true)
if [ -n "$enclosing" ]; then
  cat >&2 <<EOF
init-brain: refusing to create your brain inside an existing git repository.

  Requested location : $BRAIN
  Already inside repo: $enclosing

Your brain must be a SEPARATE, PRIVATE repository of your own — not this project,
not a fork of it, and not a branch inside it. This repo is the public tooling; your
brain is your private data. They must never be the same repository.

Pick a location outside it, e.g.:

  $0 --name $NAME --path "\$HOME/projects"
EOF
  exit 1
fi

say "Creating $BRAIN"
mkdir -p "$BRAIN" || die "cannot create $BRAIN"
cp -r "$TEMPLATE/." "$BRAIN/" || die "copy failed"
# The template ships a copy of the protocol so the brain is self-contained; make
# sure it is the CURRENT one rather than whatever the template was last synced to.
mkdir -p "$BRAIN/prompts"
cp "$FRAMEWORK/prompts/bootstrap.md" "$BRAIN/prompts/bootstrap.md"
chmod +x "$BRAIN/make-surface.sh" 2>/dev/null || true

# --- 2. git ---------------------------------------------------------------------
git -C "$BRAIN" init -q -b main || die "git init failed"
git -C "$BRAIN" add -A
git -C "$BRAIN" -c user.email="$(git config user.email 2>/dev/null || echo brain@localhost)" \
                -c user.name="$(git config user.name 2>/dev/null || echo Loreport)" \
                commit -q -m "Initialise $NAME from brain-template" || die "initial commit failed"
echo "  ✓ git repo initialised on main"

if [ "$WITH_HUB" = "1" ]; then
  for p in openclaw claude chatgpt; do git -C "$BRAIN" branch "provider/$p"; done
  echo "  ✓ provider/{openclaw,claude,chatgpt} branches created (Tier-2 hub)"
fi

# --- 3. GitHub backup (private, verified) --------------------------------------
manual_instructions() {
  cat <<EOF

  To back it up yourself, on any host you like:

    1. Create a BRAND-NEW, PRIVATE, EMPTY repository named "$NAME".
       - On GitHub: https://github.com/new — set it to Private, and do NOT add a
         README, .gitignore or licence (your brain already has its own history).
       - This is YOUR OWN repository. Do NOT fork the Loreport project, and do not
         push your brain to it — that repo is public tooling, this is private data.
         Two different repos, and they must stay that way.
    2. Connect and push:

       git -C "$BRAIN" remote add origin <your-repo-url>
       git -C "$BRAIN" push -u origin --all

  Prefer something other than GitHub? Anything git can push to works — a self-hosted
  Forgejo/Gitea, a private GitLab, even a bare repo on another machine you own:

       git init --bare /path/to/backup/$NAME.git      # on the other machine
       git -C "$BRAIN" remote add origin <that path or ssh url>

  No remote at all is a valid choice too; the brain is a folder and works offline.
  Whatever you pick, keep it private and back the folder up some other way as well.
EOF
}

if [ "$GITHUB" = "ask" ]; then
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    confirm "Create a PRIVATE GitHub repo '$NAME' and push to it?" && GITHUB="yes" || GITHUB="no"
  else
    GITHUB="no"
    say "GitHub CLI not available or not logged in — skipping automatic backup."
  fi
fi

if [ "$GITHUB" = "yes" ]; then
  command -v gh >/dev/null || die "--github given but the 'gh' CLI is not installed"
  gh auth status >/dev/null 2>&1 || die "--github given but 'gh' is not logged in (run: gh auth login)"

  say "Creating private GitHub repo '$NAME'"
  if ! gh repo create "$NAME" --private --source "$BRAIN" --remote origin >/dev/null 2>&1; then
    echo "  ! could not create the repo (it may already exist, or the name is taken)" >&2
    manual_instructions
  else
    # Do NOT trust the --private flag alone. Read the state back and refuse to push
    # anything if the API does not confirm it is private. A public brain is the one
    # failure this script must never allow.
    owner=$(gh api user --jq .login 2>/dev/null)
    is_private=$(gh repo view "$owner/$NAME" --json isPrivate --jq .isPrivate 2>/dev/null)
    if [ "$is_private" != "true" ]; then
      echo "  !! ABORTING PUSH: GitHub does not report $owner/$NAME as private (got: '${is_private:-unknown}')." >&2
      echo "  !! Nothing has been uploaded. Make it private, then push manually." >&2
      manual_instructions
    else
      echo "  ✓ verified private via the GitHub API"
      if git -C "$BRAIN" push -q -u origin --all 2>/dev/null; then
        echo "  ✓ pushed to $owner/$NAME"
      else
        echo "  ! push failed — the repo exists and is private, but nothing was uploaded" >&2
        manual_instructions
      fi
    fi
  fi
else
  [ "$GITHUB" = "no" ] && manual_instructions
fi

# --- 4. what to do next ---------------------------------------------------------
say "Done — your brain is at $BRAIN"
cat <<EOF

Next, in this order:

  1. Fill it. Paste this file into any LLM chat and answer the interview:
       $FRAMEWORK/prompts/onboard.md
     Save each block it emits to the path in its file= line, under $BRAIN.

  2. Load it. Build the one file you paste:
       cd "$BRAIN" && ./make-surface.sh --host "ChatGPT"
     Then paste surface.md into that assistant's instructions field.
     Items you mark 'visibility: local' are withheld from that file automatically.

  3. Check your work at any point:
       cd "$BRAIN" && ./doctor.sh
     It verifies layout, git, index integrity, and that no private item can reach
     a cloud assistant. Add --providers once a connector is set up to probe each
     one live.

  4. Everything else — other hosts, adding a second assistant, monthly upkeep:
       $FRAMEWORK/docs/setup.md
EOF
[ "$WITH_HUB" = "1" ] && echo "  4. Tier-2 hub setup (credentials, timers): $FRAMEWORK/hub/HUB.md"
exit 0
