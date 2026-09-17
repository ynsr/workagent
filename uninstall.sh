#!/usr/bin/env bash
# uninstall.sh — reverse everything install.sh created.
set -euo pipefail

if command -v pipx &>/dev/null; then
  pipx uninstall harness || true
elif command -v uv &>/dev/null; then
  uv tool uninstall harness || true
fi

rm -rf "${HOME}/.local/share/harness"

if command -v cli-hub &>/dev/null; then
  cli-hub unregister harness --yes || true
fi

echo "==> harness uninstalled. Config left at ~/.config/harness (remove manually if wanted)."
