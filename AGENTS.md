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
| `src/harness/cli.py` | Typer app — `start`/`review`/`sync`/`cleanup`/`register`/`repo add\|list\|remove`/`link list\|set\|remove`/`status`/`cd`/`doctor`/`completions show\|install`; Rich tables/CSV/JSON output |
| `src/harness/refs.py` | issue/PR ref parsing (`OWNER/REPO#N`, Jira `KEY-123`/browse URLs, URLs, bare N) + `gh`/`glab`/`jira-cli` fetching |
| `src/harness/repos.py` | repo resolution (`--repo` name/path/URL, cwd, interactive pick), worktree→main-checkout resolution, default-branch detection, registry, `repo_names()` completion source, remote-host-aware gh/glab detection (`repos.<name>.tool` persisted in the registry) |
| `src/harness/pick.py` | shared arrow-key picker for every multi-choice prompt: stderr render, ↑/↓ + Enter, optional dim description per item, q/Esc aborts, `None` on non-TTY; testable via `read`/`stream` seams |
| `src/harness/trackers.py` | tracker↔repo relation: canonical ids (`jira:PREFIX`, `github:O/R`, `gitlab:host/g/r`), `check_or_record` guard, `resolve_for_tracker` repo picker, `link` command data |
| `src/harness/sync.py` | sync engine: dirty-file check, local merge (fetch + ff default + merge), sole-conflict `CHANGELOG.md` Unreleased auto-resolve (bullet union), push, remote rebase dispatch (`gh pr update-branch --rebase` / `glab mr rebase`) |
| `src/harness/webapp.py` | `serve` web backend (FastAPI; lazy import behind the `web` extra): Host/Origin/Content-Type guard, read endpoints reusing in-process helpers, run registry (`python -m harness` children, 409 per target key, ≤100 runs, ≤10k lines buffered), SSE with `Last-Event-ID` replay, SIGTERM→SIGKILL cancel, SPA catch-all |

### Key flows

**`harness register <path>`**: adopt an existing (unregistered) worktree by
path — must be a linked worktree (`--git-common-dir` ≠ `--absolute-git-dir`),
main checkouts and non-git dirs rejected (exit 2). Options: `--key`
(default `jira:<KEY>` derived from an issue-id segment in the branch name,
else `branch:<branch>`), `--issue REF` (parseable issue/PR ref; sets the
key when `--key` is omitted, stores `issue`/`issue_url`), `--repo` (main
checkout override; default from the worktree's git metadata), `--force`/
`--yes` (overwrite an existing link for the key), `--json`. Records via
`store.record_link` (status/sync/cd/cleanup all resolve it), persists the
tracker↔repo relation when `--issue` is given, and registers the repo in
the registry.

**`harness serve`**: `cli.serve` lazy-imports `webapp.run_server` (needs
the `web` extra: fastapi/uvicorn) → `create_app` guards Host/Origin/
Content-Type, read endpoints (`/api/status`, `/api/path`, `/api/repos`,
`/api/links`, `/api/doctor`) call the same in-process helpers as the CLI,
and mutating commands run as `sys.executable -m harness` children via
`backend.command_argv`-shaped argv (`--yes` on confirm, `--force` on
cleanup/register). Frontend: `web/` (Vite + React + TS), build →
`web/dist`, served by the SPA catch-all; API contract in
`web/API_CONTRACT.md`.

**`harness start <issue>`**: parse ref → `trackers.resolve_for_tracker`
(repo picker: `--repo`, cwd-linked silently, unlinked-cwd y/N with
fallthrough, or linked-repo pick/manual prompt; persists the tracker↔repo
relation) → default branch → fetch title/body → `git-wt start` → record
link → exec `omp` with prompt (`--no-tty` appends the commit/push/MR suffix
and uses `omp -p`).

**`harness review [PR]`**: parse ref → same tracker-aware repo picker →
fetch head ref → worktree on that branch → record `pr:<url>` link →
one-live-harness guard + record → exec `omp` with pr-reviewer prompt
(`--post-comments`/`--fix` append auto-comment/auto-fix prompt segments).
`review --all` (optional `--sequential`, `--fix`) reviews every
not-reviewed worktree's PR/MR in non-TTY `python -m harness review`
children — parallel by default, `--sequential` waits one at a time —
skipping reviewed-at-tip, no-PR, invalid-worktree, and live-harness
entries (stderr note each; zero reviewable → exit 0). A launched review
persists `reviewed`/`reviewed_at` (branch tip) on the PR link; the flag
auto-resets when the tip moves (new commit → reviewable again).

**`harness sync <ref>`**: resolve worktree key (fuzzy, shared with cleanup) →
dirty check (abort exit 1 naming files) → PR lookup (cached; no PR → local
merge fallback) → default: remote rebase via `gh`/`glab` (host-side), then
`sync_mod.pull_rebased` fast-forwards or patch-id-guard-resets the
worktree; rebase failure → automatic fallback to the `-m/--merge` local
merge (shared `_sync_local_merge`: auto-push to origin afterwards;
conflicts: CHANGELOG auto-union, else TTY prompt, `--harness` omp
interactively, `--yes`/`--force` omp non-interactively
`omp -p --auto-approve`; non-TTY without either exits 2) → `--all` =
every worktree with `--yes` semantics, continuing past failures;
`--dry-run`/`--json`; up-to-date is a no-op (exit 0).

**`harness cleanup <ref>`**: resolve link key (exact → parsed
key/URL → substring over keys/worktrees/branches; interactive pick on
ambiguity; miss = `no linked state` error) → confirm (unless
`--yes`/`--force`) → `git-wt cleanup --delete-branch` → close issue/PR
(already-closed PR/MR is not an error) → drop link.

## Conventions

- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Human-first: `status`/`repo list` render Rich tables; `--csv`/`--json` opt in
  for scripting/agents.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes`
  acts/fails with an actionable message.
- Completion: ONE system — `completions show <bash|zsh|fish>` (Click-generated,
  never hand-coded order) + `completions install [shell] [--rcfile] [--yes]`;
  dynamic values (repo names, refs — worktree keys/branches/worktree paths)
  via `autocompletion=` callbacks that filter on the `incomplete` prefix and
  return `[]` on any failure.
- `cd <ref>` prints the worktree root (stdout data-only); the shell
  functions `harness-cd` (bash/zsh from `cd_wrapper`, fish) shipped with
  `completions show|install` make it change the caller's directory — a
  child process cannot chdir its parent.
- Host CLI (gh/glab) for PR/MR ops: `repos._detect_host_cli` matches the
  repo's origin-URL host against gh's known hosts (`~/.config/gh/hosts.yml`,
  top-level keys) and glab's (`~/.config/glab-cli/config.yml`, keys under
  `hosts:` at 4-space indent); unknown hosts fall back to the auth-status
  probe. Persisted per repo as `repos.<name>.tool` (`register_repo` seeds
  it; `_repo_tool` in cli.py validates the stored `remote` and re-detects
  on change).
- Status caching (`_status_cells` in cli.py): per-branch cache in `pr_cache.json`
  (pr, tool, base_branch, branch/base tips, behind/ahead counts, checked_at);
  reused while both tips match and age < 3h — repeat `status` runs cost two
  `git rev-parse` calls per worktree; `--refresh-pr` re-queries the PR only.
  User content in Rich output is `rich.markup.escape`d (PR titles contain `[`).
  Detail panel (`status <ref>`) shows the full issue URL via
  `refs.issue_url(key, stored)` — jira site from jira-cli's config
  (`~/.config/jira-cli/config.json`, fallback `~/.jira-cli.json`), GitHub
  issues URL from the key; a stored `issue_url`/http `issue` wins.
- Status `harness` column (tables, `status <ref>` detail, `/api/status`,
  web table/detail): `"<name> <pid>"` while a harness is live on the
  worktree, `""` (`—` in Rich tables) otherwise — `_harness_cell` in
  cli.py over `store.active_harness` (cross-key worktree fallback
  mirrors `_guard_harness`).

## Files to edit

- Subcommand flags: `src/harness/cli.py` (Click generates completions; no flag lists to sync)
- Ref parsing/fetching: `src/harness/refs.py`
- Repo resolution: `src/harness/repos.py`
- Tracker↔repo guard/picker + `link` data: `src/harness/trackers.py`
- Sync engine: `src/harness/sync.py`
- PR-status cache (`pr_cache.json`): `src/harness/store.py`
- Completion internals (rc block, shell detect): `src/harness/completions.py`
- Web backend (`serve`): `src/harness/webapp.py`; frontend: `web/`
  (npm/tsc build per `web/package.json`)
- Tests: `tests/` (mirror module names)
