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

Uses pipx when available, falls back to `uv tool install`; writes the install
receipt (`~/.local/share/harness/install-receipt.json`), registers with
cli-hub when present, and self-checks via `harness doctor`. Requires
`git-wt` and `omp` on PATH, plus `gh`/`glab` for tracker access.

## Uninstall

```bash
./uninstall.sh
```

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

State lives in `~/.config/harness/` (`config.json` registry, `links.json`
session links). Override with `HARNESS_CONFIG_DIR`.

## Caveats

- v1 supports GitHub (`gh`) and GitLab (`glab`) issues/PRs only — no Jira
  (`jira-cli` is deprecated), no Todoist-as-tracker.
- v1 supports the `omp` harness only; `ccline` comes later.
- `harness start` execs `omp` in TTY mode (replaces the process); use
  `--dry-run` to preview without launching.

## How it works

1. `start <issue>` → resolve repo (`--repo` or cwd) → fetch title/body via
   `gh`/`glab` → `git-wt start --link/--issue` → record link → exec `omp`.
2. `review <PR>` → fetch head ref → worktree on that branch → exec `omp`
   with the pr-reviewer prompt.
3. `cleanup <ref>` → look up link → `git-wt cleanup --delete-branch` →
   close issue/PR → drop link.
