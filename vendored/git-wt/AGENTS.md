# git-wt — Agent context

## Build / Test

```bash
cd /home/bs/projects/personal/cli-agents-config/tools/git-wt
uv run pytest -v
```

Install locally:

```bash
pipx install --editable .
```

## Architecture

`git-wt` is a Python project with six modules:

| Module | Responsibility |
|--------|---------------|
| `src/git_wt/cli.py` | argparse dispatch — `start`/`finish`/`cleanup`/`list`/`completion`/`completion-install`/`doctor` subcommands |
| `src/git_wt/completions.py` | Shell completion: static bash/zsh/fish scripts, `$SHELL` detection, idempotent marker-block rc install (atomic write, `.bak`) |
| `src/git_wt/doctor.py` | Install-sync self-check: `install.sh` writes `~/.local/share/git-wt/install-receipt.json` (source hash); `doctor` compares it against the live tree; dev-run staleness warning. Keep hashing identical to install.sh |
| `src/git_wt/git_utils.py` | `GitRepo` class: repo queries, default-branch detection, dirty checks, subprocess helpers |
| `src/git_wt/worktree.py` | Worktree creation/removal, branch naming, PR/MR push via `gh`/`glab` |

### Key flows

**`git-wt start`** (no `--resume`):
1. Build branch name from `--branch` or `--type`/`--issue`/`--slug`
2. Detect default branch (`gh`/`glab` → `origin/HEAD` → error if neither)
3. Handle dirty base checkouts via `--on-dirty`
4. Fetch base, `git worktree add -b <branch> <wt_path> origin/<base>`
5. Return `{worktree_path, branch, base, action}`

**`git-wt start --resume <branch>`**:
1. Check if worktree already exists for that branch → return `{action: resumed}`
2. Otherwise fetch branch from origin and recreate worktree → `{action: recreated}`

**`git-wt finish`**:
1. Detect test runner (pom.xml → mvn, etc.)
2. Run tests (unless `--skip-tests`)
3. `git push -u origin <branch>`
4. `gh pr create --draft` or `glab mr create --draft`
5. Return `{branch, pushed, pr_url, tests_ran}`

**`git-wt cleanup`**:
1. Check PR state via `gh`/`glab` (skip if `--force`)
2. `git worktree remove`
3. Optionally `git branch -D` + `git push origin --delete`
4. Return `{actions: [...]}`

## Files to edit

- To change subcommand flags: `src/git_wt/cli.py` (keep `SUBCOMMAND_FLAGS` in `src/git_wt/completions.py` in sync — it drives the completion scripts)
- To change completion scripts / rc install: `src/git_wt/completions.py`
- To change git operations: `src/git_wt/git_utils.py`
- To change worktree/PR logic: `src/git_wt/worktree.py`
- To change install/receipt sync logic: `src/git_wt/doctor.py` (keep the hashing identical to the snippet in `install.sh`)
- Tests: `tests/` (mirrors the module structure)