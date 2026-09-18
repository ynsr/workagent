# git-wt

Create, finish, and clean up git worktrees for AI agent task isolation.

## Introduction

git-wt is human-first by default and heavily agent-driven when you opt in.
Interactive output is pretty: `list` renders a Rich table, progress and
confirmations go to stderr, and stdout carries only command output — so any
command is pipe-safe. For scripts and agents, `--csv` / `--json` switch
`list` (and structured commands) to machine-readable output, and every
interactive prompt has a non-interactive bypass flag. When stdin is not a
TTY, git-wt never blocks: it fails fast with an actionable message naming
the flag to pass.

## Install

```bash
./install.sh
```

Installs via `uv tool install` (preferred) or pipx as a fallback; refreshes
the install receipt (`~/.local/share/git-wt/install-receipt.json`), registers
with [cli-hub](https://github.com/ynsr/cli-agents-config/tree/main/tools/cli-hub)
when present, and self-checks via `git-wt doctor`. Requires Python 3.10+.
Also works without installing:

```bash
uv run git-wt --help
```

## Uninstall

```bash
./uninstall.sh
```

Removes the tool and the shared data dir (`~/.local/share/git-wt`), and
unregisters from cli-hub when present.

## Usage

### Start a new task

```bash
cd ~/projects/my-repo
git-wt start --branch feat/42-add-login
# → Created worktree at: ~/dev/worktrees/my-repo/feat/42-add-login
```

Or use the branch builder:

```bash
git-wt start --type feat --issue 42 --slug add-login --base main
```

Ephemeral (temp dir, cleaned up manually):

```bash
git-wt start --branch feat/42-add-login --ephemeral
```

Resume existing work:

```bash
git-wt start --resume feat/42-add-login
```

### Finish a task (push + draft PR/MR)

```bash
cd ~/dev/worktrees/my-repo/feat/42-add-login
git-wt finish
```

Or pass the worktree path directly:

```bash
git-wt finish --worktree ~/dev/worktrees/my-repo/feat/42-add-login --skip-tests
```

### Clean up

```bash
git-wt cleanup --branch feat/42-add-login --delete-branch
```

On a TTY, `cleanup` confirms before removing a worktree whose PR is still
open. Non-interactively, pass `--force` (or `--yes`) — without a bypass the
command fails with an actionable message instead of prompting.

### List worktrees

```bash
git-wt list          # Rich table (human-readable)
git-wt list --csv    # header row: branch,path
git-wt list --json | jq '.[].branch'
```

### Shell completion

Try it in the current shell:

```bash
eval "$(git-wt completions show bash)"   # or zsh
git-wt completions show fish | source    # fish
```

Install it permanently (idempotent; edits `~/.bashrc` / `~/.zshrc` /
`~/.config/fish/config.fish` inside a marker block, keeps a `.bak` backup):

```bash
git-wt completions install            # shell detected from $SHELL
git-wt completions install zsh --rcfile ~/.zshrc --yes
```

Branch names and worktree paths complete for `--branch`/`--resume` and
`--worktree` (local git state only; offline).

### Doctor

```bash
git-wt doctor
git-wt doctor --json
```

### Global flags

- `-v` / `--verbose` — extra detail on stderr (also valid after the subcommand)
- `-q` / `--quiet` — suppress non-essential stderr output
- `--json` — structured output on stdout (also valid after the subcommand)
- `--csv` — CSV output instead of the pretty `list` table
- `--version` — print version and exit (before or after the subcommand)
- `-h` / `--help` — Rich help with a `Example:` section per command

## Caveats

- Commands must run inside a git repo; the default branch is detected via
  `gh`/`glab`, then `origin/HEAD`; supply `--base` when neither works.
- Bare `--resume` (interactive picker) needs a TTY; non-TTY runs must pass a
  branch value.
- `cleanup` refuses to remove a worktree whose PR/MR is still open unless
  `--force` (or `--yes`) is passed; non-TTY runs without a bypass fail with
  an actionable message.
- Running from the source tree while the installed copy is stale prints a
  warning on stderr (never from the installed copy itself); fix with
  `./install.sh`.

## How It Works

1. `git-wt start` creates a git worktree at `~/dev/worktrees/<repo>/<branch>` (or a temp dir with `--ephemeral`) and a new branch based off the repo's default branch.
2. You work in the worktree, making milestone commits.
3. `git-wt finish` runs the test suite (auto-detects Maven/Gradle/Go/Cargo/pytest/npm), pushes the branch, and opens a draft PR/MR via `gh` or `glab`.
4. `git-wt cleanup` removes the worktree and optionally deletes the local/remote branch.

## Commands

| Command | Description |
|---------|-------------|
| `start` | Create (or resume) a worktree + branch |
| `finish` | Push branch and create draft PR/MR |
| `cleanup` | Remove worktree and optionally delete branch |
| `list` | List worktrees for a repo (`--csv`/`--json`) |
| `doctor` | Verify the installed copy is in sync with the source tree (exit 1 = stale/missing receipt) |
| `completions show <bash\|zsh\|fish>` | Print the shell init script to stdout |
| `completions install [shell]` | Install completion into your rc file (`--rcfile`, `--yes`) |

## Exit codes

- `0` — success
- `1` — error (git failure, open PR, stale/missing install receipt)
- `2` — usage error (bad flags, missing input, unknown shell)
- `130` — interrupted (Ctrl-C)
