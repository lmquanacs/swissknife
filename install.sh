#!/usr/bin/env bash
# Install the skills in this repo for Claude Code.
#
#   ./install.sh                      symlink into ~/.claude/skills (all projects)
#   ./install.sh --project ~/code/app symlink into that project's .claude/skills
#   ./install.sh --copy               copy instead of symlink, to pin a version
#
# An existing install of the same name is removed first, so re-running is how
# you reinstall. A symlink is just dropped; a real directory might hold someone's
# edits, so it is moved aside to <name>.bak-<timestamp> rather than deleted, and
# the path is printed.
#
# Symlink is the default and the one to want: the repo stays the single source
# of truth, edits take effect immediately, and `git pull` updates the install.
# Copy is for pinning a version, or for committing the skill into a project so
# teammates get it from a clone (symlinks do not survive one).
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skills=(context-builder)   # add new skill directory names here

dest_root="$HOME/.claude/skills"
scope="user"
mode="symlink"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ -n "${2:-}" ]] || { echo "error: --project needs a path" >&2; exit 1; }
      [[ -d "$2" ]] || { echo "error: no such directory: $2" >&2; exit 1; }
      dest_root="$(cd "$2" && pwd)/.claude/skills"
      scope="project"
      shift 2 ;;
    --copy)  mode="copy";  shift ;;
    -h|--help)  # the header comment block, so help can't drift from the source
      awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; exit 1 ;;
  esac
done

echo "installing into $dest_root ($scope scope, $mode)"
mkdir -p "$dest_root"

for skill in "${skills[@]}"; do
  src="$repo/skills/$skill"
  dest="$dest_root/$skill"
  [[ -d "$src" ]] || { echo "error: $src is not in this repo" >&2; exit 1; }

  # Clear any same-name skill already installed here. Claude Code keys a skill
  # off its directory name, so a leftover one would win or shadow this install.
  if [[ -L "$dest" ]]; then
    current="$(readlink "$dest")"
    if [[ "$current" == "$src" ]]; then
      echo "  removing existing $skill symlink (same source, reinstalling)"
    else
      echo "  removing existing $skill symlink -> $current"
    fi
    rm "$dest"
  elif [[ -e "$dest" ]]; then
    # A real directory can hold edits this repo has never seen. Move it aside
    # instead of deleting it, and print where it went.
    backup="$dest.bak-$(date +%Y%m%d-%H%M%S)"
    echo "  found an existing $skill directory (not a symlink)"
    echo "  moved it to $backup — delete that yourself once you've checked it"
    mv "$dest" "$backup"
  fi

  if [[ "$mode" == "copy" ]]; then
    cp -R "$src" "$dest"
    # The venv is machine-specific and often large; bootstrap rebuilds it below.
    rm -rf "$dest/scripts/.venv"
  else
    ln -s "$src" "$dest"
  fi
  echo "  installed $skill"
done

# A same-name skill in the other scope still shadows or competes with this one.
# Not ours to delete — the user may want it — but silence here is a debugging trap.
for skill in "${skills[@]}"; do
  if [[ "$scope" == "project" ]]; then
    other="$HOME/.claude/skills/$skill"; other_scope="user"
  else
    other=""; other_scope=""
  fi
  if [[ -n "$other" && -e "$other" ]]; then
    echo "note: a $skill skill is also installed at $other_scope scope ($other)."
    echo "      Project scope wins, so that one is now shadowed. Remove it if"
    echo "      you no longer want it."
  fi
done

# Step 2 of the install: the reading-list scripts parse with tree-sitter and have
# no regex fallback, so an install without this is an install that crashes.
for skill in "${skills[@]}"; do
  bootstrap="$dest_root/$skill/scripts/bootstrap.sh"
  [[ -x "$bootstrap" ]] || continue
  echo "bootstrapping $skill"
  "$bootstrap" | sed 's/^/  /'
done

# Verify rather than assume, so a broken install fails here and not mid-task.
status=0
for skill in "${skills[@]}"; do
  score="$dest_root/$skill/scripts/score-skill.sh"
  [[ -x "$score" ]] || continue
  echo
  "$score" || status=1
done

echo
if [[ "$status" -eq 0 ]]; then
  echo "done. Start a new Claude Code session — skills load at session start, so"
  echo "an already-running one will not see this. Then try /context-builder."
else
  echo "installed, but the checks above found problems — fix those before use." >&2
fi
exit "$status"
