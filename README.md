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
harness sync IPG-929          # or OWNER/REPO#22, or a worktree key
harness repo add --name projectx --path ~/projects/projectx
harness repo list
harness link list
harness link set jira:IPG ~/projects/projectx
harness status                # or: status IPG-929 for a detail panel
harness doctor
```

`status` and `repo list` render Rich tables for humans; add `--csv` or
`--json` for scripting (stdout carries data only — logs go to stderr).

## Tracker↔repo links

Each issue tracker project (`jira:IPG`, `github:OWNER/REPO`,
`gitlab:host/group/repo`) is linked to one or more checkouts on first use;
the mapping persists in `config.json` and prevents starting work for one
project in the wrong repo. `start`/`review` without `--repo` pick the repo
as follows:

1. cwd inside a linked repo (a linked worktree resolves to its main
   checkout) → used silently.
2. cwd inside an unlinked repo → `use cwd anyway? [y/N]`; **No** falls
   through to the linked-repo flow below.
3. cwd outside any repo →
   - one linked repo: used silently (stderr note);
   - multiple: arrow-key interactive pick (shared `pick` module);
   - none: type a repo name/path/URL (non-interactive runs abort and ask
     for `--repo`).

The selected repo is linked to the tracker project and used for the
worktree. Manage mappings with `harness link list|set|remove`; pass `--yes`
to accept prompts non-interactively.

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
worktree links, `pr_cache.json` PR-status cache). Override with
`HARNESS_CONFIG_DIR`.

## Registering existing worktrees

Worktrees created outside `harness start` (plain `git worktree add`,
`git-wt start` run by hand) can be adopted:

```bash
harness register ~/dev/worktrees/projectx/feat/IPG-999--fix-thing
harness register ~/dev/wt/feat/x --issue IPG-999      # attach an issue/PR ref
harness register ~/dev/wt/feat/x --key jira:IPG-999 --repo ~/dev/projectx
```

The path must be a linked git worktree (not the main checkout). The
worktree key defaults to `jira:<KEY>` derived from the branch name (an
`IPG-123`-style segment), else `branch:<branch>`; `--issue` overrides
it and attaches the issue/PR (URL included, so `status` shows it).
`--key` names the link explicitly; `--repo` overrides the auto-detected
main checkout; `--force` (`--yes`) overwrites an existing link for the
same key. Registered worktrees work with `status`, `sync`, `cd`, and
`cleanup` like any other worktree.
`HARNESS_CONFIG_DIR`.

## Sync

`harness sync <ref>` brings a worktree branch up to date with its base
branch. Refs resolve fuzzily (worktree key, issue number, branch/worktree
substring) like `cleanup`; no ref picks interactively (or `--all` for
every worktree, confirmed one by one).

- Default: remote rebase — `gh pr update-branch --rebase` or
  `glab mr rebase`; the host merges server-side, then the worktree is
  updated: fast-forward, or a `git cherry`-guarded hard reset when the
  rebase rewrote history (skipped when local commits were never
  pushed). If the server rebase fails, the local-merge flow below runs
  automatically.
- `-m/--merge`: fetch, fast-forward the local default branch, merge it
  into the worktree branch inside the worktree, then push the branch to
  origin (also after harness-resolved conflicts).
- Any other conflict lists the files; `--harness` (or `--yes`/`--force`)
  launches the coding agent to resolve and push — `--yes` runs it
  non-interactively (`omp -p --auto-approve`). `--all` syncs every
  worktree with the same auto-harness behavior and continues with the
  remaining worktrees when one fails.
- Dirty worktrees abort (exit `1`); `--dry-run` previews; `--json` for
  scripting.

## Status

`harness status` shows every linked worktree with the branch, the `commits`
column (`behind|ahead` vs the remote-tracking base branch — no fetch;
`gone` when the worktree is missing), and the latest PR/MR with a bright
color for the state (open/merged/closed). `status <ref>` shows a detail
panel (worktree, issue URL, PR title/author/URL, behind/ahead counts); both
modes support `--json`. `--worktree` restores the worktree-path column.
The issue URL is built from jira-cli's configured site (`<site>/browse/KEY`)
or the GitHub repo in the worktree key; a stored URL wins.
The PR/MR lookup CLI (gh/glab) is chosen from the repo's origin URL host
(gh's known hosts vs glab's) and persisted per registered repo, so GitLab
repos never query GitHub. Status is cached per branch in
`~/.config/harness/pr_cache.json` and reused while the worktree-branch tip
and the base-branch tip are unchanged and the entry is younger than 3h —
`--refresh-pr` re-queries the PR/MR. When a worktree has no PR/MR, the
`status <ref>` detail shows a create hint: the web create-PR URL for
GitHub remotes, otherwise a `glab mr create` command for the branch.

## cd

`harness cd <ref>` prints the linked worktree path — use it as
`cd "$(harness cd IPG-959)"`. `completions show|install` also provides a
`harness-cd` shell function, so after installing completions
`harness-cd IPG-959` changes directory directly.

Refs (`status`, `sync`, `review`, `cleanup`, `cd`) complete in the shell
over worktree keys, branches, and worktree names.


## Web UI (`harness serve`)

`harness serve` starts a local web server that mirrors the CLI: worktree
table with branch/commits/PR/CI state, launch (`start`/`review`),
repos, tracker↔repo links, sync, cleanup, register, runs with live logs,
and doctor.

```bash
pip install 'harness[web]'            # or: uv tool install --force --with fastapi --with uvicorn .
cd web && npm ci && npm run build     # build the UI (needed once)
harness serve                         # http://127.0.0.1:3344
harness serve --port 3345 --allowed-host devbox.local
```

- Run logs stream over SSE; destructive runs need an explicit
  confirmation (opt-out per action type is stored in the browser, never
  for `--force`).
- The built-in static path is `<repo>/web/dist` (correct for
  editable/source installs). For non-editable installs pass
  `--static-dir /path/to/harness/web/dist`.
- One run per target at a time: a second launch for the same worktree
  returns 409; cancel sends SIGTERM, then SIGKILL after 10 s.
- **No authentication.** The server binds to `127.0.0.1` by default and
  refuses unexpected `Host` headers plus non-same-origin JSON posts.
  Binding `--host 0.0.0.0` exposes an unauthenticated agent runner to
  the network — don't, unless you have another isolation layer.
- Off-host development: run the Vite dev server (`cd web && npm run
  dev`) and add your origin host with `--allowed-host`.

Details: `web/API_CONTRACT.md` (HTTP/SSE API), `web/FEATURE_INVENTORY.md`


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

1. `start <issue>` → resolve repo (picker below, or `--repo`) → fetch
   title/body via `gh`/`glab`/`jira-cli` → `git-wt start --link/--issue` →
2. `review <PR>` → fetch head ref → worktree on that branch → exec `omp`
   with the pr-reviewer prompt. A worktree key/issue ref resolves to that
   worktree's recorded PR/MR.
3. `sync <ref>` → rebase the worktree's PR remotely (default) or merge the
   default branch locally (`--merge`, push with `--push`); see "Sync".
4. `cleanup <ref>` → look up link (exact key, else substring match over
   keys/worktrees/branches; interactive pick on ambiguity) →
   `git-wt cleanup --delete-branch` → close issue/PR (an already-closed
   PR/MR is not an error) → drop link.
