# git-wt — Agent context

Primary audience: Humans — pretty output by default; heavily agent-driven: `--csv`/`--json` and non-interactive flags are opt-in.

Built and maintained with the `cli-app-generator` skill conventions (Typer + Rich, one completion system via `completions show|install`, stdout data-only, exit codes 0/1/2/130).

## Build / Test

```bash
cd /home/bs/projects/personal/cli-agents-config/tools/git-wt
uv run pytest -v
uv run git-wt --help
```

Install locally (do NOT run install.sh/uninstall.sh during development):

```bash
uv tool install --force .
# or: pipx install --force .
```

## Architecture

`git-wt` is a Python project (Typer + Rich) with five modules:

| Module | Responsibility |
|--------|---------------|
| `src/git_wt/cli.py` | Typer app + Rich help (`Example:` sections) — `start`/`finish`/`cleanup`/`list`/`completions`/`doctor`; global flags `-v/-q/--json/--csv/--version` accepted before and after the subcommand; stdout data-only, prompts/errors on stderr |
| `src/git_wt/completions.py` | Shell completion: Click-generated scripts (`completions show`), `$SHELL` detection, idempotent marker-block rc install (atomic write, `.bak`), `autocompletion=` callbacks (branch names, worktree paths — local git state only, never raise) |
| `src/git_wt/doctor.py` | Install-sync self-check: `install.sh` writes `~/.local/share/git-wt/install-receipt.json` (source hash); `doctor` compares it against the live tree; dev-run staleness warning. Keep hashing identical to install.sh |
| `src/git_wt/git_utils.py` | `GitRepo` class: repo queries, default-branch detection, dirty checks, subprocess helpers |
| `src/git_wt/worktree.py` | Worktree creation/removal, branch naming, PR/MR push via `gh`/`glab`, PR-state gate with TTY confirm / `--force`/`--yes` bypass |

### Key flows

**`git-wt start`** (no `--resume`):
1. Build branch name from `--branch` or `--type`/`--issue`/`--slug`
2. Detect default branch (`gh`/`glab` → `origin/HEAD` → error if neither)
3. Handle dirty base checkouts via `--on-dirty` (non-TTY: actionable failure)
4. Fetch base, `git worktree add -b <branch> <wt_path> origin/<base>`
5. Return `{worktree_path, branch, base, action}`

**`git-wt start --resume <branch>`** (bare `--resume` = interactive pick, TTY only):
1. Check if worktree already exists for that branch → return `{action: resumed}`
2. Otherwise fetch branch from origin and recreate worktree → `{action: recreated}`

**`git-wt finish`**:
1. Detect test runner (pom.xml → mvn, etc.)
2. Run tests (unless `--skip-tests`)
3. `git push -u origin <branch>`
4. `gh pr create --draft` or `glab mr create --draft`
5. Return `{branch, pushed, pr_url, tests_ran}`

**`git-wt cleanup`**:
1. Check PR state via `gh`/`glab` — on a TTY, confirm before removing an open-PR worktree; `--force`/`--yes` bypass; non-TTY without a bypass fails with an actionable message
2. `git worktree remove`
3. Optionally `git branch -D` + `git push origin --delete`
4. Return `{actions: [...]}`

## Files to edit

- To change subcommand flags/behavior or output format: `src/git_wt/cli.py` (completion order is generated from the app — nothing to keep in sync)
- To change completion scripts / rc install / value callbacks: `src/git_wt/completions.py`
- To change git operations: `src/git_wt/git_utils.py`
- To change worktree/PR logic: `src/git_wt/worktree.py`
- To change install/receipt sync logic: `src/git_wt/doctor.py` (keep the hashing identical to the snippet in `install.sh`)
- Tests: `tests/` (mirrors the module structure)
