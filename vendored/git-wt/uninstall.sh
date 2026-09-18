#!/usr/bin/env bash
# uninstall.sh — remove git-wt
set -euo pipefail

if command -v uv &>/dev/null && uv tool list 2>/dev/null | grep -q '^git-wt '; then
  echo "==> Uninstalling git-wt via uv..."
  uv tool uninstall git-wt || echo "  (not installed via uv)"
elif command -v pipx &>/dev/null && pipx list --short 2>/dev/null | grep -q 'git-wt'; then
  echo "==> uv tool not found; uninstalling via pipx..."
  pipx uninstall git-wt || echo "  (not installed via pipx)"
else
  echo "==> git-wt not found in uv tools or pipx (nothing to uninstall)."
fi

rm -rf "${HOME}/.local/share/git-wt"

if command -v cli-hub &>/dev/null; then
  cli-hub unregister git-wt --yes || true
fi

echo "==> Done."
