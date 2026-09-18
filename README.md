# harness

Launch AI agent harnesses in git-wt worktrees from issue/PR links.

## Introduction

`harness` sits above `git-wt`: it resolves a GitHub issue or PR/MR link into a
worktree (via `git-wt start`), records the issue↔PR↔worktree link, then execs
the configured AI harness (`omp` in v1) with the issue summary + description as
the initial prompt. `review` and `cleanup` close the loop.

## Install

```bash
./install.sh
```

Uses `uv tool install` when available, falls back to `pipx install`; writes
the install receipt (`~/.local/share/harness/install-receipt.json`),
bootstraps `git-wt` from the vendored snapshot when missing, registers with
cli-hub when present, and self-checks via `harness doctor`. Requires
`git-wt` and `omp` on PATH, plus `gh`/`glab` for tracker access.

While iterating from a checkout, use `uv tool install --force .` (or
`pipx install --force .`) to re-sync the installed copy after edits —
`harness doctor` tells you when it is stale.

## Uninstall

```bash
./uninstall.sh
```

Removes the tool (uv/pipx), the install receipt, and the cli-hub entry.
Config is left at `~/.config/harness/` for you to delete if unwanted.

## Usage

```bash
harness start https://github.com/OWNER/REPO/issues/22
harness start OWNER/REPO#22 --repo my-checkout --no-tty
harness review https://github.com/OWNER/REPO/pull/33
harness cleanup OWNER/REPO#22 --force --yes
harness repo add --name projectx --path ~/projects/projectx
harness repo list
harness status
harness doctor
```

`status` and `repo list` render Rich tables for humans; add `--csv` or
`--json` for scripting (stdout carries data only — logs go to stderr).

Tab completion (one system, never goes stale):

```bash
eval "$(harness completions show bash)"   # ~/.bashrc
eval "$(harness completions show zsh)"    # ~/.zshrc
harness completions show fish | source    # fish config
harness completions install               # or: install zsh --rcfile ~/.zshrc --yes
```

The tradeoff: the eval line spawns Python on every new shell (~200–400ms)
but never goes stale when commands change — the right default for this tier.

Exit codes: `0` success · `1` general error · `2` usage/needs human input.

State lives in `~/.config/harness/` (`config.json` registry, `links.json`
session links). Override with `HARNESS_CONFIG_DIR`.

## Vendored tooling

`vendored/git-wt/` carries the git-wt source snapshot (no nested `.git` — this
repo has exactly one `.git`, at the root). Canonical git-wt development stays
in `cli-agents-config/tools/git-wt`; see `vendored/git-wt/VENDORED.md`.
Install it with `pipx install ./vendored/git-wt`.

## Caveats

- Issue trackers: GitHub (`gh`), GitLab (`glab`), and Jira (`jira-cli`:
  `KEY-123` or `https://<host>/browse/KEY-123`). Jira URLs are configured
  via `jira-cli setup` (`~/.jira-cli.json`, `JIRA_URL`/`JIRA_USER`/`JIRA_PASS`).
- v1 supports the `omp` harness only; `ccline` comes later.
- `harness start` execs `omp` in TTY mode (replaces the process); use
  `--dry-run` to preview without launching, or `--no-tty` to run
  `omp -p <prompt>` non-interactively (extra harness flags after `--`).

## How It Works

1. `start <issue>` → resolve repo (`--repo` or cwd) → fetch title/body via
   `gh`/`glab`/`jira-cli` → `git-wt start --link/--issue` → record link → exec `omp`.
2. `review <PR>` → fetch head ref → worktree on that branch → exec `omp`
   with the pr-reviewer prompt.
3. `cleanup <ref>` → look up link → `git-wt cleanup --delete-branch` →
   close issue/PR → drop link.
