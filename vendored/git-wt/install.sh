#!/usr/bin/env bash
# install.sh — install git-wt via pipx (or uv if pipx missing)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v pipx &>/dev/null; then
  echo "==> Installing git-wt via pipx..."
  pipx install --force "$DIR"
elif command -v uv &>/dev/null; then
  echo "==> pipx not found; installing via uv..."
  uv tool install --force "$DIR"
else
  echo "error: need pipx or uv (https://pipx.pypa.io)"
  exit 1
fi

# Write install receipt (source hash) so `git-wt doctor` can detect stale
# installs. Keep the hashing identical to src/git_wt/doctor.py.
python3 -c "
import hashlib, json, datetime
from pathlib import Path
pkg = Path('$DIR') / 'src' / 'git_wt'
d = hashlib.sha256()
for f in sorted(pkg.rglob('*.py')):
    if '.venv' in f.parts:
        continue
    d.update(f.relative_to(pkg).as_posix().encode())
    d.update(f.read_bytes())
receipt = Path.home() / '.local' / 'share' / 'git-wt' / 'install-receipt.json'
receipt.parent.mkdir(parents=True, exist_ok=True)
receipt.write_text(json.dumps({'source_hash': d.hexdigest()[:12], 'installed_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'source_dir': '$DIR'}, indent=2))
print('  -> install receipt:', d.hexdigest()[:12])
"

# Verify installation (and the receipt self-check).
echo "==> Verifying..."
if command -v git-wt &>/dev/null; then
  git-wt --version
  git-wt doctor || echo "  ! doctor reports stale/missing receipt (see above)."
else
  echo "  ✗ git-wt not found in PATH after install."
  echo "    Ensure ~/.local/bin is in your PATH, then re-login or:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi


if command -v cli-hub &>/dev/null; then
  REPO="$(git -C "$DIR" remote get-url origin 2>/dev/null || true)"
  cli-hub register git-wt \
    --version "0.1.0" \
    --description "Create/finish/cleanup git worktrees for AI agent task isolation" \
    --group "git" \
    --source-path "$DIR" \
    ${REPO:+--repo "$REPO"} \
    --uninstall "$DIR/uninstall.sh" \
    --reinstall "$DIR/install.sh" \
    --yes || true
fi

echo "==> Done. Run: git-wt --help"