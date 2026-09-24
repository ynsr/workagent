#!/usr/bin/env bash
# install.sh — install workagent via uv (or pipx if uv missing)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Version from pyproject.toml (single source of truth; also passed to cli-hub).
VERSION="$(python3 -c "import tomllib, pathlib, sys
try:
    print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())['project']['version'])
except Exception:
    print('')
" "$DIR/pyproject.toml")"
if [ -z "$VERSION" ]; then
  echo "error: could not read version from pyproject.toml"
  exit 1
fi
echo "==> Installing harness ${VERSION}..."

# Bootstrap git-wt from the vendored snapshot when it's missing from PATH.
if ! command -v git-wt &>/dev/null; then
  if [ -d "$DIR/vendored/git-wt" ]; then
    echo "==> git-wt not found; installing from vendored/git-wt..."
    if command -v uv &>/dev/null; then
      uv tool install --force "$DIR/vendored/git-wt"
    elif command -v pipx &>/dev/null; then
      pipx install --force "$DIR/vendored/git-wt"
    else
      echo "error: need pipx or uv to install vendored/git-wt"
      exit 1
    fi
  else
    echo "warning: git-wt not on PATH and vendored/git-wt missing."
    echo "         workagent start/review need it: install git-wt first."
  fi
fi

# Install harness itself (canonical backend: uv preferred, pipx fallback),
# including the optional `web` extra so `workagent serve` works out of the box.
if command -v uv &>/dev/null; then
  echo "==> Installing via uv (with web extra)..."
  uv tool install --force --with fastapi --with "uvicorn[standard]" "$DIR"
elif command -v pipx &>/dev/null; then
  echo "==> uv not found; installing via pipx (with web extra)..."
  pipx install --force "$DIR" --system-site-packages 2>/dev/null \
    || pipx install --force "$DIR"
  pipx inject workagent fastapi "uvicorn[standard]" 2>/dev/null || true
else
  echo "error: need uv (https://docs.astral.sh/uv/) or pipx (https://pipx.pypa.io)"
  exit 1
fi

# Build the web UI (served by `workagent serve` from <repo>/web/dist).
if [ -d "$DIR/web" ]; then
  if command -v npm &>/dev/null; then
    echo "==> Building web UI..."
    (cd "$DIR/web" && npm ci --no-fund --no-audit && npm run build) \
      || echo "warning: web UI build failed — 'workagent serve' will refuse to start until 'cd web && npm ci && npm run build' succeeds."
  else
    echo "warning: npm not found — web UI not built; install Node.js and run 'cd web && npm ci && npm run build' to enable 'workagent serve'."
  fi
fi

# Write install receipt (source hash) so `workagent doctor` can detect stale
# installs. Keep the hashing identical to src/workagent/doctor.py.
python3 -c "
import hashlib, json, datetime
from pathlib import Path
pkg = Path('$DIR') / 'src' / 'workagent'
d = hashlib.sha256()
for f in sorted(pkg.rglob('*.py')):
    if '.venv' in f.parts:
        continue
    d.update(f.relative_to(pkg).as_posix().encode())
    d.update(f.read_bytes())
receipt = Path.home() / '.local' / 'share' / 'workagent' / 'install-receipt.json'
receipt.parent.mkdir(parents=True, exist_ok=True)
receipt.write_text(json.dumps({'source_hash': d.hexdigest()[:12], 'installed_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'source_dir': '$DIR'}, indent=2))
print('  -> install receipt:', d.hexdigest()[:12])
"

# Verify installation (and the receipt self-check).
echo "==> Verifying..."
if command -v workagent &>/dev/null; then
  workagent --version
  workagent doctor || echo "  ! doctor reports stale/missing receipt (see above)."
else
  echo "  ✗ workagent not found in PATH after install."
  echo "    Ensure ~/.local/bin is in your PATH, then re-login or:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

if command -v cli-hub &>/dev/null; then
  REPO="$(git -C "$DIR" remote get-url origin 2>/dev/null || true)"
  cli-hub register workagent \
    --version "$VERSION" \
    --description "Launch AI agent harnesses in git-wt worktrees from issue/PR links" \
    --group "git" \
    --source-path "$DIR" \
    ${REPO:+--repo "$REPO"} \
    --config-path "${HOME}/.config/workagent" \
    --uninstall "$DIR/uninstall.sh" \
    --reinstall "$DIR/install.sh" \
    --yes || true
fi

echo "==> Done. Run: workagent --help"
