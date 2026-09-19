# Status/Sync/Review Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shared fuzzy ref resolution + ref completion for `status`/`sync`/`review`/`cleanup`; enriched `status`/`link list` (behind|ahead vs remote default, latest cached PR state, detail panel); new `harness sync` command (remote rebase default / local merge with CHANGELOG auto-resolve).

**Architecture:** Session keys live in `links.json`; PR status caches in a new `pr_cache.json` (store.py). PR listing normalizes gh/glab output in refs.py. Git ahead/behind lives in repos.py. Merge/conflict logic isolated in new `sync.py`; cli.py only wires commands. Ref resolution reuses cleanup's fuzzy resolver, renamed `_resolve_session_key`.

**Tech Stack:** Python 3, Typer + Rich, gh/glab CLI, pytest + CliRunner + monkeypatch (all subprocesses stubbed except tmp-repo git tests).

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line → stderr (`eprint`).
- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- `--json`/`--csv` output is plain (no Rich markup/colors); colors only in the Rich table path.
- TTY confirmations only on a TTY; non-TTY or `--yes` acts/fails with actionable message.
- Completion values read LOCAL state only, never network; any failure → `[]`.
- No auto-commit on main; commits only when user asks.
- Colors: PR open=green, merged=magenta, closed=red; `gone` for missing worktrees; `-` for unknowns.

---

### Task 1: PR cache in store.py

**Files:**
- Modify: `src/harness/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `load_pr_cache() -> dict`, `save_pr_cache(cache: dict) -> None`, `cache_pr_status(branch: str, pr: dict | None) -> None`, `get_cached_pr_status(branch: str) -> dict | None`. Cache file `pr_cache.json` in config dir; entries `{branch: {"pr": {...} | None, "checked_at": iso}}`.

- [ ] **Step 1: failing tests** — `test_pr_cache_roundtrip` (store → load returns entry with `checked_at`), `test_pr_cache_none` (cached None stays None), `test_get_cached_pr_status_missing` (returns None for unknown branch).
- [ ] **Step 2:** run `uv run pytest tests/test_store.py -q` → fails (AttributeError).
- [ ] **Step 3:** implement in store.py (mirror `_read_json`/`_write_json` pattern).
- [ ] **Step 4:** run → pass.

### Task 2: PR-list fetch helper in refs.py

**Files:**
- Modify: `src/harness/refs.py`
- Test: `tests/test_refs.py`

**Interfaces:**
- Consumes: `run_cmd` (errors.py).
- Produces: `fetch_pr_list_for_branch(tool: str, branch: str) -> list[dict]` — normalized `[{number:int, state:"open|merged|closed", title, author, created_at, url, target_branch}]`, newest-first or caller sorts; `latest_pr(prs: list[dict]) -> dict | None` (max by `created_at`).
- gh: `gh pr list --head <branch> --state all --json number,state,title,author,createdAt,url,baseRefName --limit 50`; state OPEN/MERGED/CLOSED → open/merged/closed; author = `author.login`; created_at = `createdAt`.
- glab: `glab mr list --source-branch <branch> -F json`; iid→number, opened→open; author = `author.username`; created_at, web_url→url, target_branch.

- [ ] **Step 1: failing tests** — `test_fetch_pr_list_gh` (monkeypatch `run_cmd` returning gh JSON → normalized), `test_fetch_pr_list_glab` (same for glab), `test_latest_pr_picks_newest`, `test_latest_pr_empty` → None.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run → pass.

### Task 3: shared ref resolver + ref completion

**Files:**
- Modify: `src/harness/cli.py` (rename `_resolve_cleanup_key`→`_resolve_session_key`, `_pick_cleanup_key`→`_pick_session_key`; use in `status`, `cleanup`; add `autocompletion=_complete_refs` to `status`/`review`/`cleanup` ref args)
- Modify: `src/harness/completions.py`
- Test: `tests/test_cleanup_resolve.py`, `tests/test_completions.py` (if exists; else create)

**Interfaces:**
- Produces: `_resolve_session_key(ref, links)`, `_pick_session_key(ref, resolved)` (same signatures/semantics); `_complete_refs = _completions.complete_names(_completions._ref_candidates)` where `_ref_candidates()` returns session keys + branch names + worktree dir basenames from `store.load_links()` (deduped, sorted).

- [ ] **Step 1:** rename functions + update call sites (`cleanup`, `status`) and tests.
- [ ] **Step 2:** add `_ref_candidates` to completions.py; wire `autocompletion=_complete_refs`.
- [ ] **Step 3:** test `_ref_candidates` returns keys/branches/basenames sorted, and `complete_names` wrapper filters on prefix (existing behavior).
- [ ] **Step 4:** full suite green.

### Task 4: ahead/behind in repos.py

**Files:**
- Modify: `src/harness/repos.py`
- Test: `tests/test_repos.py` (create if absent — check first)

**Interfaces:**
- Consumes: `run_cmd`.
- Produces: `ahead_behind(worktree: Path, default_branch: str) -> dict | None` — `{"behind": int, "ahead": int, "behind_hashes": [str], "ahead_hashes": [str]}` vs `origin/<default>` (remote-tracking, NO fetch); None on any git failure (no remote, no branch). Hashes short (`git rev-list --%s=7` via `--abbrev=7`? rev-list supports `--abbrev=7` on commit listing).

- [ ] **Step 1: failing test** — tmp git repo: init, origin remote stub, `main` with 1 commit, branch `feat/x` with 1 more commit + 1 behind commit → counts (1,1) and hash lists; missing remote → None.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement with three `git -C` rev-list calls.
- [ ] **Step 4:** run → pass.

### Task 5: status/link list enrichment

**Files:**
- Modify: `src/harness/cli.py` (`status`, `link_list`, `_print_rows`)
- Test: `tests/test_cli.py` (new tests)

**Interfaces:**
- Consumes: Task 1–4 symbols; `refs.tool_for_remote(host) `or decide tool from repo remote host (github.com→gh else glab; registry `remote` field; fallback: try gh then glab? — decide: read repo's registered remote URL; no remote → skip PR column `-`).
- Produces: `_pr_cell(entry, refresh=False) -> tuple[str_display, dict_raw]`, `_commits_cell(entry) -> str`, `_print_rows(..., caption=None, colorize=None)`.

Behavior:
- Columns default: `key, branch, commits, pr`; `--worktree` re-adds worktree.
- `commits` = `5|8` (behind|ahead) or `-`; caption (Rich only) lists short hashes: `behind: a1b2c3d … · ahead: e4f5a6b …`.
- `pr` = `PR #123 (open)` colored via colorize (table path only); cached by default via `get_cached_pr_status(branch)`; miss → live `fetch_pr_list_for_branch` + `latest_pr` + `cache_pr_status`; `--refresh-pr` forces live and re-caches.
- Missing worktree dir → commits/pr `gone` (`pr` shows `gone` too? No: pr stays from cache if present; commits shows `gone`).
- `status <ref>`: detail lines — key, worktree, branch, repo, PR number/title/author/state/URL, behind/ahead + hashes (colored state only in Rich path; `--json` structured with all fields).
- `link list` gains same sessions-table columns + `--worktree`/`--refresh-pr`; tracker table unchanged.

- [ ] **Step 1: failing tests** — `test_status_table_shows_behind_ahead` (monkeypatch `repos.ahead_behind`), `test_status_pr_from_cache`, `test_status_pr_refresh_queries_and_caches`, `test_status_ref_detail_includes_pr`, `test_link_list_sessions_enriched`, `test_status_csv_plain`.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run → pass.

### Task 6: sync.py — merge/conflict engine

**Files:**
- Create: `src/harness/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `run_cmd`, `HarnessError`.
- Produces:
  - `dirty_files(worktree: Path) -> list[str]` — `git status --porcelain` paths.
  - `local_merge(worktree: Path, default_branch: str) -> dict` — `git fetch origin <default>`; `git fetch origin <default>:<default>` (ff-only; failure = remote default not ahead → ok); `git merge <default>` → `{"status": "merged"|"up-to-date"|"conflict", "conflicts": [paths]}`.
  - `unreleased_union(ours: str, theirs: str) -> str | None` — pure: if both sides identical outside `## Unreleased` section, return merged text (union of bullet lines, ours order first, then new theirs lines deduped); None if sections/hunks differ elsewhere.
  - `auto_resolve_changelog(worktree: Path, conflicts: list[str]) -> bool` — sole file CHANGELOG.md + `unreleased_union(git show :2:CHANGELOG.md, :3:CHANGELOG.md)` works → write file, `git add`, continue merge → True.
  - `push(worktree: Path, branch: str) -> None` — `git push origin <branch>`.
  - `rebase_remote(tool: str, pr: dict, worktree: Path) -> None` — gh: `gh pr update-branch --rebase <url>`; glab: `glab mr rebase <branch>` run with cwd=worktree.

- [ ] **Step 1: failing tests (tmp real git repos)** — `test_dirty_files`, `test_local_merge_up_to_date`, `test_local_merge_fast_forward`, `test_local_merge_conflict_lists_files`, `test_unreleased_union_keeps_both` (both-sides bullets, dedupe), `test_unreleased_union_rejects_other_conflicts`, `test_auto_resolve_changelog` (create conflicting tmp repo with CHANGELOG), `test_push` (bare origin stub). Rebase: monkeypatch run_cmd capture argv (`test_rebase_gh`, `test_rebase_glab`).
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run → pass.

### Task 7: `harness sync [ref]` command

**Files:**
- Modify: `src/harness/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `_resolve_session_key`, sync.py engine, store cache, refs PR list.
- Produces: `sync` command. Flags: `ref` (optional, autocompletion=_complete_refs), `-r/--rebase` (default True), `-m/--merge`, `--push`, `--harness`, `--yes`, `--all`, `--dry-run`, `--json`.

Flow per session:
1. resolve key(s): ref → one; `--all` → all keys (confirm each on TTY unless `--yes`); neither → interactive pick (non-TTY exit 2).
2. dirty check → abort session with file list (exit 1) unless `--force`.
3. PR lookup (cached/live): exists → rebase via `sync.rebase_remote`; rebase failure or `-m` → local merge path.
4. local merge: conflict → sole-CHANGELOG-Unreleased auto-resolve; else TTY prompt "resolve conflicts manually / launch omp? [y/N]"; `--harness` launches `omp -p <conflict prompt>` in worktree (TTY exec, non-TTY subprocess); non-TTY without flag → exit 2 with conflicted files listed.
5. up-to-date → note, exit 0. `--push` → `sync.push` after successful merge/auto-resolve.
6. `--dry-run`: print plan `{key, branch, worktree, strategy, has_pr, dirty}` per session, no mutation.
7. `--json`: stdout per-session result dicts.

- [ ] **Step 1: failing tests** — `test_sync_dry_run` (mocks), `test_sync_no_ref_non_tty_exits_2`, `test_sync_rebase_runs_gh` (monkeypatch sync.rebase_remote capturing), `test_sync_merge_pushes_with_flag`, `test_sync_dirty_aborts`, `test_sync_all_confirms`.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** full suite green.

### Task 8: docs

**Files:** `README.md`, `CHANGELOG.md`, `AGENTS.md` — usage lines for `sync`, status columns/flags, ref completion note; AGENTS.md: cli.py row + flows.

### Task 9: live smoke

- [ ] `harness status` (rich table w/ 7 projectx sessions, commits/pr columns), `harness status IPG-967` detail, `harness sync IPG-929 --dry-run --json`, `eval "$(harness completions show bash)"` script contains `_complete_refs` wiring, full pytest suite green.
