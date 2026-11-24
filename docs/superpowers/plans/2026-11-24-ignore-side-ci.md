# Ignore Side-CI (Security) & Use Main CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ignore side-CI pipelines (e.g. security-only `trivy` scans) and use the main CI pipeline as the final CI status and link across GitLab MRs, branch lookups, GitHub PRs, and release SHA gates.

**Architecture:** Combine a zero-config built-in heuristic (preferring `push` / non-security-only pipelines on the latest commit SHA over `merge_request_event` security-only pipelines) with an optional per-repo `ci_ignore` list of glob patterns stored in `repos.ci_ignore` and configurable via `workagent repo update --ci-ignore`. Both `refs_ci.py` and `release_gates.py` consume the shared pattern-matching and main-pipeline selection helpers.

**Tech Stack:** Python 3.12+, SQLite (`store_sqlite.py` / `store_trackers.py`), Typer (`cli_manage.py`), `fnmatch`, `pytest` / `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-24-ignore-side-ci-design.md`

## Global Constraints

- Never break existing `refs.fetch_ci`, `refs.fetch_ci_status`, `refs.fetch_ci_details`, or `refs.fetch_branch_ci` signatures or soft-fail semantics (`None, ""` on `HarnessError`).
- Preserve failed-first intra-pipeline job URL selection (`#116`) and older-pipeline URL fallback when the selected main pipeline has no job URLs.
- Always fall back to all pipelines/checks if every candidate would be filtered out, so `workagent` never reports `not_started` when CI actually ran.
- Tests must never read or modify `~/.config/workagent/` (use `tmp_path` / `isolated_config`).

## Review Focus

- **All pipelines on latest SHA are side-CIs (or only one pipeline exists):** Must fall back to the newest pipeline rather than returning `not_started`.
- **Main CI has `status: failed` and side-CI has `id > main_id` with `status: success` (the `#81906` vs `#81907` case):** Must report `failure` and link to `#81906`'s failed job (or `#81906` pipeline URL if jobs lack `web_url`), never `#81907`.
- **Main CI has `status: success` and side-CI has `status: failed` or `running`:** Must report `success` and link to the main CI pipeline/job, ignoring the side-CI.
- **`cwd` is a worktree path rather than the main repo path (or `None`):** `_repo_ci_ignore(cwd)` must still resolve the repo's `ci_ignore` patterns cleanly (matching `path` or prefix/worktree) and return `[]` safely when `cwd` is `None` or unconfigured.
- **Empty `--ci-ignore ""` passed to `workagent repo update`:** Must clear `ci_ignore` back to `[]` in both SQLite and JSON config modes.

---

### Task 1: Per-Repo `ci_ignore` Storage & CLI Configuration

**Files:**
- Modify: `src/workagent/store_sqlite.py`
- Modify: `src/workagent/store_trackers.py`
- Modify: `src/workagent/cli_manage.py`
- Test: `tests/test_repos.py`

**Interfaces:**
- Produces:
  - `repos.ci_ignore` SQLite column (`TEXT NOT NULL DEFAULT '[]'`), returned as `list[str]` in `store.load_repos()[name]["ci_ignore"]`.
  - `store_trackers._parse_ci_ignore(raw: object) -> list[str]`
  - `workagent repo update <name> --ci-ignore "<comma-separated patterns>"` (empty string clears to `[]`).

- [ ] **Step 1: Write failing tests for `ci_ignore` persistence and `repo update --ci-ignore`**

In `tests/test_repos.py`, test that:
1. A newly registered repo defaults `ci_ignore` to `[]`.
2. `workagent repo update <name> --ci-ignore "trivy:*, security/*"` persists `["trivy:*", "security/*"]` in `store.load_repos()[name]["ci_ignore"]`.
3. `workagent repo update <name> --ci-ignore ""` clears it to `[]`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repos.py -k ci_ignore -v`
Expected: FAIL (unrecognized option `--ci-ignore` / missing `ci_ignore` key).

- [ ] **Step 3: Implement `ci_ignore` schema, migration, loader, and CLI flag**

1. `src/workagent/store_sqlite.py`: Add `ci_ignore TEXT NOT NULL DEFAULT '[]'` to `CREATE TABLE IF NOT EXISTS repos` and add `_migrate_repos_ci_ignore(conn)` called from `init_db(db)` (plus include `ci_ignore` in `init_db` column check).
2. `src/workagent/store_trackers.py`: Add `_parse_ci_ignore(raw: object) -> list[str]`; include `ci_ignore` in `load_repos()` SELECT and returned dict, and preserve `ci_ignore` in `register_repo()` upsert.
3. `src/workagent/cli_manage.py`: Add `ci_ignore: Optional[str] = typer.Option(None, "--ci-ignore", ...)` to `repo_update`, parsing comma-separated non-empty stripped patterns (empty string → `[]`), updating SQLite and JSON config fallback, and returning `"ci_ignore"` in the result dict.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repos.py -k ci_ignore -v`
Expected: PASS.

---

### Task 2: GitLab MR & Branch Main-CI Selection in `refs_ci.py`

**Files:**
- Modify: `src/workagent/refs_ci.py`
- Test: `tests/test_refs.py`

**Interfaces:**
- Consumes: `store.load_repos()` (for `ci_ignore` patterns via `_repo_ci_ignore(cwd: str | None) -> list[str]`).
- Produces:
  - `_repo_ci_ignore(cwd: str | None) -> list[str]`
  - `_is_side_ci_glab(pipeline: dict, jobs: list, ignore_patterns: list[str]) -> bool`
  - `_select_main_glab_pipeline(data: list[dict], path: str, host: str, cwd: str | None, pr_url: str, ignore_patterns: list[str]) -> tuple[dict, list]`

- [ ] **Step 1: Write failing tests in `tests/test_refs.py`**

Add tests covering:
1. `test_fetch_ci_glab_ignores_side_ci_security_when_main_fails`: Pipeline `#81906` (`source: "push"`, `sha: "a9892b0e"`, `status: "failed"`, jobs `[test (failed), trivy:ship (success), trivy:source (success)]`) and `#81907` (`source: "merge_request_event"`, `sha: "a9892b0e"`, `status: "success"`, jobs `[trivy:source (success)]`). Assert `fetch_ci` returns `("failure", "<#81906 test job url>")` and `fetch_ci_details` returns checks from `#81906`.
2. `test_fetch_ci_glab_ignores_side_ci_when_main_passes`: `#81906` (`push`, `success`) and `#81907` (`merge_request_event`, `failed`, job `trivy:source`). Assert `fetch_ci` returns `("success", "<#81906 job url>")`.
3. `test_fetch_ci_glab_falls_back_when_all_pipelines_match_side_ci`: Only `#81907` exists. Assert `fetch_ci` returns `("success", "<#81907 job url>")` (never `not_started`).
4. `test_fetch_ci_branch_glab_ignores_side_ci`: `_ci_branch_glab` fetches `per_page=5` and selects `#81906` (`failure`) over `#81907` (`success`) on the same `sha`.
5. `test_fetch_ci_glab_respects_repo_ci_ignore`: Custom `ci_ignore: ["lint-*"]` on the repo ignores a newer pipeline whose jobs are only `lint-check`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_refs.py -k "side_ci or ci_ignore" -v`
Expected: FAIL (`fetch_ci` currently returns `"success"` from `#81907`).

- [ ] **Step 3: Implement main-CI selection in `src/workagent/refs_ci.py`**

1. Implement `_repo_ci_ignore(cwd: str | None) -> list[str]` (safe on `None` or store errors; matches `repo["path"]` or worktree prefix).
2. Implement `_is_security_only_jobs(jobs: list) -> bool` (True when `jobs` is non-empty and every job has `stage == "security"` or name matching `trivy:*`, `security*`, `sast*`, `dast*`, `secret*`, `dependency_scanning*`, `container_scanning*`).
3. Implement `_is_side_ci_glab(pipeline: dict, jobs: list, ignore_patterns: list[str]) -> bool`:
   - Matches explicit `ignore_patterns` against `pipeline.get("source")` or when all `jobs` match `ignore_patterns`.
   - Or built-in heuristic: `pipeline.get("source") == "merge_request_event"` and `_is_security_only_jobs(jobs)`.
4. In `_ci_glab_details`:
   - Find `newest = max(data, key=lambda p: p.get("id", 0))` and `target_sha = str(newest.get("sha") or "")`.
   - Candidate pipelines for the push: if `target_sha` is present, `[p for p in ordered if str(p.get("sha") or "") == target_sha]` (fallback to `ordered` if empty).
   - Inspect candidate pipelines (caching fetched `_pipeline_jobs` by pipeline id so we don't double-fetch): prefer the highest-`id` non-side-CI pipeline (and among non-side-CIs on the same SHA, prefer `source != "merge_request_event"` if `source` is present and another non-side-CI is `push`? Wait: if a repo *only* runs `merge_request_event` for main CI and no `push` pipeline, it's non-side-CI so it's kept; if both `push` and `merge_request_event` exist and one is security-only, `_is_side_ci_glab` filters the security-only one). Fall back to `newest` if all candidates are side-CIs.
   - Use the selected pipeline's `status`, `web_url`, `jobs`, and `checks`, preserving older-pipeline job URL fallback when the selected pipeline has no job URLs.
5. In `_ci_branch_glab`: change `per_page=1` to `per_page=5` and apply the same latest-`sha` + `_is_side_ci_glab` selection.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_refs.py -v`
Expected: PASS.

---

### Task 3: GitHub PR/Branch & Release SHA Gate Filtering

**Files:**
- Modify: `src/workagent/refs_ci.py`
- Modify: `src/workagent/release_gates.py`
- Test: `tests/test_refs.py`
- Test: `tests/test_release_gates.py`

**Interfaces:**
- Consumes: `refs_ci._repo_ci_ignore(cwd: str | None) -> list[str]`, `refs_ci._matches_ci_ignore(name: str, patterns: list[str]) -> bool`.
- Modifies: `_ci_gh_details`, `_ci_branch_gh` in `refs_ci.py`; `_ci_sha_status` in `release_gates.py`.

- [ ] **Step 1: Write failing tests for GitHub and `release_gates._ci_sha_status`**

1. In `tests/test_refs.py`:
   - `test_fetch_ci_gh_ignores_configured_side_ci`: `statusCheckRollup` has `test` (SUCCESS) and `trivy-scan` (FAILURE) with repo `ci_ignore=["trivy-*"]` -> returns `("success", <test url>, [test])`.
   - `test_fetch_ci_branch_gh_ignores_configured_side_ci`: `gh run list --limit 5` returns `[trivy (SUCCESS), ci (FAILURE)]` with `ci_ignore=["trivy*"]` -> returns `("failure", <ci url>)`.
2. In `tests/test_release_gates.py`:
   - `test_ci_sha_status_glab_ignores_ci_ignore_patterns`: `statuses` has `test` (failed) and `trivy:source` (success) or `test` (success) and `trivy:source` (failed) with `ci_ignore=["trivy:*"]` -> ignores `trivy:source`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_refs.py tests/test_release_gates.py -k "ci_ignore or ignores" -v`
Expected: FAIL.

- [ ] **Step 3: Implement filtering in `_ci_gh_details`, `_ci_branch_gh`, and `_ci_sha_status`**

1. In `refs_ci.py`:
   - `_ci_gh_details`: filter `rollup` with `ignore_patterns` (fall back to full `rollup` if all checks match).
   - `_ci_branch_gh`: pass `--limit 5` and `workflowName,name` in `--json`, filter runs against `ignore_patterns` (fall back to `data[0]` if all match).
2. In `release_gates.py`:
   - In `_ci_sha_status`, load `ignore_patterns = _repo_ci_ignore(path)` and filter `rows` (by `name`/`context`) / `runs` (by `name`) before computing status & URL, falling back to unfiltered if all match.

- [ ] **Step 4: Run full test suite**

Run: `pytest -v`
Expected: All tests pass.
