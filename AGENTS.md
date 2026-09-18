# harness — Agent context

Primary audience: Humans — pretty output by default; --csv/--json opt-in for scripting/agents.

Built with the `cli-app-generator` skill conventions: Typer + Rich app,
`completions show|install` (single completion system), `--json`/`--csv`
(stdout-only), `--dry-run` on destructive paths, `--yes`/`--force` to skip
confirmations, doctor install-sync check, `install.sh` / `uninstall.sh` +
cli-hub registration.

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
| `src/harness/cli.py` | Typer app — `start`/`review`/`cleanup`/`repo add\|list\|remove`/`status`/`doctor`/`completions show\|install`; Rich tables/CSV/JSON output |
| `src/harness/refs.py` | issue/PR ref parsing (`OWNER/REPO#N`, URLs, bare N) + `gh`/`glab`/`jira-cli` fetching |
| `src/harness/repos.py` | repo resolution (`--repo` name/path/URL, cwd, interactive pick), default-branch detection, registry, `repo_names()` completion source |
| `src/harness/gitwt.py` | `git-wt` subprocess wrapper (never imports git-wt) |
| `src/harness/backend.py` | `omp` launcher (exec in TTY, `-p` in `--no-tty`) + prompt builders |
| `src/harness/store.py` | `~/.config/harness/` persistence (`config.json`, `links.json`) |
| `src/harness/doctor.py` | install-sync self-check; keep hashing identical to `install.sh` |
| `src/harness/completions.py` | Click-generated `completions show` script + idempotent marker-block rc install; `autocompletion=` callbacks for repo names |
| `src/harness/errors.py` | `HarnessError` + `run_cmd` helper |

### Key flows

**`harness start <issue>`**: parse ref → resolve repo → default branch →
fetch title/body → `git-wt start` → record link → exec `omp` with prompt
(`--no-tty` appends the commit/push/MR suffix and uses `omp -p`).

**`harness review <PR>`**: parse ref → fetch head ref → worktree on that
branch → record `pr:<url>` link → exec `omp` with pr-reviewer prompt.

**`harness cleanup <ref>`**: look up link → confirm (unless `--yes`/`--force`)
→ `git-wt cleanup --delete-branch` → close issue/PR → drop link.

## Conventions

- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Human-first: `status`/`repo list` render Rich tables; `--csv`/`--json` opt in
  for scripting/agents.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes`
  acts/fails with an actionable message.
- Completion: ONE system — `completions show <bash|zsh|fish>` (Click-generated,
  never hand-coded order) + `completions install [shell] [--rcfile] [--yes]`;
  dynamic values (repo names) via `autocompletion=` callbacks that filter on
  the `incomplete` prefix and return `[]` on any failure.

## Files to edit

- Subcommand flags: `src/harness/cli.py` (Click generates completions; no flag lists to sync)
- Ref parsing/fetching: `src/harness/refs.py`
- Repo resolution: `src/harness/repos.py`
- Completion internals (rc block, shell detect): `src/harness/completions.py`
- Tests: `tests/` (mirror module names)
