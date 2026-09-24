#!/usr/bin/env bash
# uninstall.sh — reverse everything install.sh created.
set -euo pipefail

if command -v pipx &>/dev/null; then
  pipx uninstall workagent || true
elif command -v uv &>/dev/null; then
  uv tool uninstall workagent || true
fi

rm -rf "${HOME}/.local/share/workagent"

if command -v cli-hub &>/dev/null; then
  cli-hub unregister workagent --yes || true
fi

echo "==> workagent uninstalled. Config left at ~/.config/workagent (remove manually if wanted)."
