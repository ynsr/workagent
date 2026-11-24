# Design: Ignore Side-CI (Security) and Use Main CI as Final Result (#184)

**Date:** 2026-11-24
**Issue:** https://github.com/ynsr/workagent/issues/184

## Intent & Why

Some repositories (e.g., `projectx`) trigger two CI pipelines on each push: a main CI pipeline (e.g., `#81906`, `branch`/`push` source, 5 jobs including `test` and `trivy:*`) and a side-CI security pipeline (e.g., `#81907`, `merge_request_event`, 1 job `trivy:source`). Currently `workagent` picks `max(id)` (`newest`), so when the main CI fails and the side-CI passes, `workagent` reports `success` and links the side-CI. We want `workagent` to ignore side-CIs and report/link the main CI result as the final result for the push.

## Goals & Non-Goals

- **Goals:**
  - Report and link the main CI pipeline result (failure, success, or running) even when a side-CI pipeline triggers later for the same commit.
  - Support both GitLab (MR + branch + release SHA gate) and GitHub (PR + branch + release SHA gate).
  - Combine zero-config heuristics with explicit per-repo `ci_ignore` configuration.
- **Non-goals:**
  - Changing intra-pipeline failed-first job URL ranking (#116).
  - Altering server-side GitLab/GitHub merge gates.

## Approaches Considered

1. **Built-in heuristic only:** Filter/deprioritize `merge_request_event` security-only pipelines (`trivy:*`, `security`) in favor of `push` pipelines on the latest SHA. Zero config, but inflexible for other custom side-CIs.
2. **Per-repo config only:** Require `ci_ignore` configuration on every repo. Flexible, but fails out-of-the-box for unconfigured repos.
3. **Hybrid (chosen):** Built-in default heuristic for GitLab MR/branch pipelines plus an optional per-repo `ci_ignore` list of glob patterns (`repos.ci_ignore`, configurable via `workagent repo update --ci-ignore`).

## Design Details

- **Per-repo configuration (`store_sqlite.py`, `cli_manage.py`):**
  - Add `ci_ignore TEXT NOT NULL DEFAULT '[]'` to `repos` table with `ALTER TABLE` migration in `_migrate_repos_ci_ignore` (and include in `store.load_repos()` / `repo_update`).
  - Add `--ci-ignore` (comma-separated glob patterns, empty string clears) to `workagent repo update`.
  - Helper `refs_ci._repo_ci_ignore(cwd)` looks up `ci_ignore` from `store.load_repos()` matching `cwd` (resolved path or worktree's main repo path), returning `list[str]`.
- **Main CI selection & filtering (`refs_ci.py`):**
  - **GitLab MR (`_ci_glab_details`):**
    1. Group `data` by `sha` of the newest pipeline (`max(id)`), or use all pipelines if `sha` is missing.
    2. For candidate pipelines (within `_CI_PIPELINE_SCAN`), fetch jobs as needed and classify side-CIs:
       - Explicit match: pipeline `source` or all job names/stages match `ci_ignore` patterns (fnmatch, case-insensitive).
       - Built-in heuristic: a `merge_request_event` pipeline whose jobs are all security/trivy jobs (stage `"security"` or name matching `trivy:*` / `security*` / `sast*`) when another non-side-CI pipeline exists for the same `sha` (or `source == "push"` exists).
    3. Select the main pipeline: highest `id` among non-side-CI pipelines for the latest `sha` (preferring `source == "push"` over `merge_request_event` when both are non-side-CI? Wait: if both are non-side-CI, highest `id` or `push` — specifically, non-side-CI beats side-CI; among non-side-CIs for the same `sha`, `push` or highest `id`). Fall back to newest pipeline if all are filtered.
    4. Return `(state, url, checks)` from that selected main pipeline (preserving failed-first job URL and older-pipeline URL fallback only when the selected main pipeline has no job URLs).
  - **GitLab branch (`_ci_branch_glab`):** Fetch `per_page=5` instead of `per_page=1`, apply the same main-pipeline selection on the latest `sha`.
  - **GitHub PR (`_ci_gh_details`) & branch (`_ci_branch_gh`):**
    - On PR: filter out checks whose `name`/`context` matches `ci_ignore` (falling back to all checks if all match), then compute worst status & URL.
    - On branch: fetch `--limit 5` instead of `1`, filter out runs whose `name`/`workflowName` matches `ci_ignore` (falling back to `data[0]`), and return status & URL.
- **Release SHA gate (`release_gates.py`):**
  - In `_ci_sha_status`, load `ci_ignore` for `path` and filter out matching GitLab commit statuses (`name` / `context`) or GitHub check runs (`name`) before computing aggregate status & URL (falling back to all rows if all are filtered).
