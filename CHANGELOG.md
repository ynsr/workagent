# Changelog
## 0.2.1 — 2026-09-18

- Fix Tab completion: typer 0.27 never registers its shell completion
  classes for the env-var server path, so every completion attempt died
  with `Shell bash not supported.` — cli.main() now registers them.
- Silence completion-server stderr inside the sourced eval line; a failing
  server degrades to no candidates instead of printing into the shell.
- Add a subprocess regression test covering the real completion protocol.

## 0.2.0 — 2026-09-18

- Typer + Rich migration: `--help` with real examples and documented exit
  codes; Rich tables for `status`/`repo list` with `--csv`/`--json` opt-in.
- One completion system: `completions show <bash|zsh|fish>` (Click-generated)
  + `completions install [shell] [--rcfile] [--yes]` (idempotent marker block,
  atomic write, .bak, stale-block replace); replaces `completion`/
  `completion-install`. Repo names complete on `--repo` and `repo remove`.
- `install.sh` installs harness itself (uv preferred, pipx fallback) and
  derives the cli-hub `--version` from `pyproject.toml`.
- Fixed `review` reading `--dry-run` without defining the flag.

## Unreleased

- Jira support: `KEY-123` / `<host>/browse/KEY-123` refs via `jira-cli`.
- Resilient `start`: existing branch/worktree resumes instead of failing;
  upstream verified to point at the feature branch, never the base.
- `start`/`review` accept extra harness args after `--`; `doctor` checks `jira-cli`.

## 0.1.0 — 2026-09-17

- Initial release: `start`, `review`, `cleanup`, `repo add/list/remove`,
  `status`, `doctor`, `completion`/`completion-install`.
- GitHub (`gh`) + GitLab (`glab`) issue/PR fetching; `omp` harness (v1 only).
- State in `~/.config/harness/`; `git-wt` via subprocess.
