# Changelog
## 0.1.3 — 2026-09-18

- Fix Tab completion: register typer 0.27's completion classes in main() —
  the env-var server died with `Shell bash not supported.` otherwise.
- Silence server stderr in the sourced eval line; add a subprocess
  regression test covering the real completion protocol.

## 0.1.2 (2026-09-18)

- CLI migrated from argparse to Typer + Rich: Rich help panels, an
  `Example:` section per command, `-h/--help`, `--version` before or after
  the subcommand, and consistent exit codes (0 success, 1 error, 2 usage,
  130 interrupted).
- Completion rewritten around Click-generated scripts: `completions show
  <shell>` prints the init script, `completions install [shell] [--rcfile]
  [--yes]` installs it idempotently (marker block, atomic write, `.bak`
  backup). The hand-coded static scripts and `SUBCOMMAND_FLAGS` table are
  gone; branch names and worktree paths complete via `autocompletion=`
  callbacks (local git state only).
- Human-first / agent-heavy output: `list` renders a Rich table by default
  with `--csv` and `--json` opt-ins; stdout is data-only; prompts and
  warnings go to stderr; every interactive prompt has a non-interactive
  bypass and never blocks non-TTY stdin (actionable failure instead).
- `install.sh` prefers uv over pipx, derives the registered `--version`
  from `pyproject.toml`, and passes `--config-path ~/.config/git-wt` to
  cli-hub; `uninstall.sh` mirrors the uv-first detection.
- Docs: README section order per contract (Introduction → Install →
  Uninstall → Usage → Caveats → How It Works), `completions show|install`
  and `--csv`/`--json` documented; AGENTS.md audience line + skill
  reference + updated module map.

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
