# Release Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Release action: `workagent release` CLI (MR-open, FF-only auto-merge, one remote-rebase retry, fail-back), SWR diff cache, schema columns, web mirror, Repos overlay button, Dashboard Ready-to-release card.

**Architecture:** New `cli_release.py` module owns the release flow (CLI owns logic per parity checklist); `repos.py` gains branch detection/persistence helpers; `store_sqlite.py` gains two nullable columns via idempotent `ADD COLUMN` migrations following the `is_enabled` v9 pattern; `release_diff_cache` table follows the `pr_candidates_cache` SWR shape; `webapp.py`/`web_args.py` mirror the CLI flags; frontend adds `ReleaseButton.tsx` + `ReleaseCard.tsx` reusing `RefreshOverlay` and the CI badge pattern.

**Tech Stack:** Python (Typer CLI, stdlib sqlite3, no ORM), FastAPI web backend, React + TS + TanStack Query frontend, gh/glab host CLIs for MR ops.

**Spec:** `docs/superpowers/specs/2026-10-14-release-action-design.md`

## Global Constraints

- The word Deploy MUST NOT appear in command names, flags, columns, buttons, or components — Release everywhere.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- SQLite writes use 30s `busy_timeout`/WAL; background refresh threads daemon + exactly-once per key, swallow exceptions.
- Python files and React components/classes must not exceed 500 lines.
- CHANGELOG.md entries <= 150 chars, append issue ID in brackets (`[no-issue]` for unlinked work).
- The harness runtime is named `harness` everywhere; never `runtime`.
- New/renamed CLI flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURES-NOTES.md` + frontend control + `tests/test_cli.py` + `tests/test_webapp.py` in the same PR (parity checklist; `test_specs_mirror_cli_flags` fails on drift).
- NEVER force-push; NEVER direct-push to release; NEVER squash or merge-commit in the release flow.

## Review Focus

- Release branch deleted remotely between diff-gate and MR creation: `rev-list` succeeds, MR create 404s — expect a clear host-surfaced error with branches untouched, not a traceback.
- `default_branch == release_branch` (misconfigured same value): expect exit 2 usage error, never an MR create attempt.
- `git fetch origin` fails offline: diff gate must serve stale cache (or report "cannot reach remote" when empty) rather than failing the whole status read.
- Shallow clone (depth 7 default) missing release-branch history: `rev-list release..default` counts wrong — expect `git fetch origin <default> <release>` (or `--unshallow` fallback) anchoring both tips before counting.
- Rapid re-click on the Release button while a release run is live: expect a 409-per-target-key second run refused, not two concurrent MR creates on the same repo.

---

## File Map

| File | Responsibility |
|---|---|
| `src/workagent/store_sqlite.py` | SCHEMA: `repos.default_branch`, `repos.release_branch` columns + `release_diff_cache` table; `_REQUIRED_COLS`; `_schema_current` probes; `_migrate_repos_release()` ADD COLUMN migration; `get_release_diff_cache`/`set_release_diff_cache` accessors |
| `src/workagent/repos.py` | `detect_release_branch(path)` (master-preferred-on-Jibit detector), `detect_default_branch` reuse of `default_branch(path)`, `release_diff_count(path, default_branch, release_branch)` (fetch + rev-list), `backfill_release_branches()` never-raises helper |
| `src/workagent/cli_release.py` (new) | `release` Typer command + `release_status` read helper shared by CLI `--json` and the web status endpoint; helpers `_open_mr`, `_set_ff_auto_merge`, `_rebase_retry`, each asserting FF-only argv |
| `src/workagent/cli_manage.py` | `repo add --release-branch`, `repo update NAME --default-branch/--release-branch` (new subcommand), `repo list` shows both branches |
| `src/workagent/refs.py` | `fetch_ci_status(tool, cwd, branch=None)` — add optional branch param (default preserves current callers) |
| `src/workagent/web_args.py` | `"release"` BOOL/VAL entries; `SPECS["release"]`; `_validate_args` acceptance |
| `src/workagent/webapp.py` | `GET /api/release/status`, run passthrough for `release` |
| `web/src/lib/api.ts` | `ReleaseStatus` type + `releaseStatus(name)` fetcher |
| `web/src/lib/queries.ts` | `useReleaseStatus(name)` (`staleTime` 1h, `gcTime` 1h) + `RELEASE_SWR_MS` constant |
| `web/src/components/ReleaseButton.tsx` (new) | Shared Release overlay button (confirm dialog → run → toast → navigate) |
| `web/src/components/ReleaseCard.tsx` (new) | Dashboard "Ready to release" card (SWR rows, overlay refresh, CI badge) |
| `web/src/pages/Repos.tsx` | Per-row Release overlay button + release-branch field in Add form + per-row edit |
| `web/src/pages/Dashboard.tsx` | Mount `<ReleaseCard />` after `<StatusTable>` |
| `web/API_CONTRACT.md`, `web/FEATURES-NOTES.md` | Contract entries + feature note |
| `tests/test_cli_release.py` (new) | CLI gate/argv/dry-run tests |
| `tests/test_release_cache.py` (new) | SWR cache tests |
| `tests/test_cli.py`, `tests/test_webapp.py` | Parity + endpoint additions |

---

### Task 1: Schema columns + diff cache table

**Files:**
- Modify: `src/workagent/store_sqlite.py:14-53` (SCHEMA), `:328-338` (_REQUIRED_COLS), `:441-448` (_schema_current), `:458-488` (init_db + new `_migrate_repos_release`)
- Test: `tests/test_release_cache.py` (new)

**Interfaces:**
- Consumes: existing `connect()`, `init_db()` migration chain, `swr.serve_stale_or_refresh` primitive.
- Produces: `get_release_diff_cache(db, repo_key) -> tuple[int | None, str]`, `set_release_diff_cache(db, repo_key, count) -> None` for Task 3; columns `default_branch`, `release_branch` on `repos` for Task 2.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_release_cache.py
from workagent import store_sqlite as sq

def test_release_columns_and_cache_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "rx", "/r/rx", "jira:RX", remote="https://x/rx.git", tool="gh")
    assert sq.get_release_diff_cache(db, "rx") == (None, "")
    sq.set_release_diff_cache(db, "rx", 7)
    count, fetched = sq.get_release_diff_cache(db, "rx")
    assert count == 7 and fetched != ""
    cols = {r[1] for r in sq.connect(db).execute("PRAGMA table_info(repos)")}
    assert {"default_branch", "release_branch"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_release_cache.py -q`
Expected: FAIL with `AttributeError` / `no such column` (accessors and columns do not exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
# In SCHEMA, repos table: append columns (keep existing lines, add):
#   default_branch TEXT NOT NULL DEFAULT '', release_branch TEXT NOT NULL DEFAULT '';
# New table:
# CREATE TABLE IF NOT EXISTS release_diff_cache (
#   repo_key TEXT PRIMARY KEY NOT NULL, count INTEGER NOT NULL, fetched_at TEXT NOT NULL);
```

```python
# _REQUIRED_COLS: add "release_diff_cache": ("repo_key", "count", "fetched_at"),
# (repos keeps ("key_ref", "path", "name") — new columns are optional with DEFAULT '').
```

```python
# _schema_current: add probe (same shape as the is_enabled probe):
rcols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
if not {"default_branch", "release_branch"} <= rcols:
    return False
tables = {...}  # add "release_diff_cache" to the required-tables set check
```

```python
def _migrate_repos_release(conn) -> None:
    """Idempotent ADD COLUMN for default_branch/release_branch (v9 is_enabled pattern)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if "default_branch" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN default_branch TEXT NOT NULL DEFAULT ''")
    if "release_branch" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN release_branch TEXT NOT NULL DEFAULT ''")
```

Wire `_migrate_repos_release(conn)` into `init_db()` after `_migrate_tracker_enabled(conn)`.

```python
def get_release_diff_cache(db: Path, repo_key: str) -> tuple[int | None, str]:
    """(count, fetched_at ISO); (None, '') on miss. Never raises on empty table."""
    from datetime import timezone
    try:
        conn = connect(db)
    except Exception:
        return None, ""
    try:
        row = conn.execute("SELECT count, fetched_at FROM release_diff_cache WHERE repo_key = ?",
                           (repo_key,)).fetchone()
    except Exception:
        return None, ""
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if row is None:
        return None, ""
    return int(row[0]), str(row[1] or "")


def set_release_diff_cache(db: Path, repo_key: str, count: int) -> None:
    from datetime import datetime, timezone
    conn = connect(db)
    try:
        conn.execute("INSERT INTO release_diff_cache (repo_key, count, fetched_at) VALUES (?, ?, ?)"
                     " ON CONFLICT(repo_key) DO UPDATE SET count=excluded.count, fetched_at=excluded.fetched_at",
                     (repo_key, int(count), datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_release_cache.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py tests/test_release_cache.py
git commit -m "Release schema: branch columns plus diff cache table [no-issue]"
```

---

### Task 2: Branch detection + persistence helpers

**Files:**
- Modify: `src/workagent/repos.py` (append helpers + extend `register_repo`)
- Modify: `src/workagent/store_trackers.py:198-242` (`register_repo_row` gains `default_branch`/`release_branch` kwargs, same None-preserving pattern as `fetch_enabled`)
- Test: `tests/test_release_cache.py` (append)

**Interfaces:**
- Consumes: `repos.default_branch(path)` (existing host→origin/HEAD→names detector), `remote_url(path)`, `_detect_host_cli(path)`; Task 1 columns.
- Produces: `detect_release_branch(path: Path) -> str`, `release_diff_count(path: Path, default_branch: str, release_branch: str) -> int`, `backfill_release_branches() -> None`, extended `register_repo(..., default_branch="", release_branch="")` for Tasks 3–4.

- [ ] **Step 1: Write the failing test**

```python
def test_detect_release_branch_prefers_master_on_gitlab(tmp_path, monkeypatch):
    from workagent import repos as _repos
    monkeypatch.setattr(_repos, "_detect_host_cli", lambda p: "glab")
    monkeypatch.setattr(_repos, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(Exception("no")))
    # Simulate: master exists, main does not — via rev-parse probe stub
    def _fake(*a, **k):
        if "--verify" in a and "master" in " ".join(a):
            return "sha"
        raise Exception("no ref")
    monkeypatch.setattr(_repos, "run_cmd", _fake)
    assert _repos.detect_release_branch(tmp_path) == "master"


def test_register_repo_seeds_branches(tmp_path, monkeypatch):
    from workagent import repos as _repos
    from workagent import store_sqlite as _sq
    db = tmp_path / "state.db"
    _sq.init_db(db)
    monkeypatch.setattr(_sq, "db_path", lambda: db)
    monkeypatch.setattr(_repos, "default_branch", lambda p: "main")
    monkeypatch.setattr(_repos, "detect_release_branch", lambda p: "master")
    monkeypatch.setattr(_repos, "remote_url", lambda p: "https://h/r.git")
    monkeypatch.setattr(_repos, "_detect_host_cli", lambda p: "glab")
    _repos.register_repo("rx", tmp_path, tracker_key="jira:RX")
    rows = {r[1] for r in _sq.connect(db).execute("PRAGMA table_info(repos)")}
    assert {"default_branch", "release_branch"} <= rows
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_release_cache.py -q`
Expected: FAIL with `AttributeError: detect_release_branch`.

- [ ] **Step 3: Write minimal implementation**

```python
def detect_release_branch(path: Path) -> str:
    """Release branch for *path*: '' when none configured.

    Jibit (glab-tooled) repos prefer `master` when it exists, else `main`
    when it exists, else ''. Non-glab repos return '' (release support is
    opt-in via `repo update --release-branch`). Never raises: probe
    failures mean ''.
    """
    if _detect_host_cli(path) != "glab":
        return ""
    for candidate in ("master", "main"):
        for ref in (f"origin/{candidate}", candidate):
            try:
                run_cmd("git", "-C", str(path), "rev-parse", "--verify", ref)
                return candidate
            except HarnessError:
                continue
    return ""


def release_diff_count(path: Path, default_branch: str, release_branch: str) -> int:
    """Commits *default_branch* has that *release_branch* lacks (int >= 0).

    Fetches both tips first so shallow clones (depth 7) count correctly;
    falls back to --unshallow when the release ref is missing locally.
    Raises HarnessError when offline or when either ref is unknown.
    """
    if not default_branch or not release_branch:
        raise HarnessError("release diff needs both default and release branches", exit_code=2)
    if default_branch == release_branch:
        raise HarnessError(f"default and release branch are the same ({default_branch!r})",
                           exit_code=2)
    run_cmd("git", "-C", str(path), "fetch", "origin", default_branch, release_branch)
    try:
        out = run_cmd("git", "-C", str(path), "rev-list", "--count",
                      f"origin/{release_branch}..origin/{default_branch}")
    except HarnessError:
        run_cmd("git", "-C", str(path), "fetch", "--unshallow", "origin")
        out = run_cmd("git", "-C", str(path), "rev-list", "--count",
                      f"origin/{release_branch}..origin/{default_branch}")
    return int((out or "0").strip())


def backfill_release_branches() -> None:
    """Seed default/release branches for repos missing them. Never raises.

    Per-repo failures (unreachable host, missing checkout) leave '' and
    emit a stderr warning — backfill must never break startup.
    """
    from . import store as _store
    for name, info in _store.load_repos().items():
        if info.get("default_branch") and "release_branch" in info:
            continue
        p = Path(str(info.get("path", ""))).expanduser()
        try:
            default = info.get("default_branch") or default_branch(p)
        except HarnessError as e:
            print(f"warning: cannot detect default branch for {name}: {e}", file=sys.stderr)
            default = ""
        try:
            release = info.get("release_branch", "")
            if not release and default:
                release = detect_release_branch(p)
        except HarnessError as e:
            print(f"warning: cannot detect release branch for {name}: {e}", file=sys.stderr)
            release = ""
        try:
            from . import store_sqlite as _sq
            db = _sq.db_path()
            _sq.connect(db).execute(
                "UPDATE repos SET default_branch = ?, release_branch = ? WHERE key_ref = ?",
                (default, release, name)).close()
        except Exception as e:
            print(f"warning: cannot persist branches for {name}: {e}", file=sys.stderr)
```

`register_repo`: add `default_branch: str = ""` and `release_branch: str = ""` params; when empty, auto-detect (`default_branch(path)` guarded try/except → `""`; `detect_release_branch(path)`); pass through to `register_repo_row(...)`. `register_repo_row` in `store_trackers.py`: add the two kwargs (None preserves stored, same as `fetch_enabled`), extend the INSERT/UPSERT column lists.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_release_cache.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/repos.py src/workagent/store_trackers.py tests/test_release_cache.py
git commit -m "Release branch detection plus persistence helpers [no-issue]"
```

---

### Task 3: SWR diff-status read path

**Files:**
- Modify: `src/workagent/repos.py` (append `release_status`)
- Test: `tests/test_release_cache.py` (append)

**Interfaces:**
- Consumes: Task 1 accessors, Task 2 `release_diff_count`, `swr.serve_stale_or_refresh`, `store.load_repos`.
- Produces: `release_status(repo_name: str, force: bool = False) -> dict` with keys `{repo, path, default_branch, release_branch, diff_count, releasable, reason, stale}` for Tasks 4–5.

- [ ] **Step 1: Write the failing test**

```python
def test_release_status_serves_stale_without_fetch(tmp_path, monkeypatch):
    from workagent import repos as _repos
    from workagent import store_sqlite as _sq
    from datetime import datetime, timezone
    db = tmp_path / "state.db"
    _sq.init_db(db)
    monkeypatch.setattr(_sq, "db_path", lambda: db)
    _sq.register_repo_row(db, "rx", "/r/rx", "jira:RX")
    _sq.connect(db).execute("UPDATE repos SET default_branch='main', release_branch='master' WHERE key_ref='rx'").close()
    # Expired-but-present cache (25h old, SWR window 1d): no live fetch expected
    old = "2020-01-01T00:00:00+00:00"
    _sq.connect(db).execute("INSERT INTO release_diff_cache VALUES ('rx', 4, ?)", (old,)).close()
    monkeypatch.setattr(_repos, "release_diff_count", lambda *a: (_ for _ in ()).throw(AssertionError("must not fetch")))
    from workagent import store as _store
    monkeypatch.setattr(_store, "load_repos", lambda: {"rx": {"path": "/r/rx", "default_branch": "main", "release_branch": "master"}})
    st = _repos.release_status("rx")
    assert st["diff_count"] == 4 and st["stale"] is True and st["releasable"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_release_cache.py::test_release_status_serves_stale_without_fetch -q`
Expected: FAIL with `AttributeError: release_status`.

- [ ] **Step 3: Write minimal implementation**

```python
_RELEASE_DIFF_TTL_SECONDS = 3600
_RELEASE_DIFF_SWR_SECONDS = max(_RELEASE_DIFF_TTL_SECONDS * 10, 24 * 3600)

def release_status(repo_name: str, force: bool = False) -> dict:
    """Read-only releasability for *repo_name* (SWR over release_diff_cache).

    Fresh (<1h) serves instantly; expired-but-present (<1d) serves stale +
    one background revalidation; empty/force fetches live. Offline fetch
    with stale rows serves stale + reason; offline with no rows reports
    reason "cannot reach remote". Never raises for unknown repo — returns
    releasable False with reason.
    """
    from datetime import datetime, timezone
    from . import store as _store, swr as _swr, store_sqlite as _sq
    info = _store.load_repos().get(repo_name)
    if not info:
        return {"repo": repo_name, "path": "", "default_branch": "", "release_branch": "",
                "diff_count": 0, "releasable": False, "reason": "unknown repo", "stale": False}
    default = str(info.get("default_branch", "") or "")
    release = str(info.get("release_branch", "") or "")
    base = {"repo": repo_name, "path": str(info.get("path", "")), "default_branch": default,
            "release_branch": release, "diff_count": 0, "releasable": False, "reason": "", "stale": False}
    if not release:
        base["reason"] = "no release branch"
        return base
    if not default:
        base["reason"] = "no default branch"
        return base
    try:
        db = _sq.db_path()
    except Exception:
        db = None
    cached, fetched_at = (None, "")
    if db is not None and not force:
        cached, fetched_at = _sq.get_release_diff_cache(db, repo_name)
    def _live() -> int:
        return release_diff_count(Path(str(info["path"])).expanduser(), default, release)
    def _finish(count: int, stale: bool, reason: str = "") -> dict:
        base.update(diff_count=count, releasable=count > 0, stale=stale,
                    reason="" if count > 0 else "already in sync")
        return base
    if cached is not None and not force:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
        except Exception:
            age = float("inf")
        if age < _RELEASE_DIFF_TTL_SECONDS:
            return _finish(cached, False)
        if age < _RELEASE_DIFF_SWR_SECONDS:
            def _refresh() -> None:
                try:
                    _sq.set_release_diff_cache(db, repo_name, _live())
                except Exception:
                    pass
            _swr.serve_stale_or_refresh(stale=True, is_fresh=False, refresh=_refresh,
                                        key=f"release:{repo_name}")
            return _finish(cached, True)
    try:
        count = _live()
    except HarnessError as e:
        if cached is not None:
            base.update(diff_count=cached, releasable=cached > 0, stale=True,
                        reason=f"cannot reach remote ({e}); showing cached count")
            return base
        base["reason"] = f"cannot reach remote ({e})"
        return base
    if db is not None:
        try:
            _sq.set_release_diff_cache(db, repo_name, count)
        except Exception:
            pass
    return _finish(count, False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_release_cache.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/repos.py tests/test_release_cache.py
git commit -m "Release diff status with SWR cache reads [no-issue]"
```

---

### Task 4: `release` CLI command + `repo update`

**Files:**
- Create: `src/workagent/cli_release.py`
- Modify: `src/workagent/cli.py` (import module for registration — check how `cli_cleanup`/`cli_manage` register; follow the same import line)
- Modify: `src/workagent/cli_manage.py` (`repo add --release-branch`, new `repo update`, `repo list` branch columns)
- Test: `tests/test_cli_release.py` (new)

**Interfaces:**
- Consumes: Task 3 `release_status`, Task 2 `release_diff_count`, `_repo_tool` host detection, `run_cmd`, `_fail`/`_print_result`/TTY confirm.
- Produces: `workagent release REPO [--yes] [--dry-run] [--json]` CLI; `repo update` CLI; both consumed by Task 5 web mirror.

- [ ] **Step 1: Write the failing test**

```python
def test_release_no_branch_gate_exits_2(monkeypatch):
    from workagent import cli_release as _rel
    monkeypatch.setattr(_rel.repos, "release_status",
                        lambda name, force=False: {"repo": name, "path": "/r", "default_branch": "main",
                            "release_branch": "", "diff_count": 0, "releasable": False,
                            "reason": "no release branch", "stale": False})
    with pytest.raises(Exception) as ei:
        _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    assert "no release branch" in str(ei.value).lower()


def test_release_ff_argv_per_host(monkeypatch):
    from workagent import cli_release as _rel
    calls = []
    monkeypatch.setattr(_rel, "run_cmd", lambda *a, **k: calls.append(a) or "https://h/mr/1")
    monkeypatch.setattr(_rel.repos, "release_status",
                        lambda name, force=False: {"repo": name, "path": "/r", "default_branch": "main",
                            "release_branch": "master", "diff_count": 3, "releasable": True,
                            "reason": "", "stale": False})
    monkeypatch.setattr(_rel, "_host_tool", lambda path: "glab")
    monkeypatch.setattr(_rel, "_existing_open_mr", lambda tool, path, d, r: "")
    monkeypatch.setattr(_rel, "_merge_state", lambda url, path: {"state": "merged"})
    _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    flat = " ".join(" ".join(c) for c in calls)
    assert "--ff-only" in flat and "--squash" not in flat and "--merge" not in flat.replace("--auto-merge", "")


def test_release_dry_run_runs_nothing(monkeypatch):
    from workagent import cli_release as _rel
    monkeypatch.setattr(_rel, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    monkeypatch.setattr(_rel.repos, "release_status",
                        lambda name, force=False: {"repo": name, "path": "/r", "default_branch": "main",
                            "release_branch": "master", "diff_count": 2, "releasable": True,
                            "reason": "", "stale": False})
    _rel.run_release("rx", yes=True, dry_run=True, json_output=False)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -q`
Expected: FAIL with `ModuleNotFoundError: cli_release`.

- [ ] **Step 3: Write minimal implementation**

```python
"""`release` command: ship default -> release via FF-only auto-merge MR."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from . import repos
from .cli_core import EXIT_GENERAL, EXIT_USAGE, _catch_harness_errors, _fail, _print_result, app, eprint
from .cli_repo_util import _repo_tool
from .errors import HarnessError, run_cmd


@app.command("release")
@_catch_harness_errors
def release(
    repo: str = typer.Argument(..., help="Registered repo name or path."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Open a default->release MR with FF-only auto-merge (one host rebase retry)."""
    result = run_release(repo, yes=yes, dry_run=dry_run, json_output=json_output)
    _print_result(result, json_output)


def run_release(repo: str, *, yes: bool, dry_run: bool, json_output: bool) -> dict:
    from . import store as _store
    registry = _store.load_repos()
    name = repo if repo in registry else ""
    if not name:
        for n, info in registry.items():
            if str(info.get("path", "")) == repo:
                name = n
                break
    if not name:
        _fail(f"unknown repo: {repo}", EXIT_USAGE)
    st = repos.release_status(name)
    if not st["release_branch"]:
        _fail(f"{name}: no release branch configured (repo update {name} --release-branch MASTER).", EXIT_USAGE)
    if st["default_branch"] == st["release_branch"]:
        _fail(f"{name}: default and release branch are the same.", EXIT_USAGE)
    if not st["releasable"]:
        note = {"repo": name, "merged": False, "mr_url": "", "note": "already in sync, nothing to release"}
        return note
    plan = {"repo": name, "default_branch": st["default_branch"],
            "release_branch": st["release_branch"], "diff_count": st["diff_count"],
            "mr_title": f"Release {st['default_branch']} -> {st['release_branch']}"}
    if dry_run:
        eprint(f"dry-run: would open MR {plan['mr_title']} ({plan['diff_count']} commits)")
        return {**plan, "dry_run": True, "merged": False, "mr_url": ""}
    if not yes and sys.stdin.isatty():
        try:
            answer = input(f"Open release MR {plan['mr_title']}? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    path = st["path"]
    tool = _host_tool(path)
    mr_url = _existing_open_mr(tool, path, st["default_branch"], st["release_branch"])
    if not mr_url:
        mr_url = _open_mr(tool, path, st["default_branch"], st["release_branch"], plan["mr_title"])
    try:
        _set_ff_auto_merge(tool, mr_url, cwd=path)
    except HarnessError:
        _rebase_retry(tool, mr_url, cwd=path)
        _set_ff_auto_merge(tool, mr_url, cwd=path)
    state = _merge_state(mr_url, path)
    merged = (state.get("state") or "").lower() == "merged"
    return {**plan, "merged": merged, "mr_url": mr_url}
```

Helpers `_host_tool` (via `_repo_tool` with path fallback), `_existing_open_mr` (list open MRs default→release via `refs.fetch_open_prs`, adopt URL on match, else ""), `_open_mr` (glab `mr create --source-branch D --target-branch R --title T` / gh `pr create --base R --head D --title T`, returns URL), `_set_ff_auto_merge` (glab `mr merge --ff-only --auto-merge URL` / gh `pr merge --auto --ff-only URL`, cwd=repo), `_rebase_retry` (glab `mr rebase URL` once; gh: no host rebase — raise original), `_merge_state` (`refs.fetch_pr_merge_state`). Fail-back: any HarnessError after retry → `_fail(f"release MR left open: {mr_url}: {e}", EXIT_GENERAL)` — branches untouched.

`repo update` in `cli_manage.py`: `repo_update(name, default_branch: Optional[str], release_branch: Optional[str], json_output)` — UPDATE repos row (empty string clears release); unknown name → exit 2. `repo add`: `--release-branch` option passed to `register_repo`. `repo list`: add `default_branch`/`release_branch` to row dicts + columns list.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py tests/test_release_cache.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_release.py src/workagent/cli.py src/workagent/cli_manage.py src/workagent/repos.py src/workagent/store_trackers.py tests/test_cli_release.py
git commit -m "Release CLI: FF-only auto-merge MR plus repo update [no-issue]"
```

---

### Task 5: Web mirror + status endpoint

**Files:**
- Modify: `src/workagent/web_args.py:36-68` (release BOOL/VAL/SPECS + `_validate_args`)
- Modify: `src/workagent/webapp.py` (`GET /api/release/status`, release run passthrough)
- Modify: `web/API_CONTRACT.md`, `web/FEATURES-NOTES.md`
- Test: `tests/test_webapp.py` (append: specs-mirror covered by existing test; add endpoint test)

**Interfaces:**
- Consumes: Task 4 CLI flags, Task 3 `release_status`.
- Produces: `/api/release/status?name=` JSON; `release` run target; contract docs for Task 6.

- [ ] **Step 1: Write the failing test**

```python
def test_release_status_endpoint(client):
    r = client.get("/api/release/status", params={"name": "rx"})
    assert r.status_code == 200
    body = r.json()
    assert {"repo", "default_branch", "release_branch", "diff_count", "releasable", "reason", "stale"} <= set(body)
```

(Check `tests/test_webapp.py` for the existing client fixture name — reuse it verbatim; if it is named differently, use that name.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_webapp.py -q -k release_status`
Expected: FAIL with 404 (no such route).

- [ ] **Step 3: Write minimal implementation**

```python
# web_args.py:
# BOOL_FLAGS: "release": ("--yes", "--dry-run", "--json"),
# VAL_FLAGS:  "release": (),
# SPECS: "release": {"confirm": True, "force": True, "key": lambda args: f"release:{_first_positional(args, ())}"},
# _validate_args: accept "release" with the above flags (follow the "cleanup" branch shape).
```

```python
# webapp.py:
@app.get("/api/release/status")
def release_status(name: str = ""):
    from . import repos as _repos
    return _repos.release_status(name)
```

Run passthrough: add `"release"` to the command dispatch map the same way `"cleanup"` is wired (check `webapp.py` for the SPECS-driven generic runner — if runs dispatch generically off SPECS, no extra code is needed beyond the SPECS entry; verify by reading).

`API_CONTRACT.md`: add `GET /api/release/status?name=` → response shape + `POST /api/runs {command: "release"}` entries. `FEATURES-NOTES.md`: append entry 13 (next number — verify tail first) describing the Release action in one paragraph.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_webapp.py tests/test_cli.py -q`
Expected: PASS (specs-mirror + new endpoint).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py src/workagent/webapp.py web/API_CONTRACT.md web/FEATURES-NOTES.md tests/test_webapp.py
git commit -m "Web mirror for release plus status endpoint [no-issue]"
```

---

### Task 6: `fetch_ci_status` branch param

**Files:**
- Modify: `src/workagent/refs.py` (`fetch_ci_status`)
- Test: extend `tests/test_cli_release.py` (or the existing CI test file — check where `fetch_ci_status` is tested and append there)

**Interfaces:**
- Consumes: existing `fetch_ci_status(tool, cwd)` behavior.
- Produces: `fetch_ci_status(tool, cwd, branch=None)` — None preserves current callers; branch set queries that branch's pipeline for Task 7.

- [ ] **Step 1: Write the failing test**

```python
def test_fetch_ci_status_branch_param(monkeypatch):
    from workagent import refs as _refs
    import inspect
    assert "branch" in inspect.signature(_refs.fetch_ci_status).parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -q -k branch_param`
Expected: FAIL (no `branch` parameter).

- [ ] **Step 3: Write minimal implementation**

Add `branch: str | None = None` param; when set, pass `--branch <branch>` (glab: `glab ci list --branch X` / `gh run list --branch X` — check the current implementation and add the flag to whichever list command it shells). None → identical argv to today (no behavior change for existing callers).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/refs.py tests/test_cli_release.py
git commit -m "CI status gains optional branch param [no-issue]"
```

---

### Task 7: Frontend — API, queries, ReleaseButton

**Files:**
- Modify: `web/src/lib/api.ts` (append `ReleaseStatus` + `releaseStatus`)
- Modify: `web/src/lib/queries.ts` (append `RELEASE_SWR_MS`, `queryKeys.release(name)`, `useReleaseStatus`)
- Create: `web/src/components/ReleaseButton.tsx`
- Test: `npx tsc --noEmit -p tsconfig.app.json` + `npm run build --prefix web`

**Interfaces:**
- Consumes: Task 5 endpoint; `useConfirmedRun` (check `RunActions.tsx:104-115` for exact signature), `RefreshOverlay`, `SearchableSelect`/dialog patterns.
- Produces: `<ReleaseButton repo={name} />` for Tasks 8–9.

- [ ] **Step 1: Write the failing typecheck probe**

```typescript
// Temporary probe file web/src/release.probe.ts (delete after):
import { useReleaseStatus } from "@/lib/queries";
export function probe(name: string) { return useReleaseStatus(name); }
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx tsc --noEmit -p tsconfig.app.json`
Expected: FAIL (`useReleaseStatus` does not exist).

- [ ] **Step 3: Write minimal implementation**

```typescript
// api.ts:
export interface ReleaseStatus {
  repo: string; path: string; default_branch: string; release_branch: string;
  diff_count: number; releasable: boolean; reason: string; stale?: boolean;
}
export async function releaseStatus(name: string): Promise<ReleaseStatus> {
  const r = await fetch(`/api/release/status?name=${encodeURIComponent(name)}`);
  if (!r.ok) throw new Error(`release status ${r.status}`);
  return r.json();
}
```

```typescript
// queries.ts:
export const RELEASE_SWR_MS = 3600_000;
queryKeys: { ..., release: (name: string) => ["release", name] as const },
export function useReleaseStatus(name: string) {
  return useQuery({
    queryKey: queryKeys.release(name),
    queryFn: () => api.releaseStatus(name),
    staleTime: RELEASE_SWR_MS,
    gcTime: RELEASE_SWR_MS,
    enabled: !!name,
  });
}
```

```tsx
// ReleaseButton.tsx: props { repo: string; compact?: boolean }
// - const { data, isPending, isFetching } = useReleaseStatus(repo)
// - const runConfirmed = useConfirmedRun(); const navigate = useNavigate()
// - Not releasable (or pending): ghost icon button disabled, title={reason || "…"} — NEVER hidden.
// - Releasable: Rocket icon button, title=`Release ${default_branch} → ${release_branch} (${diff_count} commits)`.
// - Click: confirm dialog (branches, diff count) → runConfirmed.run("release", [repo, "--yes"]) → toast + navigate to run.
// - While isFetching && !isPending: render <RefreshOverlay /> in the relative parent (no layout shift).
// - Keep under 500 lines; no Deploy wording anywhere.
```

Delete the probe file.

- [ ] **Step 4: Run to verify it passes**

Run: `npx tsc --noEmit -p tsconfig.app.json && npm run build --prefix web`
Expected: clean tsc + successful build.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/queries.ts web/src/components/ReleaseButton.tsx
git commit -m "Release button plus status query hooks [no-issue]"
```

---

### Task 8: Repos page — overlay button + branch fields

**Files:**
- Modify: `web/src/pages/Repos.tsx:190-233` (table rows), Add form (~lines 45-130 — read first), mobile card layout (~234-264)
- Test: `npx tsc --noEmit -p tsconfig.app.json` + build

**Interfaces:**
- Consumes: Task 7 `<ReleaseButton />`, `useReleaseStatus` for the Add-form release-branch default hint.
- Produces: Repos rows with Release overlay buttons; Add form release-branch field; per-row branch edit.

- [ ] **Step 1: Verify current behavior (no test to write — UI wiring)**

Read `web/src/pages/Repos.tsx:45-130` (Add form) and `:234-264` (mobile cards). Confirm the `Repo` type in `api.ts` gains `default_branch`/`release_branch` fields (extend the interface + the `/api/repos` backend serializer in the same step — check `webapp.py` repos endpoint shape first).

- [ ] **Step 2: Implement**

  - `Repo` interface: add `default_branch: string; release_branch: string`.
  - Table rows: add Branch columns (default → release, `—` when empty) + `<ReleaseButton repo={r.name} />` overlay cell (same `group-hover` pattern as StatusTable row actions — check `StatusTableMain.tsx` for the exact class string and reuse it).
  - Mobile cards: same button + branch line.
  - Add form: optional `release_branch` input (placeholder "e.g. master — empty = no release support"), passed as `--release-branch` through the existing `repo add` run path (check `Repos.tsx` handleAdd + `web_args.py "repo add"` VAL_FLAGS — add `"--release-branch"` there too).
  - Per-row edit: inline or dialog edit for both branches → `repo update NAME --default-branch X --release-branch Y` run (add `"repo update"` BOOL/VAL/SPECS entries in `web_args.py` in this task — `"repo update": ("--json",)` BOOL, `("--default-branch", "--release-branch")` VAL).

- [ ] **Step 3: Typecheck + build**

Run: `npx tsc --noEmit -p tsconfig.app.json && npm run build --prefix web`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Repos.tsx web/src/lib/api.ts src/workagent/webapp.py src/workagent/web_args.py
git commit -m "Repos page release button plus branch fields [no-issue]"
```

---

### Task 9: Dashboard ReleaseCard + docs + full verification

**Files:**
- Create: `web/src/components/ReleaseCard.tsx`
- Modify: `web/src/pages/Dashboard.tsx` (mount after `<StatusTable>`, ~line 535)
- Modify: `CHANGELOG.md` (append-only gap insert, verify `git diff` shows only `+`)
- Test: full suite + tsc + build + install + push

**Interfaces:**
- Consumes: Task 7 hooks/button, `useRepos` list, Task 6 branch CI, `RefreshOverlay`, `relativeTime` (check `lib/format.ts`).
- Produces: shipped Release action.

- [ ] **Step 1: Write the component**

```tsx
// ReleaseCard.tsx:
// - const { data: repos } = useRepos()
// - Per-repo useReleaseStatus via a child <ReleaseRow repo={name} /> (hooks per row — one query per repo, enabled when repos loaded).
// - <ReleaseRow>: skipped (returns null) when !releasable; else row: Repo name | default → release | diff count + relative fetch time (exact on hover title) | release-branch CI badge + pipeline link (fetch_ci_status branch param via a small /api query — reuse useStatusAll CI shape if available, else plain badge + link) | <ReleaseButton compact />.
// - Card title: `Ready to release (N)`; N=0 → EmptyState "All repos in sync" (card still renders — never hidden).
// - Loading: skeleton only when repos pending AND no data; else rows + <RefreshOverlay /> while any row refetches.
// - No Deploy wording; under 500 lines.
```

Mount in `Dashboard.tsx` after `<CandidatesCard />` (same pattern, line ~535-537).

CHANGELOG (gap insert after line 3, verify `git diff` shows only additions):

```markdown
- Release action ships default->release via FF-only auto-merge MRs (one host rebase retry, fail-back leaves the MR open): `workagent release REPO`, Repos row button, Dashboard Ready-to-release card [no-issue]
```

- [ ] **Step 2: Full verification**

Run: `.venv/bin/python -m pytest -q` (expect all pass)
Run: `npx tsc --noEmit -p tsconfig.app.json` (expect clean)
Run: `npm run build --prefix web` (expect success)
Run: `./install.sh` (expect Done)

- [ ] **Step 3: Commit + push**

```bash
git add -A
git commit -m "Dashboard Ready-to-release card plus changelog [no-issue]"
git push
```

Expected: `ok main`.

---

## Self-Review

**1. Spec coverage:** §1 outcome → Tasks 4+7+8+9. §2 schema → Tasks 1+2 (`repo update`, Add form, backfill). §3 diff gate → Task 3 (+ Task 1 table). §4 CLI → Task 4 (gates, MR reuse, FF-only, rebase retry, fail-back, exit codes, dry-run, json). §5 web → Task 5 (flags, endpoint, runs, contract). §6 UI → Tasks 7+8+9 (overlay button, card, CI branch link). §7 testing → per-task tests + Task 9 full gate. §8 out of scope → untouched. Gap found and fixed: `_validate_args` + `repo update` web entries folded into Tasks 5/8; `Repo` type branch fields folded into Task 8.

**2. Placeholder scan:** every step has concrete code/commands; no TBD/TODO/"similar to"; error messages and argv spelled out; fixture names flagged where the executor must read first (webapp client fixture, Add-form lines) rather than guessed.

**3. Type consistency:** `release_status` dict keys identical in Tasks 3/4/5/7 (`repo, path, default_branch, release_branch, diff_count, releasable, reason, stale`); `ReleaseStatus` TS mirrors them; `get/set_release_diff_cache(db, repo_key, ...)` signatures match Task 1→3; `run_release(repo, *, yes, dry_run, json_output)` matches tests→implementation.

**4. Review Focus:** five failure modes listed at top; tests pinned: same-branch gate (Task 4 `_fail` + add `test_release_same_branch_exits_2` next to the no-branch test), fetch-offline stale serve (Task 3 `_live` HarnessError branch + add offline-with-cache assertion), shallow-clone anchoring (`--unshallow` fallback in `release_diff_count`, Task 2), concurrent-click 409 (relies on existing run-target 409 — covered by generic run tests, noted), remote-deleted release branch (MR-create 404 surfaces via `_fail` fail-back path — add assertion in Task 4 rebase/fail-back test round).
