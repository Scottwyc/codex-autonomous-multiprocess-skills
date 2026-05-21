#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
TARGET_ROOT="$CODEX_HOME_DIR/skills/general"
BACKUP_ROOT="$CODEX_HOME_DIR/skills/.backup"
STAMP="$(date +%Y%m%d-%H%M%S)"

skills=(
  "long-running-autonomous-project-management"
  "tmux-codex-parallel-workers"
)

mkdir -p "$TARGET_ROOT" "$BACKUP_ROOT"

for skill in "${skills[@]}"; do
  src="$REPO_ROOT/skills/general/$skill"
  dest="$TARGET_ROOT/$skill"
  if [[ ! -f "$src/SKILL.md" ]]; then
    echo "Missing skill source: $src" >&2
    exit 1
  fi
  if [[ -e "$dest" ]]; then
    backup="$BACKUP_ROOT/$skill.$STAMP"
    echo "Backing up existing $dest -> $backup"
    mv "$dest" "$backup"
  fi
  echo "Installing $skill -> $dest"
  mkdir -p "$(dirname "$dest")"
  cp -a "$src" "$dest"
  find "$dest" -type d -name __pycache__ -prune -exec rm -rf {} +
done

echo "Installed skills into $TARGET_ROOT"

