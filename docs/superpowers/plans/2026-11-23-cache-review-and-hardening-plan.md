# Cache Review & Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 20 Critical, High, and Medium cache bugs across Workagent's SQLite caches, SWR layer, in-memory caches, CDC triggers, and React/TanStack Query frontend.

**Architecture:** Five phased milestones moving bottom-up: (1) atomic SQLite `pr_cache` & safe `swr.py`/`_INIT_CACHE` primitives, (2) true SWR for `pr_cache` + `watcher.py` key fix + pruning, (3) `pr_candidates_cache`/`issue_cache`/`release_diff_cache` correctness & cached branch CI, (4) TTL/locking on in-memory process caches, and (5) expanded CDC triggers + debounced SSE + frontend query/UX fixes.

**Tech Stack:** Python 3.12, SQLite (WAL), FastAPI, pytest (`uv run pytest -v`), TypeScript, React, TanStack Query v5, Vitest (`cd web && npm test`).

**Spec:** [`docs/superpowers/specs/2026-11-23-cache-review-and-hardening-design.md`](../specs/2026-11-23-cache-review-and-hardening-design.md)
**Audit Reference:** [`docs/caches.md`](../../caches.md)

## Global Constraints

- Tests MUST NEVER touch `~/.config/workagent/` or `~/.gemini/antigravity-cli/`; always use `isolated_config` / `tmp_path` (`tests/conftest.py`).
- Every SQLite read/write MUST go through `store_sqlite.connect(path)` (WAL mode, 30s `busy_timeout`, `foreign_keys = ON`). No raw `sqlite3.connect` calls in `store.py`.
- Short-lived CLI processes MUST NEVER spawn background SWR daemon threads (`swr.is_background_enabled()` defaults to `False`; `webapp.create_app()` sets it `True`).
- Existing `WORKAGENT_*_TTL_SECONDS` and `WORKAGENT_*_SWR_SECONDS` env vars remain honored; legacy JSON fallback (`pr_cache.json`) continues working when SQLite is absent.
- CLI/Web parity (`AGENTS.md`): update `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, frontend controls, `tests/test_cli.py`, and `tests/test_webapp.py` whenever flags or API params change.

## Review Focus

- **Empty result `[]` vs missing cache (`None`):** Both `issue_cache` and `pr_candidates_cache` must distinguish `[]` (valid cached empty result, serve from cache) from `None` (cache miss, fetch live) — pinned by tests in Tasks 6 & 7.
- **Total upstream outage with warm cache:** When `gh`/`glab`/`jira` fails for every repo/source, existing valid cached rows must be preserved (never overwritten with `[]` and never having `checked_at` bumped to `now`) — pinned by tests in Tasks 4, 6, 7 & 8.
- **DB file swap / backup restore while `serve` is running:** `_INIT_CACHE` inode+device check must detect a replaced `state.db` file and re-verify schema rather than trusting a stale boolean — pinned by test in Task 3.
- **Concurrent multi-writer updates to `pr_cache`:** Parallel `cache_pr_status`, `cache_ci_status`, and `cache_review_stats` on different branches must not lose updates or emit `2N` CDC rows — pinned by test in Task 1.
- **Persisted `localStorage` `workagent.issuesFilter` (`range: "365"` or `scope.all: true`) on mount:** Mounting `CandidatesCard` must NOT send `force=true` or fire duplicate requests for `days=365&mine=true` — pinned by test in Task 12.

---

## Phase 1: Core SQLite & SWR Primitives

### Task 1: Atomic Per-Row `pr_cache` Operations

**Files:**
- Modify: `src/workagent/store_sqlite.py` (add `get_pr_cache_entry`, `upsert_pr_cache_entry`, `delete_pr_cache_entry`)
- Modify: `src/workagent/store.py:395-637`
- Test: `tests/test_pr_cache_atomic.py`

**Interfaces:**
- Produces (`store_sqlite.py`):
  - `get_pr_cache_entry(path: Path, key: str, fallback_key: str = "") -> dict | None`
  - `upsert_pr_cache_entry(path: Path, key: str, updater: Callable[[dict], dict], *, touch_checked_at: bool = False) -> dict`
  - `delete_pr_cache_entry(path: Path, key: str, fallback_key: str = "") -> int`
- Produces (`store.py`):
  - `lookup_pr_cache(branch: str, repo: str = "") -> dict | None` (O(1) single-row read)
  - `delete_pr_cache_entry(branch: str, repo: str = "") -> int`

- [ ] **Step 1: Write failing tests for concurrent `pr_cache` writes, CDC row count, and `checked_at` preservation**

```python
# tests/test_pr_cache_atomic.py
import threading
from workagent import store, store_sqlite as sq


def test_concurrent_pr_cache_writes_do_not_lose_updates(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    errors: list[Exception] = []

    def _worker(i: int) -> None:
        try:
            store.cache_pr_status(f"feat/branch-{i}", {"number": i, "state": "open"}, repo="/repo/a")
            store.cache_ci_status(f"feat/branch-{i}", "success", sha=f"sha{i}", repo="/repo/a")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for i in range(12):
        hit = store.lookup_pr_cache(f"feat/branch-{i}", "/repo/a")
        assert hit is not None
        assert hit["pr"]["number"] == i
        assert hit["ci"] == "success"


def test_single_row_update_emits_one_cdc_event_not_full_table(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    for i in range(5):
        store.cache_pr_status(f"b-{i}", {"number": i, "state": "open"}, repo="/repo/a")
    with sq.connect(db) as conn:
        before = conn.execute("SELECT COUNT(*) FROM change_log WHERE table_name = 'pr_cache'").fetchone()[0]
    store.cache_ci_status("b-0", "failure", sha="abc", repo="/repo/a")
    with sq.connect(db) as conn:
        after = conn.execute("SELECT COUNT(*) FROM change_log WHERE table_name = 'pr_cache'").fetchone()[0]
    assert after - before == 1


def test_ci_only_write_does_not_fabricate_pr_checked_at(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    store.cache_ci_status("brand-new", "running", sha="abc", repo="/repo/a")
    hit = store.lookup_pr_cache("brand-new", "/repo/a")
    assert hit is not None
    assert hit["ci"] == "running"
    assert hit.get("checked_at", "") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pr_cache_atomic.py -v`
Expected: FAIL (`after - before` is 10 instead of 1; `checked_at` is non-empty).

- [ ] **Step 3: Implement single-row SQLite helpers in `store_sqlite.py` and wire `store.py`**

In `src/workagent/store_sqlite.py`:
```python
def get_pr_cache_entry(path: Path, key: str, fallback_key: str = "") -> dict | None:
    """Read one pr_cache entry by key (falling back to bare branch key)."""
    init_db(path)
    with connect(path) as conn:
        for k in (key, fallback_key) if fallback_key and fallback_key != key else (key,):
            row = conn.execute(
                "SELECT payload, checked_at FROM pr_cache WHERE branch = ?", (k,)
            ).fetchone()
            if row is not None:
                try:
                    payload = json.loads(row["payload"] or "{}")
                except ValueError:
                    payload = {}
                entry = payload if isinstance(payload, dict) else {"pr": payload}
                entry.setdefault("pr", None)
                entry.setdefault("checked_at", row["checked_at"] or "")
                return entry
    return None


def upsert_pr_cache_entry(
    path: Path,
    key: str,
    updater,
    *,
    touch_checked_at: bool = False,
) -> dict:
    """Atomically read-modify-write one pr_cache row under BEGIN IMMEDIATE."""
    init_db(path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload, checked_at FROM pr_cache WHERE branch = ?", (key,)
        ).fetchone()
        existing: dict = {}
        checked_at = ""
        if row is not None:
            try:
                raw = json.loads(row["payload"] or "{}")
                if isinstance(raw, dict):
                    existing = raw
            except ValueError:
                existing = {}
            checked_at = row["checked_at"] or ""
            existing["checked_at"] = checked_at
        updated = dict(updater(existing))
        new_checked = now_iso if touch_checked_at else str(updated.pop("checked_at", checked_at) or "")
        updated.pop("checked_at", None)
        conn.execute(
            "INSERT INTO pr_cache (branch, payload, checked_at) VALUES (?, ?, ?) "
            "ON CONFLICT(branch) DO UPDATE SET payload = excluded.payload, checked_at = excluded.checked_at",
            (key, json.dumps(updated), new_checked),
        )
        updated["checked_at"] = new_checked
        return updated


def delete_pr_cache_entry(path: Path, key: str, fallback_key: str = "") -> int:
    """Delete pr_cache row(s) for key and optional bare fallback_key."""
    init_db(path)
    keys = [k for k in dict.fromkeys((key, fallback_key)) if k]
    if not keys:
        return 0
    with connect(path) as conn:
        cur = conn.execute(
            f"DELETE FROM pr_cache WHERE branch IN ({','.join('?' for _ in keys)})",
            keys,
        )
        return cur.rowcount
```

In `src/workagent/store.py`:
- Update `load_pr_cache()` to use `sq.connect(db)` instead of raw `sqlite3.connect(str(db))`.
- Update `lookup_pr_cache(branch, repo)` to call `sq.get_pr_cache_entry(db, pr_cache_key(repo, branch), branch)` when SQLite is active.
- Update `cache_pr_status`, `cache_ci_status`, `cache_review_stats`, `cache_issue_status` to call `sq.upsert_pr_cache_entry(...)` (with `touch_checked_at=True` ONLY in `cache_pr_status`) when SQLite is active, keeping legacy `_locked` JSON path unchanged.
- Export `delete_pr_cache_entry(branch: str, repo: str = "") -> int`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_pr_cache_atomic.py tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store.py tests/test_pr_cache_atomic.py
git commit -m "[171] Use atomic single-row upserts and sq.connect for pr_cache"
```

---

### Task 2: Fix `swr.py` Footguns & CLI Background-Thread Guard

**Files:**
- Modify: `src/workagent/swr.py:85-128`
- Modify: `src/workagent/webapp.py` (call `swr.set_background_enabled(True)` in `create_app`)
- Test: `tests/test_swr.py`

**Interfaces:**
- Produces (`swr.py`):
  - `set_background_enabled(enabled: bool) -> None`
  - `is_background_enabled() -> bool`
  - `serve_stale_or_refresh(stale: Any, is_fresh: bool, refresh: Callable[[], Any], key: str, *, allow_background: bool | None = None) -> tuple[Any, bool]`

- [ ] **Step 1: Write failing tests for `is_fresh=True`, CLI no-thread default, and error logging**

```python
# tests/test_swr.py
import logging
import threading
from workagent import swr


def test_is_fresh_returns_stale_without_calling_refresh():
    called = False
    def _refresh():
        nonlocal called
        called = True
        return "fresh"
    val, served_stale = swr.serve_stale_or_refresh("cached", True, _refresh, "k-fresh")
    assert val == "cached"
    assert served_stale is False
    assert called is False


def test_cli_default_does_not_spawn_background_thread():
    swr.set_background_enabled(False)
    ran = threading.Event()
    val, served_stale = swr.serve_stale_or_refresh("cached", False, lambda: ran.set(), "k-cli")
    assert val == "cached"
    assert served_stale is True
    assert not ran.wait(0.1)


def test_background_exception_is_logged(caplog):
    done = threading.Event()
    def _boom():
        try:
            raise RuntimeError("boom")
        finally:
            done.set()
    with caplog.at_level(logging.WARNING, logger="workagent.swr"):
        val, served_stale = swr.serve_stale_or_refresh(
            "cached", False, _boom, "k-boom", allow_background=True
        )
        assert done.wait(2.0)
    assert val == "cached"
    assert served_stale is True
    assert any("k-boom" in r.message and "boom" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_swr.py -v`
Expected: FAIL (`is_fresh=True` invokes `_refresh`, `set_background_enabled` missing).

- [ ] **Step 3: Implement fixes in `src/workagent/swr.py` and enable in `webapp.create_app`**

In `src/workagent/swr.py`:
```python
import logging

_LOG = logging.getLogger("workagent.swr")
_BACKGROUND_ENABLED = False


def set_background_enabled(enabled: bool) -> None:
    """Enable/disable background SWR threads (True in serve, False in CLI)."""
    global _BACKGROUND_ENABLED
    _BACKGROUND_ENABLED = bool(enabled)


def is_background_enabled() -> bool:
    return _BACKGROUND_ENABLED
```
Update `_background` `except Exception as exc:` to `_LOG.warning("swr refresh failed for %s: %s", key, exc)`.
Update `serve_stale_or_refresh`:
```python
def serve_stale_or_refresh(
    stale: Any,
    is_fresh: bool,
    refresh: Callable[[], Any],
    key: str,
    *,
    allow_background: bool | None = None,
) -> tuple[Any, bool]:
    if stale is not None and is_fresh:
        return stale, False
    if stale is not None and not is_fresh:
        bg = _BACKGROUND_ENABLED if allow_background is None else allow_background
        if bg:
            _background(key, refresh)
        return stale, True
    return refresh(), False
```
In `src/workagent/webapp.py` inside `create_app(...)`: call `swr.set_background_enabled(True)`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_swr.py tests/test_webapp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/swr.py src/workagent/webapp.py tests/test_swr.py
git commit -m "[171] Fix swr is_fresh footgun, log background errors, guard CLI threads"
```

---

### Task 3: Fix `store_sqlite._INIT_CACHE` Fast Path, Inode Detection & `raise last`

**Files:**
- Modify: `src/workagent/store_sqlite.py:1880-2098`
- Test: `tests/test_store_init_cache.py`

**Interfaces:**
- Preserves `init_db(path: Path, defer_backfill: bool = False) -> Path` and `backfill_async() -> threading.Thread | None`.

- [ ] **Step 1: Write failing tests for fast-path skip, DB-file swap detection, and lock-retry error**

```python
# tests/test_store_init_cache.py
import sqlite3
import pytest
from workagent import store_sqlite as sq


def test_init_db_skips_schema_current_on_warm_cache(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    sq.init_db(db)
    calls = 0
    orig = sq._schema_current
    def _spy(p):
        nonlocal calls
        calls += 1
        return orig(p)
    monkeypatch.setattr(sq, "_schema_current", _spy)
    sq.init_db(db)
    sq.init_db(db)
    assert calls == 0


def test_init_db_rechecks_when_db_file_replaced(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    sq.init_db(db)
    db.unlink()
    sqlite3.connect(str(db)).close()  # new empty file (new inode)
    sq.init_db(db)
    with sq.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pr_cache" in tables


def test_init_db_raises_operational_error_not_typeerror_on_lock(tmp_path, monkeypatch):
    db = tmp_path / "locked.db"
    def _locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(sq, "connect", _locked)
    monkeypatch.setattr(sq, "_schema_current", lambda _p: False)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        sq.init_db(db)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_init_cache.py -v`
Expected: FAIL (`calls == 2`; `TypeError: exceptions must derive from BaseException` instead of `OperationalError`).

- [ ] **Step 3: Implement inode-keyed `_INIT_CACHE`, per-path backfill tracking, and `last = e` in `store_sqlite.py`**

```python
_INIT_LOCK = threading.Lock()
_INIT_CACHE: dict[str, tuple[int, int]] = {}
_BACKFILL_DONE: set[str] = set()
_BACKFILL_STARTED: set[str] = set()


def _file_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return (st.st_ino, st.st_dev)
    except OSError:
        return None
```
In `init_db(path, defer_backfill=False)`:
- Check `sig = _file_sig(path)` under `_INIT_LOCK`: if `sig is not None and _INIT_CACHE.get(str(path)) == sig`, trigger backfill if `str(path)` not in `_BACKFILL_DONE | _BACKFILL_STARTED` and return `path` immediately (no `_schema_current` call).
- In `except sqlite3.OperationalError as e:`: set `last = e` before checking `"locked"`.
- On success, store `_INIT_CACHE[str(path)] = _file_sig(path)` under `_INIT_LOCK`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_store_init_cache.py tests/test_store_sqlite.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py tests/test_store_init_cache.py
git commit -m "[171] Fix _INIT_CACHE fast path, inode invalidation, and lock retry raise"
```

---

## Phase 2: `pr_cache` True SWR, Watcher Fix & Pruning

### Task 4: Wire True SWR for `pr_cache` + Open vs Terminal TTLs + Error/Gone-Worktree Fixes

**Files:**
- Modify: `src/workagent/cli_repo_util.py:20-58`
- Modify: `src/workagent/cli_status.py:58-481`
- Test: `tests/test_pr_cache_swr.py`

**Interfaces:**
- Consumes: `swr.serve_stale_or_refresh`, `store.lookup_pr_cache`, `store.cache_pr_status`, `store.cache_ci_status`, `store.cache_review_stats`, `store.cache_issue_status`.
- Produces (`cli_repo_util.py`): `_status_open_ttl() -> int` (default 600s, fallback to explicit `WORKAGENT_STATUS_TTL_SECONDS`).

- [ ] **Step 1: Write failing tests for tri-state freshness, open vs merged PR TTL, error fallback not bumping `checked_at`, and gone worktree CI hit**

```python
# tests/test_pr_cache_swr.py
from datetime import datetime, timedelta, timezone
from workagent import cli_status, store, store_sqlite as sq, swr


def test_open_pr_becomes_stale_after_10m_while_merged_stays_fresh(isolated_config, monkeypatch):
    sq.init_db(sq.db_path())
    old_20m = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    open_entry = {"pr": {"state": "open", "number": 1}, "branch_tip": "a", "base_tip": "b", "checked_at": old_20m}
    merged_entry = {"pr": {"state": "merged", "number": 2}, "branch_tip": "a", "base_tip": "b", "checked_at": old_20m}
    assert cli_status._pr_freshness(open_entry, "a", "b") == "stale"
    assert cli_status._pr_freshness(merged_entry, "a", "b") == "fresh"


def test_query_pr_error_fallback_does_not_bump_checked_at(isolated_config, monkeypatch, tmp_path):
    sq.init_db(sq.db_path())
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    repo = str(tmp_path)
    store.cache_pr_status("feat/x", {"state": "open", "number": 9}, branch_tip="t1", base_tip="b1", repo=repo)
    with sq.connect(sq.db_path()) as conn:
        conn.execute("UPDATE pr_cache SET checked_at = ?", (old_ts,))
    monkeypatch.setattr(cli_status, "_query_pr", lambda *a, **kw: (_ for _ in ()).throw(cli_status.HarnessError("offline")))
    monkeypatch.setattr(cli_status, "_repo_default_branch", lambda _r: "main")
    monkeypatch.setattr(cli_status, "_rev_parse", lambda _wt, ref: "t1" if ref == "HEAD" else "b1")
    entry = {"repo": repo, "branch": "feat/x", "worktree": repo}
    cli_status._pr_cells(entry, refresh_pr=True)
    hit = store.lookup_pr_cache("feat/x", repo)
    assert hit["checked_at"] == old_ts


def test_gone_worktree_ci_cache_hits_when_branch_tip_is_none(isolated_config):
    sq.init_db(sq.db_path())
    store.cache_ci_status("gone-br", "success", sha="", repo="/r")
    hit = store.lookup_pr_cache("gone-br", "/r")
    assert cli_status._ci_freshness(hit, None) == "fresh"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pr_cache_swr.py -v`
Expected: FAIL (`_pr_freshness` / `_ci_freshness` not defined; `checked_at` gets bumped).

- [ ] **Step 3: Implement tri-state freshness & SWR in `cli_repo_util.py` and `cli_status.py`**

- In `cli_repo_util.py`: add `_status_open_ttl()` (reads `WORKAGENT_STATUS_OPEN_TTL_SECONDS`, else `WORKAGENT_STATUS_TTL_SECONDS` if set in `os.environ`, else `600`); remove unused `_STATUS_TTL_SECONDS` / `_NEGATIVE_TTL_SECONDS` / `_CI_TTL_SECONDS` / `_*_SWR_SECONDS` module-level snapshots.
- In `cli_status.py`:
  - Add `_pr_freshness(cached, branch_tip, base_tip) -> Literal["fresh", "stale", "expired"]` using `_status_open_ttl()` for open PRs, `_status_ttl()` for merged/closed PRs, `_negative_ttl()` for `pr is None`, and `_status_swr()` / `_negative_swr()` for SWR upper bounds.
  - Add `_ci_freshness(cached, branch_tip)`, `_reviews_freshness(cached, branch_tip)`, `_issue_freshness(cached)` using `_ci_ttl()` (fresh) and `_ci_swr()` (stale), normalizing `sha = branch_tip or ""`.
  - In `_pr_cells`, `_ci_cell`, `_reviews_cell`, `_issue_cell`: on `"stale"`, call `swr.serve_stale_or_refresh(cached, False, _refresh_fn, key=...)`.
  - On `_query_pr` `HarnessError` fallback, return `cached["pr"]` without calling `store.cache_pr_status`.
  - On `refresh_pr=True` with changed remote `target_branch`, recompute `base_branch` and ahead/behind counts.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_pr_cache_swr.py tests/test_cli_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_repo_util.py src/workagent/cli_status.py tests/test_pr_cache_swr.py
git commit -m "[171] Implement true SWR for pr_cache with open/terminal TTLs"
```

---

### Task 5: Fix `watcher.py` `pr_cache` Key/Path & Prune Orphan `pr_cache` Rows

**Files:**
- Modify: `src/workagent/watcher.py:60-95, 295-330`
- Modify: `src/workagent/cli_cleanup.py`, `src/workagent/store_links.py`, `src/workagent/store_sqlite.py`
- Test: `tests/test_watcher_pr_cache.py`

**Interfaces:**
- Consumes: `store.lookup_pr_cache(branch, repo)`, `store.delete_pr_cache_entry(branch, repo)`.
- Produces: `store_sqlite.prune_orphan_pr_cache(path: Path) -> int`, `watcher._cached_pr_url(cached: dict | None) -> str`.

- [ ] **Step 1: Write failing tests for watcher repo-scoped lookup + PR URL extraction + orphan pruning**

```python
# tests/test_watcher_pr_cache.py
from workagent import store, store_sqlite as sq, watcher


def test_watcher_reads_repo_scoped_pr_cache_and_nested_pr_url(isolated_config):
    sq.init_db(sq.db_path())
    store.cache_pr_status(
        "feat/x",
        {"number": 42, "state": "open", "url": "https://github.com/o/r/pull/42"},
        tool="gh",
        repo="/repo/a",
    )
    entry = store.lookup_pr_cache("feat/x", "/repo/a")
    assert watcher._cached_pr_url(entry) == "https://github.com/o/r/pull/42"


def test_prune_orphan_pr_cache_removes_unlinked_entries(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    store.cache_pr_status("orphan-br", {"number": 1}, repo="/repo/gone")
    deleted = sq.prune_orphan_pr_cache(db)
    assert deleted == 1
    assert store.lookup_pr_cache("orphan-br", "/repo/gone") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_watcher_pr_cache.py -v`
Expected: FAIL (`_cached_pr_url` and `prune_orphan_pr_cache` not defined).

- [ ] **Step 3: Fix `watcher.py` and wire `delete_pr_cache_entry` / `prune_orphan_pr_cache`**

- In `src/workagent/watcher.py`:
  - Add `_cached_pr_url(cached: dict | None) -> str` returning `((cached or {}).get("pr") or {}).get("url") or (cached or {}).get("url") or (cached or {}).get("pr_url") or ""`.
  - Replace `pr_cache.get(branch)` at lines ~300 and ~313 with `store.lookup_pr_cache(branch, repo)`.
- In `src/workagent/store_sqlite.py`: implement `prune_orphan_pr_cache(path)` deleting `pr_cache` rows not matching any `f"{repo}\x1f{branch}"` or `branch` in `worktrees`.
- Call `store.delete_pr_cache_entry(branch, repo)` in worktree teardown (`cli_cleanup.py`, `store_links.py`) and `prune_orphan_pr_cache` in `link purge-orphans`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_watcher_pr_cache.py tests/test_watcher.py tests/test_cleanup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/watcher.py src/workagent/cli_cleanup.py src/workagent/store_links.py src/workagent/store_sqlite.py tests/test_watcher_pr_cache.py
git commit -m "[171] Fix watcher pr_cache repo-scoped lookup and prune orphan entries"
```

---

## Phase 3: Candidate, Issue & Release Caches

### Task 6: Fix `pr_candidates_cache` (Empty `[]`, SWR, Read-Time Filtering, `?force=true`, `--reset-cache`)

**Files:**
- Modify: `src/workagent/store_sqlite.py:2332-2357` (add `clear_pr_candidates_cache(path: Path) -> None`)
- Modify: `src/workagent/cli_candidates.py:116-235, 340-355`
- Modify: `src/workagent/webapp.py:871-888`
- Test: `tests/test_pr_candidates_cache.py`

**Interfaces:**
- Produces: `store_sqlite.clear_pr_candidates_cache(path: Path) -> None`, `_candidate_prs_cached(force: bool = False) -> tuple[list[dict], list[str], bool]`, `GET /api/candidates/prs?force=bool`.

- [ ] **Step 1: Write failing tests for empty `[]` hit, SWR enforcement, read-time link filter, error preservation, and `?force=true`**

```python
# tests/test_pr_candidates_cache.py
from datetime import datetime, timedelta, timezone
from workagent import cli_candidates, store_sqlite as sq


def test_empty_pr_list_is_served_from_cache(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_pr_candidates_cache(db, [], source="prs")
    calls = 0
    def _live():
        nonlocal calls
        calls += 1
        return ([], [], True)
    monkeypatch.setattr(cli_candidates, "_fetch_raw_open_prs", _live)
    prs, warnings, stale = cli_candidates._candidate_prs_cached()
    assert prs == [] and stale is False and calls == 0


def test_linked_pr_filtered_on_read(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_pr_candidates_cache(db, [{"url": "https://github.com/o/r/pull/1", "title": "t"}], source="prs")
    monkeypatch.setattr(cli_candidates, "_linked_sets", lambda: ({"https://github.com/o/r/pull/1"}, set()))
    prs, _, _ = cli_candidates._candidate_prs_cached()
    assert prs == []


def test_total_failure_preserves_existing_cache(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    sq.set_pr_candidates_cache(db, [{"url": "https://github.com/o/r/pull/7"}], source="prs")
    with sq.connect(db) as conn:
        conn.execute("UPDATE pr_candidates_cache SET fetched_at = ?", (old,))
    monkeypatch.setattr(cli_candidates, "_fetch_raw_open_prs", lambda: ([], ["gh down"], False))
    prs, warnings, stale = cli_candidates._candidate_prs_cached(force=True)
    assert len(prs) == 1
    assert "gh down" in warnings[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pr_candidates_cache.py -v`
Expected: FAIL (`[]` treated as miss; `_fetch_raw_open_prs` missing).

- [ ] **Step 3: Refactor `cli_candidates.py`, add `sq.clear_pr_candidates_cache`, and add `force: bool = False` to `/api/candidates/prs`**

- In `store_sqlite.py`: add `clear_pr_candidates_cache(path: Path) -> None` (`DELETE FROM pr_candidates_cache`). Note that `sq.get_pr_candidates_cache(db)` returns `([], None)` on cache miss, so `fetched_at is not None` is the cache-hit sentinel (allowing `cached == []` to hit).
- Extract `_fetch_raw_open_prs() -> tuple[list[dict], list[str], bool]` (returns `(raw_prs, warnings, any_ok)` without `_linked_sets()` filtering).
- In `_candidate_prs_cached(force: bool = False)`:
  - Check `fetched_at is not None` (handles `cached == []`).
  - Enforce `_prs_cache_swr()`: only serve stale when `ttl <= age < swr`.
  - In `_refresh()`, only call `sq.set_pr_candidates_cache(db, raw, source="prs")` when `any_ok` is `True`; if `not any_ok` and `fetched_at is not None`, fall back to `cached`.
  - Apply `_filter_unlinked(raw)` on **read** before returning.
- Have `_candidates(force=...)` use `_candidate_prs_cached(force=force)` so `/api/candidates` shares the cache.
- Have `--reset-cache` clear both `sq.clear_issue_cache(db)` and `sq.clear_pr_candidates_cache(db)`.
- Add `force: bool = False` param to `GET /api/candidates/prs` in `webapp.py`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_pr_candidates_cache.py tests/test_candidates.py tests/test_webapp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_candidates.py src/workagent/webapp.py src/workagent/store_sqlite.py tests/test_pr_candidates_cache.py
git commit -m "[171] Fix pr_candidates_cache negative caching, SWR, read-time filtering, and force param"
```

---

### Task 7: Fix `issue_cache` (Empty `[]`, Error Preservation, Thread-Safe `warn`, Pruning & Invalidation)

**Files:**
- Modify: `src/workagent/trackers.py:677-749`
- Modify: `src/workagent/store_sqlite.py:2297-2330`
- Modify: `src/workagent/webapp.py:789-796`
- Test: `tests/test_issue_cache.py`

**Interfaces:**
- Preserves `trackers.list_my_issues(warn: list[str], *, force: bool = False, days: int = 90, mine: bool = True) -> list[dict]`.
- Produces: `store_sqlite.set_issue_cache(path: Path, source: str, items: list[dict], *, max_age_seconds: int | None = None) -> None`.

- [ ] **Step 1: Write failing tests for empty `[]` cache hit, error preservation, and background `warn` isolation**

```python
# tests/test_issue_cache.py
from workagent import store_sqlite as sq, trackers


def test_empty_issue_list_is_cached(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira:mine90", [])
    calls = 0
    def _fetch(w, days=90, mine=True):
        nonlocal calls
        calls += 1
        return []
    monkeypatch.setattr(trackers, "_jira_my_issues", _fetch)
    warn: list[str] = []
    rows = trackers._cached_source("jira", _fetch, warn, force=False, days=90, mine=True)
    assert rows == [] and calls == 0


def test_upstream_failure_does_not_overwrite_issue_cache(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira:mine90", [{"key": "IPG-1"}])
    def _fail(w, days=90, mine=True):
        w.append("jira timeout")
        return []
    warn: list[str] = []
    rows = trackers._cached_source("jira", _fail, warn, force=True, days=90, mine=True)
    assert rows == [{"key": "IPG-1"}]
    cached, _ = sq.get_issue_cache(db, "jira:mine90")
    assert cached == [{"key": "IPG-1"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_issue_cache.py -v`
Expected: FAIL (`calls == 1`; cache overwritten with `[]`).

- [ ] **Step 3: Fix `_cached_source` in `trackers.py`, add SWR-window pruning in `store_sqlite.py`, invalidate in `webapp.py`**

- In `trackers.py`:
  - Check `fetched_at is not None` (since `sq.get_issue_cache` returns `([], None)` on miss, checking `fetched_at` allows valid `[]` hits).
  - Detect upstream error via a local `local_warn: list[str] = []` (`len(local_warn) > 0 and not rows`): when failed, do NOT call `sq.set_issue_cache` and fall back to `cached` if `fetched_at is not None`.
  - Background SWR lambda uses its own `bg_warn: list[str] = []` so it never mutates the caller's `warn` list.
  - Pass `max_age_seconds=_cache_swr()` to `sq.set_issue_cache(...)`.
  - Remove unused `_CACHE_TTL_SECONDS` / `_CACHE_SWR_SECONDS` snapshots.
- In `store_sqlite.py`: in `set_issue_cache(..., max_age_seconds: int | None = None)`, prune rows whose `fetched_at` is older than `max_age_seconds` (when provided).
- In `webapp.py`: call `sq.clear_issue_cache(sq.db_path())` on `POST /api/tracker-domains` and tracker mutations.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_issue_cache.py tests/test_trackers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/trackers.py src/workagent/store_sqlite.py src/workagent/webapp.py tests/test_issue_cache.py
git commit -m "[171] Fix issue_cache negative caching, error preservation, and warn thread safety"
```

---

### Task 8: Fix `release_diff_cache` (Fresh-TTL Bug, Cached Branch CI, Branch Keying & `?force=true`)

**Files:**
- Modify: `src/workagent/store_sqlite.py:83-85, 1805, 2358-2383` (migration + `_EXPECTED_COLS["release_diff_cache"]` for `default_branch`, `release_branch`, `ci_json`, `ci_fetched_at`)
- Modify: `src/workagent/repos_release.py:163-344`
- Modify: `src/workagent/repos.py` (`unregister_repo` deletes `release_diff_cache` row)
- Modify: `src/workagent/webapp.py:904-910`
- Test: `tests/test_release_cache.py`

**Interfaces:**
- Produces: `repos_release.release_status(repo_name: str, force: bool = False, background: bool = False) -> dict`, `GET /api/release/status?name=&force=bool`.

- [ ] **Step 1: Write failing tests for fresh-TTL no-fetch, cached branch CI, branch-change miss, and `?force=true` offline fallback**

```python
# tests/test_release_cache.py
from workagent import repos_release, store, store_sqlite as sq, swr


def test_fresh_release_cache_does_not_trigger_background_git_fetch(isolated_config, monkeypatch, tmp_path):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_release_diff_cache(
        db, "demo", 3,
        default_branch="main", release_branch="release",
        ci_json='{"status":"success"}', ci_fetched_at="2099-01-01T00:00:00+00:00",
    )
    kicked = []
    monkeypatch.setattr(swr, "serve_stale_or_refresh", lambda *a, **kw: (kicked.append(kw.get("key") or a[3]), (a[0], False))[1])
    monkeypatch.setattr(store, "load_repos", lambda: {"demo": {"path": str(tmp_path), "default_branch": "main", "release_branch": "release"}})
    monkeypatch.setattr(repos_release, "_behind_count", lambda *a, **kw: 0)
    swr.set_background_enabled(True)
    res = repos_release.release_status("demo", background=True)
    assert res["commits_ahead"] == 3
    assert kicked == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_release_cache.py -v`
Expected: FAIL (`set_release_diff_cache` doesn't accept new kwargs; `kicked` is non-empty).

- [ ] **Step 3: Add migration, update `_EXPECTED_COLS`, `repos_release.py`, `repos.py`, and `webapp.py`**

- In `store_sqlite.py`: add `_migrate_release_diff_cache(conn)` adding `default_branch`, `release_branch`, `ci_json`, `ci_fetched_at` columns if absent; update `_EXPECTED_COLS["release_diff_cache"]` (`line 1805`) so `_schema_current` detects unmigrated DBs; update `get_release_diff_cache` / `set_release_diff_cache` and add `delete_release_diff_cache(path, repo_key)`.
- In `repos.py`: call `sq.delete_release_diff_cache(sq.db_path(), name)` inside `unregister_repo`.
- In `repos_release.py`:
  - Validate `cached_row.default_branch == default_branch and cached_row.release_branch == release_branch`.
  - When `age < _diff_ttl_seconds()` and `not force`: return cached `count` immediately without calling `swr.serve_stale_or_refresh`.
  - Cache `refs_ci.fetch_branch_ci(...)` in `ci_json`/`ci_fetched_at` with `_ci_ttl()` (10m) fresh / `_ci_swr()` (1d) SWR; refresh via `swr.serve_stale_or_refresh` when stale.
  - Always load `cached_row` before `force` live fetch so offline errors fall back to `cached_row.count` AND set `base["fetched_at"] = cached_row.fetched_at`.
  - Remove unused `_RELEASE_DIFF_TTL_SECONDS` / `_RELEASE_DIFF_SWR_SECONDS`.
- In `webapp.py`: add `force: bool = False` to `GET /api/release/status` and pass `force=force`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_release_cache.py tests/test_release.py tests/test_webapp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/repos_release.py src/workagent/repos.py src/workagent/webapp.py tests/test_release_cache.py
git commit -m "[171] Fix release_diff_cache fresh-TTL storm, cache branch CI, add force param"
```

---

## Phase 4: In-Memory Process Caches & Env Cache

### Task 9: Add TTL, Locking & Deepcopy to Process Caches (`_NO_TOOL_DIRS`, `_DB_CACHE`, `release_caps`, `_OPTIONS_CACHE`, `env._SOURCED_CACHE`)

**Files:**
- Modify: `src/workagent/repos.py:437-473`
- Modify: `src/workagent/cli_repo_util.py:15, 60-66`
- Modify: `src/workagent/release_caps.py:15-107`
- Modify: `src/workagent/web_runs_routes.py:450-489`
- Modify: `src/workagent/env.py:108-165`
- Test: `tests/test_process_caches.py`

- [ ] **Step 1: Write failing tests for TTL expiry, mutable reference isolation, non-auth GH error not cached, and `env._SOURCED_CACHE` TTL**

```python
# tests/test_process_caches.py
from workagent import cli_repo_util, release_caps, repos, web_runs_routes


def test_release_caps_returns_isolated_copy(monkeypatch):
    release_caps._CACHE.clear()
    monkeypatch.setattr(release_caps, "_gh_caps", lambda *a, **kw: {"strategy": ["ff-push"], "raw": {"pr_required": False}})
    first = release_caps.get_caps("gh", "/r", "release")
    first["strategy"].append("mutated")
    second = release_caps.get_caps("gh", "/r", "release")
    assert second["strategy"] == ["ff-push"]


def test_no_tool_dirs_expires_after_ttl(monkeypatch, tmp_path):
    repos._NO_TOOL_DIRS.clear()
    now = [100.0]
    monkeypatch.setattr(repos.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(repos, "remote_url", lambda _p: "")
    assert repos._detect_host_cli(tmp_path) is None
    assert str(tmp_path) in repos._NO_TOOL_DIRS
    now[0] = 500.0  # past 300s TTL
    monkeypatch.setattr(repos, "remote_url", lambda _p: "git@github.com:o/r.git")
    monkeypatch.setattr(repos, "_gh_hosts", lambda: {"github.com"})
    assert repos._detect_host_cli(tmp_path) == "gh"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_caches.py -v`
Expected: FAIL (`second["strategy"]` mutated; `_NO_TOOL_DIRS` never expires).

- [ ] **Step 3: Harden all 5 in-memory caches**

- `repos.py`: `_NO_TOOL_DIRS: dict[str, float]` + `_NO_TOOL_LOCK`, 300s TTL.
- `cli_repo_util.py`: `_DB_CACHE: dict[str, tuple[float, str]]` + `_DB_LOCK`, 600s positive TTL / 60s negative TTL.
- `release_caps.py`: `_CACHE_LOCK` + per-key `threading.Lock` single-flight + `copy.deepcopy` on store/return + stop caching non-auth `_gh_caps` `HarnessError` as `pr_required: False` + remove dead `_TTL`.
- `web_runs_routes.py`: `_OPTIONS_LOCK` + per-harness `threading.Lock` single-flight + `copy.deepcopy` + `swr.cache_ttl("WORKAGENT_OPTIONS_TTL_SECONDS", 600)` + timestamp *after* probe + stale fallback on probe error.
- `env.py`: `_SOURCED_LOCK` + `swr.cache_ttl("WORKAGENT_ENV_CACHE_TTL_SECONDS", 60)` alongside `mtime_ns` check.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_process_caches.py tests/test_repos.py tests/test_env.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/repos.py src/workagent/cli_repo_util.py src/workagent/release_caps.py src/workagent/web_runs_routes.py src/workagent/env.py tests/test_process_caches.py
git commit -m "[171] Add TTL, locking, and deepcopy isolation to in-memory process caches"
```

---

## Phase 5: CDC Expansion & Frontend UX Hardening

### Task 10: Expand SQLite `CDC_TABLES` Triggers

**Files:**
- Modify: `src/workagent/store_sqlite.py:395-410`
- Test: `tests/test_cdc.py`

**Interfaces:**
- Produces: CDC events on `GET /api/events` for `pr_candidates_cache`, `issue_cache`, `release_diff_cache`, `repos`, `trackers`, `pr_checks`, `pr_review_threads` in addition to existing 7 tables.

- [ ] **Step 1: Write failing test verifying triggers fire for new cache & metadata tables**

```python
# append to tests/test_cdc.py
def test_cdc_triggers_cover_cache_and_metadata_tables(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_pr_candidates_cache(db, [], source="prs")
    sq.set_issue_cache(db, "jira:mine90", [])
    sq.set_release_diff_cache(db, "demo", 1)
    with sq.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT DISTINCT table_name FROM change_log")}
    assert {"pr_candidates_cache", "issue_cache", "release_diff_cache"} <= tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cdc.py -v`
Expected: FAIL (missing tables in `change_log`).

- [ ] **Step 3: Add the 7 tables to `CDC_TABLES` in `src/workagent/store_sqlite.py`**

```python
CDC_TABLES: tuple[tuple[str, str], ...] = (
    ("worktrees", "ref_key"),
    ("runs", "id"),
    ("flows", "id"),
    ("messages", "message_id"),
    ("pr_cache", "branch"),
    ("prodtest_items", "issue_key"),
    ("schedules", "id"),
    ("pr_candidates_cache", "source"),
    ("issue_cache", "source"),
    ("release_diff_cache", "repo_key"),
    ("repos", "name"),
    ("trackers", "id"),
    ("pr_checks", "worktree_ref"),
    ("pr_review_threads", "worktree_ref"),
)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_cdc.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py tests/test_cdc.py
git commit -m "[171] Expand SQLite CDC triggers to cache and metadata tables"
```

---

### Task 11: Debounced `useServerEvents.ts` + Single-Fetch Dashboard Status

**Files:**
- Modify: `web/src/hooks/useServerEvents.ts`
- Modify: `web/src/lib/queries.ts:53-96, 301-303, 390-402`
- Modify: `web/src/pages/Dashboard.tsx:191-193, 269-284, 779`
- Test: `web/src/hooks/useServerEvents.test.tsx`, `web/src/pages/Dashboard.test.tsx`

- [ ] **Step 1: Write failing Vitest tests for CDC debouncing, new table mappings, and single `/api/status` call**

```tsx
// web/src/hooks/useServerEvents.test.tsx (add test)
it("coalesces bursts of pr_cache CDC events into one invalidation", async () => {
  vi.useFakeTimers()
  const spy = vi.spyOn(qc, "invalidateQueries")
  // dispatch 10 pr_cache change events within 50ms
  for (let i = 0; i < 10; i++) {
    es.emit("change", { data: JSON.stringify({ seq: i + 1, table: "pr_cache", row_key: `b-${i}`, op: "update", created_at: 1 }) })
  }
  vi.advanceTimersByTime(200)
  const statusCalls = spy.mock.calls.filter((c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(queryKeys.statusAll))
  expect(statusCalls).toHaveLength(1)
  vi.useRealTimers()
})
```

- [ ] **Step 2: Run Vitest to verify it fails**

Run: `cd web && npx vitest run src/hooks/useServerEvents.test.tsx`
Expected: FAIL (`statusCalls` length is 10 instead of 1).

- [ ] **Step 3: Implement debounced invalidation queue in `useServerEvents.ts` and consolidate Dashboard status queries**

- In `useServerEvents.ts`:
  - Add `scheduleInvalidate(key: readonly unknown[])` with a 150ms `setTimeout` coalescing map (cleared on unmount).
  - Handle `pr_candidates_cache`, `issue_cache`, `release_diff_cache`, `repos`, `trackers`, `pr_checks`, `pr_review_threads`, and add `candidatesPrs` / `candidatesWorktrees` to `worktrees` + reconnect handler.
- In `queries.ts`:
  - Make `useStatusAll()`, `useStatusInactive()`, `useStatusDeleted()` share `["status", "all", Boolean(refresh)]` fetching `api.statusAll({ includeInactive: true, includeDeleted: true, refresh })`, with `select` projections for inactive/deleted rows.
  - Remove dead `refetchOnWindowFocus: true` from 30d/1h queries (set `refetchOnWindowFocus: false` where CDC + polling own freshness).
  - Add `enabled: Boolean(id)` to `useRun(id)`.
  - Expand `invalidateAfterRun(qc)` to include `candidatesPrs`, `candidatesWorktrees`, `["candidates", "issues"]`, `prodtests`, `["release"]`, `["worktree-files"]`, `["worktree-diff"]`, `["worktree-timeline"]`.
- In `Dashboard.tsx`: update stale `"Refreshes every 15 s"` copy to reflect SSE live updates.

- [ ] **Step 4: Run Vitest to verify pass**

Run: `cd web && npx vitest run src/hooks/useServerEvents.test.tsx src/pages/Dashboard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useServerEvents.ts web/src/lib/queries.ts web/src/pages/Dashboard.tsx web/src/hooks/useServerEvents.test.tsx
git commit -m "[171] Debounce CDC SSE invalidations and deduplicate Dashboard status fetches"
```

---

### Task 12: Fix `CandidatesCard`, `ReleaseCard` & Remaining Frontend Cache/Mutation Bugs + Docs Parity

**Files:**
- Modify: `web/src/lib/api.ts` (add `{ force?: boolean }` to `candidatesPrs` & `releaseStatus`)
- Modify: `web/src/components/CandidatesCard.tsx:45-135, 180, 396`
- Modify: `web/src/components/ReleaseCard.tsx:109-175`
- Modify: `web/src/pages/Trackers.tsx`, `web/src/pages/Launch.tsx`, `web/src/pages/WorktreeDetail.tsx`, `web/src/pages/RunDetail.tsx`, `web/src/pages/Settings.tsx`, `web/src/pages/WorktreeWorkspace.tsx`
- Modify: `web/src/components/RunWatcher.tsx`, `web/src/components/WorktreeWatcherOverrides.tsx`, `web/src/components/StartForm.tsx`, `web/src/components/SearchableSelect.tsx`
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `web/src/components/CandidatesCard.test.tsx`, `web/src/components/ReleaseCard.test.tsx`

- [ ] **Step 1: Write failing tests for `CandidatesCard` (`needsBroad` uses `force=false`, Refresh updates subscribed key) and `ReleaseCard` Refresh**

```tsx
// web/src/components/CandidatesCard.test.tsx (add test)
it("uses force=false for broad issues even when range=365 is persisted", async () => {
  localStorage.setItem("workagent.issuesFilter", JSON.stringify({ range: "365", scope: { mine: true, all: false } }))
  const spy = vi.spyOn(api, "candidatesIssues")
  renderWithClient(<CandidatesCard />)
  await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.objectContaining({ days: 365, mine: true })))
  for (const call of spy.mock.calls) {
    expect(call[0]?.force).not.toBe(true)
  }
})
```

- [ ] **Step 2: Run Vitest to verify it fails**

Run: `cd web && npx vitest run src/components/CandidatesCard.test.tsx`
Expected: FAIL (`force: true` is sent).

- [ ] **Step 3: Apply frontend fixes & update `web/API_CONTRACT.md` / `web/FEATURE_INVENTORY.md`**

- `CandidatesCard.tsx`:
  - Line 92: `useCandidatesIssues(false, broadRange, broadMine, needsBroad)`.
  - `handleRefresh()`: fetch `api.candidatesPrs({ force: true })`, `api.candidatesIssues({ force: true, days: 90, mine: true })`, optional broad variant, `api.candidatesWorktrees()`, and write each directly into the subscribed cache keys via `qc.setQueryData(...)`.
  - Delete the 0ms `prsStale` `useEffect` block (lines 55-64).
  - Replace hardcoded `"last 30 days"` copy with dynamic label from `filterProbe.range`.
- `ReleaseCard.tsx`:
  - Add Refresh button calling `api.releaseStatus(name, { force: true })` + `qc.setQueryData(queryKeys.releaseStatus(name), fresh)`.
  - Tie `<RefreshOverlay />` to `releaseQueries.some((q) => q.isFetching)`.
  - Slow poll to 60s when `useServerEventsConnected()` is true.
- Mutations & ad-hoc fetches:
  - `Trackers.tsx`: invalidate `["candidates", "issues"]`, `["issues"]`, `candidatesPrs`, `statusAll`.
  - `Launch.tsx`: `handleRefreshIssues` updates `queryKeys.candidatesIssues(false, 90, true)` and invalidates `["issues"]`.
  - `WorktreeDetail.tsx`: `launchTerminal` invalidates `statusAll` + `runs`.
  - `RunDetail.tsx`: `handleRerun` calls `useCreateRun()` so `awaitRunThenInvalidate` runs.
  - `Settings.tsx`: await run completion before invalidating `config`.
  - `WorktreeWorkspace.tsx`: pass `onState` callback to `useRunEvents` to invalidate `queryKeys.run(activeRunId)` and `queryKeys.runs` on terminal event.
  - `WorktreeWatcherOverrides.tsx`: use TanStack Query hook `useWorktreeWatcher(ref)`.
  - `SearchableSelect.tsx` / `StartForm.tsx` / `Launch.tsx`: remove `loadedRef` ad-hoc caching where `fetchOptions` is used for issues; pass options sourced from `useCandidatesIssues(false, 90, true)`.
  - `RunWatcher.tsx`: loop over all newly-terminal runs (not `data.find`).
- Update `web/API_CONTRACT.md` and `web/FEATURE_INVENTORY.md` for `?force=true` on `/api/candidates/prs` and `/api/release/status`, plus the expanded CDC table list.

- [ ] **Step 4: Run full frontend & backend test suites**

Run: `cd web && npm test && npm run build && cd .. && uv run pytest -v`
Expected: All unit tests and production web build PASS.

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "[171] Fix CandidatesCard, ReleaseCard, and frontend cache invalidation bugs"
```
