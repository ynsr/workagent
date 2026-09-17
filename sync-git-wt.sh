#!/usr/bin/env bash
# sync-git-wt.sh — re-vendor git-wt from the canonical checkout in
# cli-agents-config into vendored/git-wt (plain copy, NO nested .git).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-$HOME/projects/personal/cli-agents-config/tools/git-wt}"
DEST="$DIR/vendored/git-wt"

if [ ! -d "$SRC/.git" ]; then
  echo "error: $SRC is not a git checkout (pass the path explicitly if it moved)"
  exit 1
fi

UPSTREAM_HEAD="$(git -C "$SRC" rev-parse --short HEAD)"
VENDORED_HEAD="$(sed -n 's/.*upstream head at `\([0-9a-f]*\)`.*/\1/p' "$DEST/VENDORED.md" 2>/dev/null || echo unknown)"


echo "==> syncing $SRC ($UPSTREAM_HEAD) -> vendored/git-wt"
rsync -a --delete \
  --exclude '.git' --exclude 'build' --exclude '__pycache__' \
  --exclude '.venv' --exclude '.pytest_cache' \
  --exclude 'VENDORED.md' \
  "$SRC/" "$DEST/"
rm -rf "$DEST/.pytest_cache"
echo "==> vendored head: $UPSTREAM_HEAD (was: $VENDORED_HEAD)"
echo "==> next: commit vendored/git-wt and reinstall git-wt if changed:"
echo "    pipx install --force $DEST"
