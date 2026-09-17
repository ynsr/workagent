# harness — Agent context

Built with the `cli-app-generator` skill conventions: argparse dispatch,
`--json` (stdout-only), `--dry-run` on destructive paths, `doctor`
install-sync check, `completion`/`completion-install`, `install.sh` /
`uninstall.sh` + cli-hub registration.

## Build / Test

```bash
cd /home/bs/projects/personal/harness
uv run pytest -v
```

Install locally:

```bash
./install.sh
```

## Architecture

| Module | Responsibility |
|--------|---------------|
| `src/harness/cli.py` | argparse dispatch — `start`/`review`/`cleanup`/`repo`/`status`/`doctor`/`completion` |
| `src/harness/refs.py` | issue/PR ref parsing (`OWNER/REPO#N`, URLs, bare N) + `gh`/`glab` fetching |
| `src/harness/repos.py` | repo resolution (`--repo` name/path/URL, cwd, interactive pick), default-branch detection, registry |
| `src/harness/gitwt.py` | `git-wt` subprocess wrapper (never imports git-wt) |
| `src/harness/backend.py` | `omp` launcher (exec in TTY, `-p` in `--no-tty`) + prompt builders |
| `src/harness/store.py` | `~/.config/harness/` persistence (`config.json`, `links.json`) |
| `src/harness/doctor.py` | install-sync self-check; keep hashing identical to `install.sh` |
| `src/harness/completions.py` | static bash/zsh/fish scripts + idempotent rc install |
| `src/harness/errors.py` | `HarnessError` + `run_cmd` helper |

### Key flows

**`harness start <issue>`**: parse ref → resolve repo → default branch →
fetch title/body → `git-wt start` → record link → exec `omp` with prompt
(`--no-tty` appends the commit/push/MR suffix and uses `omp -p`).

**`harness review <PR>`**: parse ref → fetch head ref → worktree on that
branch → record `pr:<url>` link → exec `omp` with pr-reviewer prompt.

**`harness cleanup <ref>`**: look up link → confirm (unless `--yes`/`--force`)
→ `git-wt cleanup --delete-branch` → close issue/PR → drop link.

## Files to edit

- Subcommand flags: `src/harness/cli.py` (keep `src/harness/completions.py` flag lists in sync)
- Ref parsing/fetching: `src/harness/refs.py`
- Repo resolution: `src/harness/repos.py`
- Tests: `tests/` (mirror module names)
