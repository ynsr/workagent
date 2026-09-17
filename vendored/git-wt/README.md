# git-wt

Create, finish, and clean up git worktrees for AI agent task isolation.

## Install

```bash
./install.sh
```

Uses pipx when available, falls back to `uv tool install`; refreshes the
install receipt (`~/.local/share/git-wt/install-receipt.json`), registers with
[cli-hub](https://github.com/ynsr/cli-agents-config/tree/main/tools/cli-hub)
when present, and self-checks via `git-wt doctor`. Requires Python 3.10+.
Also works via `uv run`:

```bash
uv run git-wt --help
```

## Uninstall

```bash
./uninstall.sh
```

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

### List worktrees

```bash
git-wt list
git-wt list --json
```

### Shell completion

Try it in the current shell:

```bash
eval "$(git-wt completion bash)"   # or zsh
git-wt completion fish | source    # fish
```

Install it permanently (idempotent; edits `~/.bashrc` / `~/.zshrc` /
`~/.config/fish/config.fish` inside a marker block, keeps a `.bak` backup):

```bash
git-wt completion-install            # shell detected from $SHELL
git-wt completion-install zsh --rcfile ~/.zshrc --yes
```

## How it works

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
| `list` | List worktrees for a repo |
| `doctor` | Verify the installed copy is in sync with the source tree (exit 1 = stale/missing receipt) |
| `completion <bash\|zsh\|fish>` | Print the completion script to stdout |
| `completion-install [shell]` | Install completion into your rc file (`--rcfile`, `--yes`) |

## Exit codes

- `0` — success
- `1` — error (git failure, bad args, etc.)
- `2` — needs human input / usage error (e.g. unknown shell, missing --base)
- `130` — interrupted (Ctrl-C)

## Global flags

- `-v` / `--verbose` — extra detail on stderr (also valid after the subcommand)
- `-q` / `--quiet` — suppress non-essential stderr output
- `--json` — structured output on stdout (also valid after the subcommand)
- `--version` — print version and exit