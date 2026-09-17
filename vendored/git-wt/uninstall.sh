#!/usr/bin/env bash
# uninstall.sh — remove git-wt
set -euo pipefail

if command -v pipx &>/dev/null; then
  echo "==> Uninstalling git-wt via pipx..."
  pipx uninstall git-wt 2>/dev/null || echo "  (not installed via pipx)"
elif command -v uv &>/dev/null; then
  echo "==> Uninstalling git-wt via uv..."
  uv tool uninstall git-wt 2>/dev/null || echo "  (not installed via uv)"
fi

rm -rf "${HOME}/.local/share/git-wt"

if command -v cli-hub &>/dev/null; then
  cli-hub unregister git-wt --yes || true
fi

echo "==> Done."