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
- `start` from inside a linked worktree on a non-protected branch continues
  on that branch — no new issue-named branch is created (same rule when
  `--base` names a non-protected branch).
- AI-harness prompts now pin the push target: exact worktree and
  `push to origin/<branch>` — never create or push a different branch.
- Running `start`/`review` from a linked worktree resolves the repo to the
  main checkout (links and repo registry point at the main repo, not the
  worktree).
- `start`/`review` accept extra harness args after `--`; `doctor` checks `jira-cli`.
- Fix `start` in TTY mode: the harness now runs inside the new worktree
  (cwd changed before `execvp`) instead of staying in the launch directory.
- `-N` shorthand for `--no-harness` on `start` and `review`.
- `--base` completes local and remote-only git branches of the current
  directory (offline: reads the origin mirror, never the network; filtered
  by prefix; empty outside a repo).
- `--base <remote-only branch>` works: `start`/`review` materialize a local
  branch tracking `origin/<branch>` (fetch-free) before git-wt sees it;
  a branch missing locally and on the mirror fails with a `git fetch` hint.
- `start --no-harness`: skip launching the harness — print the exact harness
  command and replace the process with an interactive shell inside the
  worktree (non-TTY: print and exit). Same for `review --no-harness`.
- `start --base <branch>` naming a non-default branch (not `main`/`master`/
  `develop`/repo default) runs on that existing branch: worktree created for
  it via git-wt (naming unchanged), upstream `origin/<branch>`, no new branch.
  Default-base `--base` still creates a new feature branch as before.

## 0.1.0 — 2026-09-17

- Initial release: `start`, `review`, `cleanup`, `repo add/list/remove`,
  `status`, `doctor`, `completion`/`completion-install`.
- GitHub (`gh`) + GitLab (`glab`) issue/PR fetching; `omp` harness (v1 only).
- State in `~/.config/harness/`; `git-wt` via subprocess.
