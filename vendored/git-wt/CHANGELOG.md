# Changelog

## 0.1.1 (2026-09-17)

- `install.sh`: derives `UNINSTALL`/`REINSTALL` from the backend actually used
  (pipx or uv) and registers those exact commands with cli-hub; writes an
  install receipt (`~/.local/share/git-wt/install-receipt.json`); verifies
  with `git-wt doctor`.
- New `git-wt doctor` subcommand: exit 0 when the installed copy matches the
  install receipt, exit 1 with the fix command when stale/missing.
- Dev-run staleness warning on stderr when running from the source tree while
  the installed snapshot differs (never from the installed copy itself).
- `uninstall.sh` also removes the shared data dir (install receipt).
- Docs: README install/uninstall/doctor; AGENTS.md module map.
