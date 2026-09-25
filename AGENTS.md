# workagent — Agent context

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
| `src/workagent/cli.py` | Typer app — `start`/`review`(+`--all`/`--sequential`/`--fix`)/`sync`/`cleanup`(+`--merged`)/`register`/`repo add\|list\|remove`/`link list\|set\|remove`/`status`/`candidates`/`cd`/`open`/`doctor`/`serve`/`completions show\|install`; Rich tables/CSV/JSON output; `status`/`link list` tables carry branch/commits/PR/`ci` (success\|failure\|running\|not_started; ✓/✗/● rendered, `-` when none) + `wt_valid` in `--json` |
| `src/workagent/refs.py` | issue/PR ref parsing (`OWNER/REPO#N`, Jira `KEY-123`/browse URLs, URLs, bare N) + `gh`/`glab`/`jira-cli` fetching; `fetch_open_prs` (open PR/MR rows) + `fetch_ci_status` (soft-fail CI lookup, None + stderr warning); recorded `pr_url` seeds the PR cell when cache/query find none; GitLab matches render `MR #N` |
| `src/workagent/repos.py` | repo resolution (`--repo` name/path/URL, cwd, interactive pick), worktree→main-checkout resolution, default-branch detection, registry, `repo_names()` completion source, remote-host-aware gh/glab detection (`repos.<name>.tool` persisted in the registry) |
| `src/workagent/pick.py` | shared arrow-key picker for every multi-choice prompt: stderr render, ↑/↓ + j/k + Enter, optional dim description per item, q/Esc aborts, `None` on non-TTY; raw-mode-safe `\r\n` terminators (no staircase); testable via `read`/`stream` seams |
| `src/workagent/trackers.py` | tracker↔repo relation: canonical ids (`jira:PREFIX`, `github:O/R`, `gitlab:host/g/r`), `check_or_record` guard, `resolve_for_tracker` repo picker, `link` command data; `list_my_issues` (jira `_MY_ISSUES_JQL` reporter=me To Do/In Progress last 2 months + gh authored-by-me; never raises, appends warnings) |
| `src/workagent/sync.py` | sync engine: dirty-file check, local merge (fetch + ff default + merge), sole-conflict `CHANGELOG.md` Unreleased auto-resolve (bullet union), push, remote rebase dispatch (`gh pr update-branch --rebase` / `glab mr rebase`) |
| `src/workagent/webapp.py` | `serve` web backend (FastAPI; lazy import behind the `web` extra): Host/Origin/Content-Type guard, read endpoints reusing in-process helpers (`/api/status`, `/api/path`, `/api/repos`, `/api/links` with `worktrees` key, `/api/doctor`, `/api/issues`, `/api/candidates`), run registry (`python -m workagent` children, 409 per target key — `open:<ref>`, `review:all`, `cleanup:all`, `sync:all` included — ≤100 runs, ≤10k lines buffered), SSE with `Last-Event-ID` replay, SIGTERM→SIGKILL cancel, SPA catch-all |

### Key flows

**`workagent register <path>`**: adopt an existing (unregistered) worktree by
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

**`workagent serve`**: `cli.serve` lazy-imports `webapp.run_server` (needs
the `web` extra: fastapi/uvicorn) → `create_app` guards Host/Origin/
Content-Type, read endpoints (`/api/status`, `/api/path`, `/api/repos`,
`/api/links`, `/api/doctor`, `/api/issues`, `/api/candidates`) call the
same in-process helpers as the CLI,
and mutating commands run as `sys.executable -m workagent` children via
`backend.command_argv`-shaped argv (`--yes` on confirm, `--force` on
cleanup/register). Frontend: `web/` (Vite + React + TS), build →
`web/dist`, served by the SPA catch-all; API contract in
`web/API_CONTRACT.md`.

**`workagent candidates`**: two Rich tables — "Unlinked PR/MRs" (open
PR/MRs of every registered repo, minus URLs already linked in
`links.json`; per-repo CLI failures → stderr warning) and "Recent
issues (reported by me, last 7 days)" (jira `list_my_issues`: reporter
= me, To Do/In Progress, last 2 months — plus GitHub issues authored by
me; server-side 7-day filter). `--json` prints `{"prs": […],
"issues": […]}` (stdout-only); `--csv` renders the PR/MR table as CSV.
The web endpoints `/api/issues` (all my issues + `warning`) and
`/api/candidates` (same data as the CLI) share `_candidates()` /
`trackers.list_my_issues()` and are read-only GETs that always return
200. `refs.fetch_open_prs(tool, cwd)` raises (caller decides);
`trackers.list_my_issues()` never raises. glab issues are intentionally
omitted (Jira covers work tracking). `store.record_link` stamps
`added_at` on first sight and never bumps it (Task 7's sort).


**`workagent start <issue>`**: parse ref → `trackers.resolve_for_tracker`
(repo picker: `--repo`, cwd-linked silently, unlinked-cwd y/N with
fallthrough, or linked-repo pick/manual prompt; persists the tracker↔repo
relation) → default branch → fetch title/body → `git-wt start` → record
link → exec `omp` with prompt (`--no-tty` appends the commit/push/MR suffix
and uses `omp -p`).

**`workagent review [PR]`**: parse ref → same tracker-aware repo picker →
fetch head ref → worktree on that branch → record `pr:<url>` link →
one-live-harness guard + record → exec `omp` with pr-reviewer prompt
(`--post-comments`/`--fix` append auto-comment/auto-fix prompt segments).
`review --all` (optional `--sequential`, `--fix`) reviews every
not-reviewed worktree's PR/MR in non-TTY `python -m workagent review`
children — parallel by default, `--sequential` waits one at a time —
skipping reviewed-at-tip, no-PR, invalid-worktree, and live-harness
entries (stderr note each; zero reviewable → exit 0). A launched review
persists `reviewed`/`reviewed_at` (branch tip) on the PR link; the flag
auto-resets when the tip moves (new commit → reviewable again).

**`workagent sync <ref>`**: resolve worktree key (fuzzy, shared with cleanup) →
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

**`workagent cleanup <ref>`**: resolve link key (exact → parsed
key/URL → substring over keys/worktrees/branches; interactive pick on
ambiguity; miss = `no linked state` error) → confirm (unless
`--yes`/`--force`) → `git-wt cleanup --delete-branch` → close issue/PR
(already-closed PR/MR is not an error) → drop link.

**`workagent open <ref>`**: resolve link key (same fuzzy resolver as
cleanup) → refuse missing/invalid worktrees (exit 1) → detached
`xdg-open`/`open`/`explorer` (`Popen` stdio→DEVNULL, `start_new_session`
on posix) → print the path on stdout. Non-destructive run: no confirm
needed in `webapp.SPECS` (`open:<ref>` target key).

**`workagent cleanup --merged --yes`**: loop every link; the merged/closed
source of truth is `_status_cells(refresh_pr=True)` per link (recorded
`pr_url` seed included); non-merged, live-harness, and invalid entries
become `skipped:<reason>` rows — never torn down. Requires `--yes`
(or `--dry-run` preview); `--json` prints `{"results": [...]}`.

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
  functions `workagent-cd` (bash/zsh from `cd_wrapper`, fish) shipped with
  `completions show|install` make it change the caller's directory — a
  child process cannot chdir its parent.
- Tracker canonical ids are host-scoped: `gitlab:<host>/<group>/<repo>`
  (e.g. `gitlab:git.jibit.cloud/server/projectx`). A bare
  `server/projectx` is not portable across hosts — always persist the
  full host-scoped id via `trackers.normalize_id`.
- Host CLI (gh/glab) for PR/MR ops: `repos._detect_host_cli` matches the
  repo's origin-URL host against gh's known hosts (`~/.config/gh/hosts.yml`,
  top-level keys) and glab's (`~/.config/glab-cli/config.yml`, keys under
  `hosts:` at 4-space indent); unknown hosts fall back to the auth-status
  probe. Persisted per repo as `repos.<name>.tool` (`register_repo` seeds
  it; `_repo_tool` in cli.py validates the stored `remote` and re-detects
  on change).
- Status caching (`_status_cells` in cli.py): per-branch cache in the
  `pr_cache` table of `state.db` (post-`workagent migrate`; legacy
  `pr_cache.json` before) via `store.cache_pr_status`
  (pr, tool, base_branch, branch/base tips, behind/ahead counts, checked_at;
  CI in `ci`/`ci_checked_at`/`ci_sha` via `store.cache_ci_status`); a cached
  PR is reused while both tips match and age < 3d, a cached no-PR result
  while age < 30min — repeat `status` runs cost two `git rev-parse` calls
  per worktree; `--refresh-pr` re-queries the PR and re-fetches CI (CI is
  otherwise reused while the branch tip is unchanged and `ci_checked_at`
  < 10min). A recorded `pr_url` seeds the PR cell when cache/query find
  none (display only — never drives sync strategy). User content in Rich
  output is `rich.markup.escape`d (PR titles contain `[`). Detail panel
  (`status <ref>`) shows the full issue URL via `refs.issue_url(key,
  stored)` — jira site from jira-cli's config
  (`~/.config/jira-cli/config.json`, fallback `~/.jira-cli.json`), GitHub
  issues URL from the key; a stored `issue_url`/http `issue` wins. A
  missing PR/MR yields a `create_hint` (GitHub web URL, else a
  `glab mr create` command).
- Status `ci` column (tables, `status <ref>` detail, `/api/status`, web
  table/detail): `success | failure | running | not_started` via
  `refs.fetch_ci_status` (soft-fail: `None` + stderr warning, nothing
  cached); Rich tables render ✓/✗/●, `-` otherwise.
- Status `harness` column (tables, `status <ref>` detail, `/api/status`,
  web table/detail): `"<name> <pid>"` while a harness is live on the
  worktree, `""` (`—` in Rich tables) otherwise — `_harness_cell` in
  cli.py over `store.active_harness` (cross-key worktree fallback
  mirrors `_guard_harness`).

## Files to edit

- Subcommand flags: `src/workagent/cli.py` (Click generates completions; no flag lists to sync)
- Ref parsing/fetching: `src/workagent/refs.py`
- Repo resolution: `src/workagent/repos.py`
- Tracker↔repo guard/picker + `link` data: `src/workagent/trackers.py`
- Sync engine: `src/workagent/sync.py`
- PR-status cache (`pr_cache` table in `state.db`): `src/workagent/store.py`
  (delegates to `src/workagent/store_sqlite.py` once `workagent migrate` ran)
- Completion internals (rc block, shell detect): `src/workagent/completions.py`
- Web backend (`serve`): `src/workagent/webapp.py`; frontend: `web/`
- Tests: `tests/` (mirror module names)

## Parity checklist (review PRs against this list)

4. Parity checklist per feature: CLI flag → web arg passthrough →
   API_CONTRACT.md entry → frontend control → both test files.
   - CLI owns the logic; `webapp.py` `SPECS`/`BOOL_FLAGS`/`VAL_FLAGS`
     mirror it — `tests/test_webapp.py::test_specs_mirror_cli_flags`
     fails on drift (it introspects the real Typer app).
   - New/renamed flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args`
     + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend
     control + `tests/test_cli.py` + `tests/test_webapp.py` in the same PR.


----

- Commit and push on main branch, unless asked otherwise. 
