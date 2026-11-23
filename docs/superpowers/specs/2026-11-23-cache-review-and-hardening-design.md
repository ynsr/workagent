# Cache Review & Hardening — Design Spec

- **Issue:** [ynsr/workagent#171](https://github.com/ynsr/workagent/issues/171)
- **Audit Reference:** [`docs/caches.md`](../../caches.md)
- **Date:** 2026-11-23
- **Status:** Approved for planning (Plan mode — no implementation in this branch)

---

## 1. Goals & Constraints

### Goals
Fix all 20 Critical, High, and Medium cache bugs across Workagent's backend (`src/workagent/`) and frontend (`web/src/`) in five clean, independently testable phases:

1. Eliminate data-loss races, full-table rewrites, and lock errors in SQLite caches (`pr_cache`, `_INIT_CACHE`).
2. Make Stale-While-Revalidate (SWR) actually work for `pr_cache`, `issue_cache`, `pr_candidates_cache`, and `release_diff_cache` — instant stale reads + background refresh in `workagent serve`, safe non-spawning reads in short-lived CLI processes.
3. Propagate background cache refreshes and metadata updates to the Web UI via expanded SQLite CDC triggers + debounced SSE invalidation, eliminating polling hacks and duplicate fetches.
4. Bound and harden in-memory process caches (`_NO_TOOL_DIRS`, `_DB_CACHE`, `release_caps._CACHE`, `_OPTIONS_CACHE`, `env._SOURCED_CACHE`).
5. Keep cache management controls minimal (YAGNI): fix existing Refresh buttons, add `?force=true` to `/api/candidates/prs` and `/api/release/status`, and have `candidates --reset-cache` clear both `issue_cache` and `pr_candidates_cache`.

### Non-Goals
- No new `workagent cache` subcommand or `/api/cache` endpoint.
- No cross-tab `localStorage` sync framework or IndexedDB persistence plugin.
- No change to `web_runs.Registry` ring-buffer semantics or `backend.HARNESSES`.

### Global Constraints
- **Test DB/config isolation:** All tests use `isolated_config` / `tmp_path` (`tests/conftest.py`); never touch `~/.config/workagent/`.
- **CLI/Web parity (`AGENTS.md`):** Any new/changed CLI flag or API query param updates `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, frontend controls, `tests/test_cli.py`, and `tests/test_webapp.py`.
- **SQLite concurrency:** Every SQLite read/write goes through `store_sqlite.connect(path)` (WAL mode, 30s `busy_timeout`, `foreign_keys = ON`). Zero raw `sqlite3.connect` calls in `store.py`.
- **No doomed CLI daemon threads:** Short-lived CLI processes never spawn background SWR daemon threads; only `workagent serve` enables background SWR (`swr.set_background_enabled(True)`).
- **Backward compatibility:** Existing `WORKAGENT_*_TTL_SECONDS` and `WORKAGENT_*_SWR_SECONDS` env vars remain honored. Legacy JSON fallback (`pr_cache.json`) continues to work when SQLite is absent.

---

## 2. Phase 1 — Core SQLite & SWR Primitives

### 2.1 Atomic Per-Row `pr_cache` Operations (`src/workagent/store.py`, `src/workagent/store_sqlite.py`)
- Replace `_write_pr_cache`'s raw `sqlite3.connect` + `DELETE FROM pr_cache` + full re-insert with single-row SQLite helpers in `store_sqlite.py`:
  - `get_pr_cache_entry(path: Path, key: str, fallback_key: str = "") -> dict | None`: single `SELECT payload, checked_at FROM pr_cache WHERE branch = ?` (checking `key`, then `fallback_key` if different).
  - `upsert_pr_cache_entry(path: Path, key: str, updater: Callable[[dict], dict], *, touch_checked_at: bool = False) -> dict`: runs inside a single `BEGIN IMMEDIATE` transaction via `sq.connect(path)`, reads current row (if any), merges via `updater`, and executes `INSERT INTO pr_cache (branch, payload, checked_at) VALUES (?, ?, ?) ON CONFLICT(branch) DO UPDATE SET payload = excluded.payload, checked_at = excluded.checked_at`.
  - `delete_pr_cache_entry(path: Path, key: str, fallback_key: str = "") -> int`: deletes one worktree's cache key(s).
- `store.lookup_pr_cache(branch, repo)` calls `sq.get_pr_cache_entry` (O(1) row lookup instead of deserializing the entire table 4× per worktree).
- `cache_pr_status`, `cache_ci_status`, `cache_review_stats`, `cache_issue_status` use `upsert_pr_cache_entry`:
  - Only `cache_pr_status` sets `touch_checked_at=True`.
  - `cache_ci_status` / `cache_review_stats` / `cache_issue_status` preserve existing `checked_at` (leaving `checked_at=""` when creating a CI-only row so PR status is not falsely treated as freshly checked).
- **Impact:** Eliminates lost-update races, eliminates `database is locked` from missing `busy_timeout`, cuts CDC events per update from `2N` to `1`, and cuts `status` deserialization from `O(4W·N)` to `O(W)`.

### 2.2 `swr.py` Semantics & CLI Safety (`src/workagent/swr.py`, `src/workagent/webapp.py`)
- Add process-level background switch:
  - `_BACKGROUND_ENABLED: bool = False` (default `False` for short-lived CLI processes).
  - `set_background_enabled(enabled: bool) -> None` and `is_background_enabled() -> bool`.
  - `webapp.create_app()` calls `swr.set_background_enabled(True)`.
- Fix `serve_stale_or_refresh(stale, is_fresh, refresh, key, *, allow_background: bool | None = None) -> tuple[Any, bool]`:
  - If `stale is not None and is_fresh`: return `(stale, False)` immediately (fixes the `is_fresh=True` footgun).
  - If `stale is not None and not is_fresh`: if `allow_background` (or `is_background_enabled()` when `None`), kick `_background(key, refresh)` and return `(stale, True)`; otherwise (CLI mode) return `(stale, True)` without spawning a doomed daemon thread.
  - Otherwise (`stale is None`): run `refresh()` inline and return `(result, False)`.
- Log background exceptions in `_background` via `logging.getLogger("workagent.swr").warning("swr refresh failed for %s: %s", key, exc)` instead of `except Exception: pass`.

### 2.3 `store_sqlite._INIT_CACHE` Fast Path & Retry Bug (`src/workagent/store_sqlite.py`)
- Add `_INIT_LOCK = threading.Lock()`.
- Track `_INIT_CACHE[str(path)] = (st.st_ino, st.st_dev)` (inode+device tuple) so `init_db(path)` only runs `_schema_current(path)` when the path is not yet cached or the DB file was replaced on disk (e.g., backup restore or test swap).
- Scope backfill flags per DB path: `_BACKFILL_DONE: set[str]` and `_BACKFILL_STARTED: set[str]` guarded by `_INIT_LOCK`.
- Fix retry loop (`store_sqlite.py:2064-2071`): assign `last = e` inside `except sqlite3.OperationalError as e:`.

---

## 3. Phase 2 — `pr_cache` True SWR, Watcher Fix & Pruning

### 3.1 True SWR & Open vs Terminal PR TTLs (`src/workagent/cli_repo_util.py`, `src/workagent/cli_status.py`)
- Split positive PR fresh TTLs:
  - **Open PR fresh TTL:** `WORKAGENT_STATUS_OPEN_TTL_SECONDS` (default `600`s / 10m; falls back to `WORKAGENT_STATUS_TTL_SECONDS` if explicitly set and `OPEN` is unset).
  - **Terminal PR (merged/closed) fresh TTL:** `WORKAGENT_STATUS_TTL_SECONDS` (default `259200`s / 3d; SWR window `WORKAGENT_STATUS_SWR_SECONDS` = 30d).
  - **Negative PR (no PR) fresh TTL:** `WORKAGENT_NEGATIVE_TTL_SECONDS` (default `1800`s / 30m; SWR `1d`).
  - **CI / reviews / issue status fresh TTL:** `WORKAGENT_CI_TTL_SECONDS` (default `600`s / 10m; SWR `1d`).
- Remove unused import-time snapshot constants (`_STATUS_TTL_SECONDS`, `_NEGATIVE_TTL_SECONDS`, `_CI_TTL_SECONDS`, `_*_SWR_SECONDS`).
- In `cli_status.py`:
  - Replace binary `_cache_usable` / `_ci_usable` / `_reviews_usable` / `_issue_usable` with tri-state freshness checks: `"fresh" | "stale" | "expired"`:
    - `"fresh"`: within fresh TTL and git tips/sha match → return cached immediately, no refresh.
    - `"stale"`: past fresh TTL, within SWR window, and git tips/sha match → call `swr.serve_stale_or_refresh(cached, False, _refresh_fn, key=f"pr:{kind}:{cache_key}")` (returns cached immediately; in `serve`, refreshes in background and updates SQLite → CDC pushes to UI; in CLI, returns stale without spawning daemon threads).
    - `"expired"` (or tips changed or `refresh_pr=True`): fetch inline.
  - **No `checked_at` bump on error fallback:** When `_query_pr` raises `HarnessError` and falls back to `cached["pr"]`, return the fallback without calling `cache_pr_status(...)`.
  - **Remote `target_branch` update on `refresh_pr=True`:** Always recompute `base_branch` and ahead/behind counts when `pr.get("target_branch")` differs from `cached.get("base_branch")`, even if local tips didn't move.
  - **Gone worktrees (`branch_tip is None`):** Normalize `sha = branch_tip or ""` consistently on both read and write so gone worktrees hit CI/reviews cache instead of re-fetching every call.

### 3.2 `watcher.py` Key & JSON Path Fix (`src/workagent/watcher.py`)
- Replace `pr_cache.get(branch)` at `watcher.py:300` and `watcher.py:313` with `store.lookup_pr_cache(branch, repo)`.
- Fix PR URL extraction at `watcher.py:64-65`: read `(cached.get("pr") or {}).get("url") or cached.get("url") or cached.get("pr_url")`.
- Re-read fresh `ci` / `reviews` after `_refresh_worktree_inputs` using `store.lookup_pr_cache(branch, repo)`.

### 3.3 `pr_cache` Pruning (`src/workagent/cli_cleanup.py`, `src/workagent/store_links.py`, `src/workagent/store_sqlite.py`)
- Delete matching `pr_cache` row(s) via `store.delete_pr_cache_entry(branch, repo)` during worktree cleanup, hard-delete, `link remove --hard-delete`, `link destroy-deleted`, and `link gc`.
- Add orphan `pr_cache` sweep to `link purge-orphans` (delete `pr_cache` rows whose `(repo, branch)` no longer matches any active/inactive worktree in `worktrees`).

---

## 4. Phase 3 — Candidate, Issue & Release Caches

### 4.1 `pr_candidates_cache` (`src/workagent/cli_candidates.py`, `src/workagent/webapp.py`)
- **Empty-list negative caching:** Use `cached is not None and fetched_at` (never `if cached:`) so `[]` is cached for the 10m TTL.
- **SWR enforcement:** Enforce `_prs_cache_swr()` (1d). If `age >= swr`, fetch inline rather than serving arbitrarily old rows.
- **Read-time link filtering:** Store raw open PRs across repos in `pr_candidates_cache`, and filter against `_linked_sets()` on **read** so newly linked PRs disappear immediately without waiting for TTL expiry.
- **Error preservation:** Track `any_ok: bool` across repos in `_candidate_prs()`. If all repos fail (`not any_ok`), return stale cached PRs + warnings without overwriting SQLite with `[]`.
- **Force refresh & CLI parity:**
  - Add `force: bool = False` query parameter to `GET /api/candidates/prs` (`webapp.py:880`) and pass through to `_candidate_prs_cached(force=force)`.
  - Have `GET /api/candidates` use `_candidate_prs_cached(force=force)` so `/api/candidates` and `/api/candidates/prs` share the same cache.
  - Have `workagent candidates --reset-cache` clear **both** `issue_cache` and `pr_candidates_cache`.
- Remove unused `_PRS_CACHE_TTL_SECONDS` / `_PRS_CACHE_SWR_SECONDS` snapshots.

### 4.2 `issue_cache` (`src/workagent/trackers.py`, `src/workagent/store_sqlite.py`, `src/workagent/webapp.py`)
- **Empty-list negative caching:** Use `cached is not None and fetched_at` (`trackers.py:736`).
- **Error preservation:** `_jira_my_issues` / `_gh_my_issues` return `(items, ok: bool)` (or set a failure flag). When `not ok`, do **not** overwrite existing `issue_cache` rows with `[]`.
- **Thread-safe background `warn` isolation:** In `_cached_source`, the background refresh closure passes a fresh local `bg_warn: list[str] = []` instead of mutating the caller's `warn` list.
- **Invalidation & pruning:**
  - Call `store_sqlite.clear_issue_cache(sq.db_path())` inside `POST /api/tracker-domains` and tracker enable/disable/remove mutations.
  - Prune `issue_cache` rows older than `_cache_swr()` during `store_sqlite.set_issue_cache` (accepting optional `max_age_seconds: int | None`) to bound table size.
- Remove unused `_CACHE_TTL_SECONDS` / `_CACHE_SWR_SECONDS` snapshots.

### 4.3 `release_diff_cache` & Branch CI Caching (`src/workagent/store_sqlite.py`, `src/workagent/repos_release.py`, `src/workagent/webapp.py`)
- **Schema migration (idempotent `ALTER TABLE`):** Extend `release_diff_cache` with nullable/defaulted columns:
  - `default_branch TEXT NOT NULL DEFAULT ''`
  - `release_branch TEXT NOT NULL DEFAULT ''`
  - `ci_json TEXT NOT NULL DEFAULT ''`
  - `ci_fetched_at TEXT NOT NULL DEFAULT ''`
- **Branch-aware key validation:** Cached `count` is only usable when `row.default_branch == default_branch and row.release_branch == release_branch`. Changing branches via `repo update` immediately misses and refreshes.
- **Fix fresh-TTL `git fetch` storm:** When `background=True` and `age < _diff_ttl_seconds()`, return cached `count` immediately **without** triggering `_swr.serve_stale_or_refresh` (`git fetch origin`). Only kick background refresh when `age >= _diff_ttl_seconds()`.
- **Cache branch CI (`fetch_branch_ci`):** Store `ci_json` + `ci_fetched_at` in `release_diff_cache` with fresh TTL `_ci_ttl()` (10m) and SWR `_ci_swr()` (1d). Serve cached CI immediately; refresh via `swr.serve_stale_or_refresh` in `serve` when stale. Never block `/api/release/status` on live `gh`/`glab` CI fetches when cached CI exists.
- **`force=True` & offline fallback:**
  - Add `force: bool = False` query param to `GET /api/release/status`.
  - Always load existing `cached` row even when `force=True` so offline errors still fall back to last-known `count` and preserve `base["fetched_at"] = cached_fetched_at`.
  - Delete `release_diff_cache` row on `repo remove`.
- Remove unused `_RELEASE_DIFF_TTL_SECONDS` / `_RELEASE_DIFF_SWR_SECONDS` snapshots.

---

## 5. Phase 4 — In-Memory Process Caches & Env Cache

| Cache | File | Fix |
|---|---|---|
| `repos._NO_TOOL_DIRS` | `repos.py` | Replace `set[str]` with lock-guarded `dict[str, float]` with 300s (5m) TTL so transient auth/network failures or newly added remotes self-heal. |
| `cli_repo_util._DB_CACHE` | `cli_repo_util.py` | Replace `dict[str, str]` with lock-guarded `dict[str, tuple[float, str]]` — 600s TTL for positive default branch, 60s TTL for negative `""`. |
| `release_caps._CACHE` | `release_caps.py` | Add `threading.Lock`, per-key single-flight, `copy.deepcopy` on read/write, remove dead `_TTL`, and stop caching non-auth `_gh_caps` `HarnessError` as `pr_required: False`. |
| `web_runs_routes._OPTIONS_CACHE` | `web_runs_routes.py` | Add `threading.Lock`, per-key single-flight, `copy.deepcopy`, `swr.cache_ttl("WORKAGENT_OPTIONS_TTL_SECONDS", 600)`, capture timestamp *after* `discover_options()`, and fall back to last-known cached options on probe error (with warning). |
| `env._SOURCED_CACHE` | `env.py` | Add `threading.Lock` + 60s TTL alongside `mtime_ns` check (`WORKAGENT_ENV_CACHE_TTL_SECONDS`, default `60`) so edits to transitively `source`d files (e.g., `~/.secrets/.env`) or proxy restarts are picked up automatically. |

---

## 6. Phase 5 — CDC Expansion & Frontend UX Hardening

### 6.1 Expand SQLite CDC Triggers (`src/workagent/store_sqlite.py`)
- Add 7 tables to `CDC_TABLES`:
  - `("pr_candidates_cache", "source")`
  - `("issue_cache", "source")`
  - `("release_diff_cache", "repo_key")`
  - `("repos", "name")`
  - `("trackers", "id")`
  - `("pr_checks", "worktree_ref")`
  - `("pr_review_threads", "worktree_ref")`
- Existing `_migrate_change_log` + `_CDC_TRIGGER_NAMES` check in `_schema_current` automatically installs the new triggers idempotently on startup.

### 6.2 Debounced CDC Invalidation (`web/src/hooks/useServerEvents.ts`)
- Add a lightweight per-query-key debounce queue (150ms window) inside `useServerEvents.ts` so bursts of `pr_cache` / `pr_checks` / `worktrees` CDC events coalesce into a single `qc.invalidateQueries` call per key.
- Map the new CDC tables:
  - `pr_candidates_cache` → `queryKeys.candidatesPrs`
  - `issue_cache` → `["candidates", "issues"]`, `["issues"]`
  - `release_diff_cache` → `["release"]`
  - `repos` → `queryKeys.repos`, `["release"]`, `queryKeys.candidatesPrs`
  - `trackers` → `queryKeys.trackers`, `queryKeys.trackerDomains`, `["candidates", "issues"]`, `["issues"]`
  - `pr_checks`, `pr_review_threads` → `["worktree-timeline", row_key]` (and `["worktree-timeline"]` if empty)
  - `worktrees` → also invalidate `queryKeys.candidatesPrs`, `queryKeys.candidatesWorktrees`
- Expand SSE reconnect invalidation (`onOpen` when `hadError`) to also invalidate `repos`, `trackers`, `candidatesPrs`, `["candidates", "issues"]`, `candidatesWorktrees`, and `["release"]`.

### 6.3 Frontend Query, Invalidation & UX Fixes
- **`CandidatesCard.tsx`:**
  - Pass `force = false` (not `needsBroad`) to `broadIssuesQuery` so the 365d prefetch and backend 1h cache actually hit.
  - Fix `handleRefresh()`: call `api.candidatesPrs({ force: true })`, `api.candidatesIssues({ force: true, days: 90, mine: true })`, optional broad variant, and `api.candidatesWorktrees()`, and write results into the **subscribed** `queryKeys.candidatesIssues(false, ...)` / `queryKeys.candidatesPrs` / `queryKeys.candidatesWorktrees` keys via `qc.setQueryData` (eliminating the orphaned `"force"` key).
  - Remove the broken 0ms `prsStale` `useEffect` refetch hack (replaced by `pr_candidates_cache` CDC trigger).
  - Fix stale copy (`"last 30 days"` → dynamic window label).
- **`Dashboard.tsx` & `queries.ts`:**
  - Consolidate `useStatusAll()`, `useStatusInactive()`, and `useStatusDeleted()` onto one shared query key `["status", "all", Boolean(refresh)]` backed by `GET /api/status?include_inactive=true&include_deleted=true`, with `useStatusInactive()` and `useStatusDeleted()` as lightweight `select` projections over the same cached response (cutting `/api/status` calls by 3×).
  - Remove dead `refetchOnWindowFocus: true` from 30d/1h queries (or set `refetchOnWindowFocus: false` where CDC + polling own freshness).
  - Fix copy at `Dashboard.tsx:779` to reflect SSE live updates vs fallback polling.
- **Complete `invalidateAfterRun` & mutation handlers (`queries.ts`, `Trackers.tsx`, `Launch.tsx`, `WorktreeDetail.tsx`, `RunDetail.tsx`, `Settings.tsx`):**
  - `invalidateAfterRun`: add `candidatesPrs`, `candidatesWorktrees`, `["candidates", "issues"]`, `prodtests`, `["release"]`, `["worktree-files"]`, `["worktree-diff"]`, `["worktree-timeline"]`.
  - `Trackers.tsx`: invalidate `["candidates", "issues"]`, `["issues"]`, `candidatesPrs`, and `statusAll` on domain updates.
  - `Launch.tsx`: `handleRefreshIssues` updates `queryKeys.candidatesIssues(false, 90, true)` and invalidates `["issues"]`.
  - `WorktreeDetail.tsx`: `launchTerminal` invalidates `statusAll` as well as `runs`.
  - `RunDetail.tsx`: route `handleRerun` through `useCreateRun()` (or call `invalidateQueries(runs)` + `awaitRunThenInvalidate`).
  - `Settings.tsx`: await run completion before invalidating `config`.
- **Remaining UX fixes:**
  - `useRun(id)` (`queries.ts:301`): add `enabled: Boolean(id)`.
  - `WorktreeWorkspace.tsx:90`: pass `onState` callback to `useRunEvents` to sync terminal state into `qc.invalidateQueries(queryKeys.run(activeRunId))` & `queryKeys.runs`.
  - `ReleaseCard.tsx`: add manual Refresh button calling `api.releaseStatus(name, { force: true })` + `setQueryData`; tie `RefreshOverlay` to `useReleaseStatuses` `isFetching` (not `useRepos`); slow poll when SSE connected (60s vs 30s).
  - `SearchableSelect.tsx` / `StartForm.tsx` / `Launch.tsx`: share `useCandidatesIssues(false, 90, true)` instead of `loadedRef` ad-hoc fetch.
  - `WorktreeWatcherOverrides.tsx`: migrate raw `useEffect` fetch to a TanStack Query hook (`useWorktreeWatcher(ref)`).
  - `RunWatcher.tsx`: iterate all newly-finished runs (not `data.find(...)` which drops concurrent completions).
- **API Contract & Inventory:** Update `web/API_CONTRACT.md` and `web/FEATURE_INVENTORY.md` with `?force=true` on `/api/candidates/prs` and `/api/release/status`, plus the expanded CDC table list.

---

## 7. Verification Strategy

- **Backend unit & concurrency tests (`uv run pytest -v`):**
  - Concurrent `cache_pr_status` + `cache_ci_status` + `cache_review_stats` threads updating distinct branches verify zero lost updates and exactly 1 CDC row per write.
  - `swr.serve_stale_or_refresh` tests for `is_fresh=True`, `allow_background=False` (CLI), `allow_background=True` (`serve`), and warning log on background exception.
  - `pr_cache` tri-state freshness tests (open PR 10m vs merged PR 3d fresh / 30d SWR vs error fallback not bumping `checked_at` vs gone worktree `branch_tip=None`).
  - `watcher` test verifying repo-scoped `pr_cache` read/write and `cached["pr"]["url"]` extraction.
  - `pr_candidates_cache` & `issue_cache` tests for `[]` negative caching, transient error preservation, read-time link filtering, and `?force=true`.
  - `release_status` tests for fresh TTL not kicking `git fetch`, cached branch CI, branch-change cache invalidation, and `?force=true` offline fallback.
  - In-memory TTL & locking tests for `_NO_TOOL_DIRS`, `_DB_CACHE`, `release_caps`, `_OPTIONS_CACHE`, and `env._SOURCED_CACHE`.
  - `test_webapp.py::test_specs_mirror_cli_flags` parity test.
- **Frontend unit tests (`cd web && npm test`):**
  - `useServerEvents.test.tsx`: debounced coalescing of `pr_cache` bursts + invalidation for all new CDC tables.
  - `CandidatesCard.test.tsx`: `needsBroad` sends `force=false`, Refresh updates subscribed keys with `force=true`, no 0ms `prsStale` loop.
  - `Dashboard.test.tsx`: single `/api/status` request shared by active/inactive/deleted tables.
  - `ReleaseCard.test.tsx`: manual Refresh button sends `?force=true` and updates cache.
  - `useRun("")` disabled test + `WorktreeWorkspace` SSE terminal sync test.
