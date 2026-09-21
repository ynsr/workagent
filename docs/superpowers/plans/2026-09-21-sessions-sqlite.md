# Sessions on SQLite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist runtime sessions in SQLite with timestamp+random ids, migrate all JSON state, and expose a Sessions page.

**Architecture:** New `src/harness/store_sqlite.py` repository module (stdlib `sqlite3`, no ORM) owns schema v1 + migration; `backend.py` gains a `Runtime` ABC with `OmpRuntime`; `_run_harness` writes session rows around `backend.launch`; `_cleanup_one` squash-merges open PR/MRs; webapp adds `/api/sessions` endpoints consumed by a Sessions page.

**Tech Stack:** Python stdlib `sqlite3`, Typer CLI, FastAPI webapp, React + TanStack Query frontend.

**Spec:** `docs/superpowers/specs/2026-09-21-sessions-sqlite-design.md`

## Global Constraints

- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Human-first: Rich tables by default; `--csv`/`--json` opt in for scripting/agents.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- `uv run pytest -q` green; `cd web && npm run build` + `npx eslint .` clean.
- Reuse existing patterns (`_locked`/`_atomic_replace` seams, `run_cmd` seam, `HarnessError`); YAGNI/no dead code.
- Escape user content in Rich (`rich.markup.escape`); completions stay offline.
- Web has NO test runner (proof = `tsc` build + eslint); Python tests mirror module names.
- Merge locally with `--no-ff`; never push without ask.

## Review Focus

- Same-millisecond session-id collision must retry with a fresh suffix, never crash the launch.
- Migration with corrupt JSON must abort, leave files in place, and exit 2 with the path.
- A runtime that ignores the session-file flag must surface `transcript: missing`, never silently assume.
- Cleanup merge failure must NOT delete the worktree, drop the link, or delete the remote branch.
- `sync --yes` conflict-harness path and `review --all` children must still write session rows (any `_run_harness` caller).

---

## File Structure

- Create `src/harness/store_sqlite.py` — schema v1, repository functions, migration.
- Create `tests/test_store_sqlite.py` — repository + migration tests.
- Modify `src/harness/backend.py` — `Runtime` ABC + `OmpRuntime` + registry.
- Modify `src/harness/cli.py` — `_run_harness` session rows, `_cleanup_one` squash-merge + `--no-squash`, `migrate` command.
- Modify `src/harness/webapp.py` — `/api/sessions` list/detail endpoints.
- Modify `web/src/lib/api.ts`, `web/src/lib/queries.ts` — session types + hooks.
- Create `web/src/pages/Sessions.tsx`, wire into router — Sessions page.
- Modify `tests/test_cli.py`, `tests/test_webapp.py` — new behavior tests.
- Modify `CHANGELOG.md`, `AGENTS.md`, `web/API_CONTRACT.md` — docs (with cutover task).

---

### Task 1: SQLite repository + schema v1

**Files:**
- Create: `src/harness/store_sqlite.py`
- Test: `tests/test_store_sqlite.py`

**Interfaces:**
- Consumes: `store.config_dir()` for the db path (`~/.config/harness/state.db`, `HARNESS_CONFIG_DIR` override in tests).
- Produces: `init_db(path)`, `gen_session_id()`, `insert_session(...)`, `finish_session(id, state)`, `insert_run(session_id, ...)`, `list_sessions()`, `get_session(id)`, `migrate_json(...)` (Task 2), used by Tasks 3–5.

- [ ] **Step 1: Write the failing test**

```python
def test_schema_tables_exist(tmp_path):
    from harness import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"trackers", "repos", "worktrees", "pr_cache",
            "sessions", "runs"} <= tables


def test_session_id_shape():
    from harness import store_sqlite as sq
    sid = sq.gen_session_id()
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z-\d{4}", sid)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_sqlite.py -q`
Expected: FAIL with "No module named 'harness.store_sqlite'" (use `PYTHONPATH=src` via conftest `sys.path` — follow `tests/test_store.py` import style: `from harness import store`).

- [ ] **Step 3: Write minimal implementation**

```python
"""SQLite state: trackers, repos, worktrees, pr_cache, sessions, runs."""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS trackers (
  key_ref TEXT PRIMARY KEY, remote_url TEXT NOT NULL DEFAULT '',
  vendor TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS repos (
  key_ref TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
  tracker_key TEXT REFERENCES trackers(key_ref), remote TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS worktrees (
  ref_key TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
  branch TEXT UNIQUE NOT NULL, repo_key TEXT REFERENCES repos(key_ref) ON DELETE CASCADE,
  issue_url TEXT NOT NULL DEFAULT '', pr_url TEXT NOT NULL DEFAULT '',
  added_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS pr_cache (
  branch TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, worktree_ref TEXT NOT NULL REFERENCES worktrees(ref_key) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'running', runtime_name TEXT NOT NULL DEFAULT '',
  initiator_command TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  command TEXT NOT NULL DEFAULT '', args TEXT NOT NULL DEFAULT '[]',
  exit_code INTEGER, created_at TEXT NOT NULL);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path) -> Path:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
    return path


def gen_session_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    return f"{ts}-{random.randint(1000, 9999):04d}"


def insert_session(path: Path, *, worktree_ref: str, runtime_name: str,
                   initiator_command: str, prompt: str,
                   file_path: str) -> str:
    """Insert a running session; PK collision (same-ms id) retries once."""
    created = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        for _ in range(2):
            sid = gen_session_id()
            try:
                conn.execute(
                    "INSERT INTO sessions (id, worktree_ref, state, runtime_name,"
                    " initiator_command, prompt, file_path, created_at)"
                    " VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
                    (sid, worktree_ref, runtime_name, initiator_command,
                     prompt, file_path, created))
                return sid
            except sqlite3.IntegrityError:
                continue
    raise Exception("session id collision — retry the launch")


def finish_session(path: Path, sid: str, state: str) -> None:
    assert state in ("finished", "failed")
    with connect(path) as conn:
        conn.execute("UPDATE sessions SET state = ? WHERE id = ?", (state, sid))


def insert_run(path: Path, session_id: str, command: str,
               args: list[str], exit_code: int | None) -> int:
    import json
    created = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (session_id, command, args, exit_code, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, command, json.dumps(args), exit_code, created))
        return cur.lastrowid


def list_sessions(path: Path) -> list[dict]:
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC")]


def get_session(path: Path, sid: str) -> dict | None:
    import json
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM sessions WHERE id = ?",
                           (sid,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["runs"] = [dict(r) for r in conn.execute(
            "SELECT * FROM runs WHERE session_id = ? ORDER BY id", (sid,))]
        for r in out["runs"]:
            r["args"] = json.loads(r["args"])
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_sqlite.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Add cascade + retry tests, run, commit**

```python
def test_worktree_delete_cascades_sessions(tmp_path):
    from harness import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    import sqlite3
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO repos (key_ref, path) VALUES ('r', '/r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('k', '/wt', 'b', 'r')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
                            initiator_command="start", prompt="p",
                            file_path="/tmp/x.jsonl")
    with sq.connect(db) as conn:
        conn.execute("DELETE FROM worktrees WHERE ref_key = 'k'")
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_finish_session_states(tmp_path):
    from harness import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    import sqlite3
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO repos (key_ref, path) VALUES ('r', '/r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('k', '/wt', 'b', 'r')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
                            initiator_command="start", prompt="p",
                            file_path="/tmp/x.jsonl")
    sq.finish_session(db, sid, "finished")
    assert sq.get_session(db, sid)["state"] == "finished"
```

Run: `uv run pytest tests/test_store_sqlite.py -q`
Expected: PASS (4 passed).

```bash
git add src/harness/store_sqlite.py tests/test_store_sqlite.py
git commit -m "feat: SQLite repository with schema v1 and session ids"
```

---

### Task 2: One-shot JSON→SQLite migration

**Files:**
- Modify: `src/harness/store_sqlite.py` (add `migrate_json`)
- Test: `tests/test_store_sqlite.py` (append migration tests)

**Interfaces:**
- Consumes: `init_db`, `connect` from Task 1; `store.load_links/load_pr_cache/load_harnesses/load_config` shapes.
- Produces: `migrate_json(config_dir, db_path) -> dict` returning counts; `harness migrate` CLI wired in Task 6.

- [ ] **Step 1: Write the failing test**

```python
def test_migrate_copies_and_deletes(tmp_path):
    import json
    from harness import store_sqlite as sq
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "links.json").write_text(json.dumps({
        "jira:IPG-1": {"worktree": "/wt", "branch": "feat/1",
                       "repo": "/r", "issue_url": "http://j/1"}}))
    (cfg / "pr_cache.json").write_text(json.dumps({
        "feat/1": {"pr": {"number": 1}, "checked_at": "2026-09-20"}}))
    (cfg / "harnesses.json").write_text(json.dumps({}))
    (cfg / "config.json").write_text(json.dumps(
        {"repos": {"r": {"path": "/r"}}}))
    out = sq.migrate_json(cfg, cfg / "state.db")
    assert out["worktrees"] == 1 and out["pr_cache"] == 1
    assert not (cfg / "links.json").exists()
    assert (cfg / "config.json").exists()


def test_migrate_corrupt_aborts(tmp_path):
    from harness import store_sqlite as sq
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "links.json").write_text("{broken")
    import pytest
    from harness.errors import HarnessError
    with pytest.raises(HarnessError):
        sq.migrate_json(cfg, cfg / "state.db")
    assert (cfg / "links.json").exists()  # left in place
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_sqlite.py -q -k migrate`
Expected: FAIL with "has no attribute 'migrate_json'".

- [ ] **Step 3: Write minimal implementation**

```python
def migrate_json(config_dir: Path, db_path: Path) -> dict:
    """One-shot migration: links/pr_cache/harnesses JSON → state.db.

    Verifies row counts, then deletes the three JSON files; config.json
    is kept. Corrupt JSON or count mismatch aborts WITHOUT deleting
    (raise HarnessError, exit 2 at the CLI). Idempotent: existing db
    with rows is a no-op returning zeros.
    """
    import json
    from .errors import HarnessError

    def _load(name: str):
        p = config_dir / name
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise HarnessError(f"cannot migrate {p}: {e}",
                               exit_code=2) from e

    init_db(db_path)
    with connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0]
    if n:
        return {"worktrees": 0, "pr_cache": 0, "harnesses": 0}
    links = _load("links.json")
    pr_cache = _load("pr_cache.json")
    _load("harnesses.json")  # validated, then dropped (live-pid guard stays JSON-free: not migrated)
    counts = {"worktrees": 0, "pr_cache": 0, "harnesses": 0}
    with connect(db_path) as conn:
        for key, e in links.items():
            conn.execute(
                "INSERT OR IGNORE INTO repos (key_ref, path) VALUES (?, ?)",
                (str(e.get("repo", "")), str(e.get("repo", ""))))
            conn.execute(
                "INSERT INTO worktrees (ref_key, path, branch, repo_key,"
                " issue_url, pr_url, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, str(e.get("worktree", "")), str(e.get("branch", "")),
                 str(e.get("repo", "")), str(e.get("issue_url", "")),
                 str(e.get("pr_url", "")), str(e.get("added_at", ""))))
            counts["worktrees"] += 1
        for branch, entry in pr_cache.items():
            conn.execute(
                "INSERT INTO pr_cache (branch, payload, checked_at)"
                " VALUES (?, ?, ?)",
                (branch, json.dumps(entry.get("pr")),
                 str(entry.get("checked_at", ""))))
            counts["pr_cache"] += 1
    if counts["worktrees"] != len(links) or counts["pr_cache"] != len(pr_cache):
        raise HarnessError("migration count mismatch — files left in place",
                           exit_code=2)
    for name in ("links.json", "pr_cache.json", "harnesses.json"):
        p = config_dir / name
        if p.exists():
            p.unlink()
    return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_sqlite.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/harness/store_sqlite.py tests/test_store_sqlite.py
git commit -m "feat: one-shot JSON to SQLite migration"
```

---

### Task 3: Runtime ABC + session rows in `_run_harness`

**Files:**
- Modify: `src/harness/backend.py:13-53`
- Modify: `src/harness/cli.py:175-200` (`_run_harness`), all 5 call sites (`:326`, `:551`, `:587`, `:1842`, `:1853`)
- Test: `tests/test_cli.py` (append session-row tests)

**Interfaces:**
- Consumes: `store_sqlite.insert_session/finish_session/insert_run`, `store.config_dir`.
- Produces: `Runtime` ABC, `OmpRuntime`, `RUNTIMES` registry; `_run_harness(runtime, ...)` signature. Task 5 consumes session rows.

- [ ] **Step 1: Write the failing test**

```python
def test_run_harness_writes_session_row(isolated_config, tmp_path, monkeypatch):
    from harness import store_sqlite as sq
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    result = {"key": "jira:IPG-929"}
    cli._run_harness("omp", "prompt", str(wt_dir), str(repo_dir), False,
                     False, result, True, run_key="jira:IPG-929")
    rows = sq.list_sessions(sq.db_path())
    assert len(rows) == 1 and rows[0]["state"] == "finished"
    assert rows[0]["runtime_name"] == "omp"
    assert rows[0]["file_path"].endswith(".jsonl")


def test_run_harness_no_harness_writes_nothing(isolated_config, tmp_path, monkeypatch):
    from harness import store_sqlite as sq
    result = {"key": "k"}
    cli._run_harness("omp", "prompt", "/tmp/wt", "/tmp", False,
                     True, result, True, run_key="k")
    assert sq.list_sessions(sq.db_path()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k session_row`
Expected: FAIL with "has no attribute 'db_path'".

- [ ] **Step 3: Write minimal implementation**

In `store_sqlite.py` add:

```python
def db_path(config_dir: Path | None = None) -> Path:
    from . import store as _store
    base = config_dir or _store.config_dir()
    return base / "state.db"


def session_file_path(session_id: str, runtime_name: str,
                      config_dir: Path | None = None) -> Path:
    from . import store as _store
    base = (config_dir or _store.config_dir()) / "sessions" / runtime_name
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.jsonl"
```

In `backend.py` (after imports):

```python
class Runtime:
    """AI harness runtime: argv, launch, transcript-file flag."""
    name: str = ""

    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None) -> list[str]:
        raise NotImplementedError

    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None) -> int:
        raise NotImplementedError

    def session_file_flag(self, path: str) -> list[str]:
        return []


class OmpRuntime(Runtime):
    name = "omp"

    def command_argv(self, prompt, no_tty, extra_args=None):
        if no_tty:
            return ["omp", "-p", "--auto-approve", *(extra_args or []), prompt]
        return ["omp", *(extra_args or []), prompt]

    def launch(self, prompt, workdir, no_tty, extra_args=None):
        import shutil, subprocess
        from .errors import HarnessError
        argv = self.command_argv(prompt, no_tty, extra_args)
        if shutil.which(argv[0]) is None:
            raise HarnessError(f"`{argv[0]}` not found on PATH")
        if no_tty:
            proc = subprocess.run(argv, cwd=workdir)
            return proc.returncode
        cd_worktree(workdir)
        os.execvp(argv[0], argv)
        return 0

    def session_file_flag(self, path: str) -> list[str]:
        return ["--session-file", path]


RUNTIMES: dict[str, Runtime] = {"omp": OmpRuntime()}


def get_runtime(name: str) -> Runtime:
    from .errors import HarnessError
    try:
        return RUNTIMES[name]
    except KeyError:
        raise HarnessError(
            f"unsupported harness: {name} (v1 supports: {', '.join(sorted(RUNTIMES))})",
            exit_code=2) from None
```

Keep the module-level `command_argv(harness, ...)` and `launch(harness, ...)` as thin shims over `get_runtime(harness)` so existing callers/tests keep working; new code uses `Runtime` objects. Rewrite `_run_harness` in `cli.py`:

```python
def _run_harness(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                 no_tty: bool, no_harness: bool, result: dict, json_output: bool,
                 run_key: str | None = None) -> None:
    runtime = backend.get_runtime(harness_name)
    harness_cmd = " ".join(shlex.quote(a) for a in
                           runtime.command_argv(prompt, no_tty, _HARNESS_ARGS))
    if no_harness:
        # ... unchanged preview path ...
        return
    sid = None
    if run_key:
        store.record_harness_run(run_key, harness_name, worktree or fallback_dir)
        try:
            db = store_sqlite.db_path()
            store_sqlite.init_db(db)
            sid = store_sqlite.gen_session_id()
            fpath = str(store_sqlite.session_file_path(sid, harness_name))
            Path(fpath).touch(exist_ok=True)
            sid = store_sqlite.insert_session(
                db, worktree_ref=run_key, runtime_name=harness_name,
                initiator_command=result.get("initiator", harness_name),
                prompt=prompt, file_path=fpath)
        except Exception as e:
            eprint(f"warning: session record failed: {e}")
            sid = None
    try:
        extra = runtime.session_file_flag(fpath) if sid else None
        backend.launch(harness_name, prompt, worktree or fallback_dir, no_tty,
                       (_HARNESS_ARGS + extra) if extra else _HARNESS_ARGS)
        if sid:
            store_sqlite.finish_session(db, sid, "finished")
    except Exception:
        if sid:
            try:
                store_sqlite.finish_session(db, sid, "failed")
            except Exception:
                pass
        raise
    finally:
        if run_key:
            store.clear_harness_run(run_key)
    _print_result(result, json_output)
```

Note: `worktree_ref=run_key` requires the link row to exist in SQLite — after cutover (Task 6) links live in SQLite; before cutover, wrap the insert in try/except (FK failure → warn, launch continues). Session rows for `review --all` children: children are separate processes running `review` → they hit the same `_run_harness` path.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -q -k session_row`
Expected: PASS (2 passed). Then full CLI file: `uv run pytest tests/test_cli.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/harness/backend.py src/harness/cli.py src/harness/store_sqlite.py tests/test_cli.py
git commit -m "feat: Runtime interface and session rows on harness launch"
```

---

### Task 4: Cleanup squash-merges open PR/MRs

**Files:**
- Modify: `src/harness/cli.py:700-732` (`_cleanup_one`), `cleanup` command signature (`:598-606`, add `--no-squash`), `webapp.py` `VAL_FLAGS`/`BOOL_FLAGS` for cleanup.
- Test: `tests/test_cli.py` (append merge-path tests)

**Interfaces:**
- Consumes: `_status_cells` pr_data state (open vs MERGED/CLOSED), `run_cmd` seam.
- Produces: `_merge_pr(url, tool, squash)` helper; `_cleanup_one` merge-first behavior. No later task depends on it.

- [ ] **Step 1: Write the failing test**

```python
def test_cleanup_merges_open_pr_first(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "worktree": "/tmp/wt",
                                     "branch": "feat/9", "repo": "/tmp/proj",
                                     "pr_url": "https://github.com/o/r/pull/9"})
    calls = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: calls.append("cleanup") or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: calls.append("close"))
    from harness.errors import run_cmd as _real
    def fake(*a, **k):
        calls.append(a)
        return ""
    monkeypatch.setattr(cli.refs, "run_cmd", fake)
    monkeypatch.setattr("harness.cli.run_cmd", fake)
    out = cli._cleanup_one("jira:IPG-9", dict(store.load_links()["jira:IPG-9"]),
                           force=True, yes=True, dry_run=False,
                           json_output=True)
    assert out["status"] == "cleaned"
    assert any("merge" in str(c) for c in calls)
    assert "close" not in calls  # merged, not closed


def test_cleanup_merge_failure_keeps_worktree(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "worktree": "/tmp/wt",
                                     "branch": "feat/9", "repo": "/tmp/proj",
                                     "pr_url": "https://github.com/o/r/pull/9"})
    from harness.errors import HarnessError
    def boom(*a, **k):
        raise HarnessError("merge conflict")
    monkeypatch.setattr("harness.cli.run_cmd", boom)
    import pytest
    with pytest.raises(HarnessError):
        cli._cleanup_one("jira:IPG-9", dict(store.load_links()["jira:IPG-9"]),
                         force=False, yes=True, dry_run=False,
                         json_output=True)
    assert "jira:IPG-9" in store.load_links()  # link kept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k "cleanup_merge or cleanup_open"`
Expected: FAIL with "_cleanup_one() got an unexpected keyword" or merge never called.

- [ ] **Step 3: Write minimal implementation**

```python
def _merge_pr(pr_url: str, squash: bool) -> None:
    """Merge an open PR/MR (squash by default); raise HarnessError on failure."""
    from .errors import run_cmd as _run
    if "github.com" in pr_url:
        _run("gh", "pr", "merge", pr_url,
             *([] if squash else ["--no-squash"]))
    else:
        _run("glab", "mr", "merge", pr_url,
             *([] if squash else ["--no-squash"]))
    eprint(f"merged {pr_url}")
```

In `_cleanup_one`, after the `dry_run` early return and BEFORE `gitwt.cleanup_worktree`:

```python
    state = (_status_cells(dict(entry), refresh_pr=False).get("pr_data")
             or {}).get("state", "")
    if pr_url and state not in ("MERGED", "CLOSED", ""):
        # Open PR/MR: merge (squash default) so no work is lost; any
        # failure aborts BEFORE worktree removal / link drop / branch delete.
        _merge_pr(pr_url, squash=squash)
```

Thread `squash: bool = True` through `_cleanup_one(...)` and the `cleanup` command (`--no-squash` flag, default squash). After successful merge + `gitwt.cleanup_worktree`, delete the remote branch LAST:

```python
    if pr_url and state not in ("MERGED", "CLOSED", ""):
        try:
            run_cmd("git", "-C", str(repo), "push", "origin",
                    "--delete", branch)
        except HarnessError as e:
            eprint(f"warning: remote branch delete failed: {e}")
```

Add `"--no-squash"` to webapp `BOOL_FLAGS["cleanup"]`. Dry-run result gains `"merge": pr_url or ""`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -q -k cleanup`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness/cli.py src/harness/webapp.py tests/test_cli.py
git commit -m "feat: cleanup squash-merges open PRs before teardown"
```

---

### Task 5: Sessions API + web page

**Files:**
- Modify: `src/harness/webapp.py` (add `/api/sessions`, `/api/sessions/{id}`)
- Modify: `web/src/lib/api.ts`, `web/src/lib/queries.ts`
- Create: `web/src/pages/Sessions.tsx` (+ router entry in `web/src/App.tsx`)
- Test: `tests/test_webapp.py` (endpoint shape tests)

**Interfaces:**
- Consumes: `store_sqlite.list_sessions/get_session/db_path` from Task 1.
- Produces: `GET /api/sessions`, `GET /api/sessions/{id}`; `Sessions` page. Task 6 documents them.

- [ ] **Step 1: Write the failing test**

```python
def test_api_sessions_empty(client):
    body = client.get("/api/sessions").json()
    assert body == {"sessions": []}


def test_api_sessions_roundtrip(client, tmp_path, monkeypatch):
    from harness import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    import sqlite3
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO repos (key_ref, path) VALUES ('r', '/r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('k', '/wt', 'b', 'r')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
                            initiator_command="start", prompt="hello",
                            file_path="/tmp/x.jsonl")
    body = client.get("/api/sessions").json()
    assert body["sessions"][0]["id"] == sid
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["prompt"] == "hello" and detail["runs"] == []
    assert client.get("/api/sessions/nope").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webapp.py -q -k sessions`
Expected: FAIL with 404 (no such endpoint) — FastAPI returns 404 for unknown routes.

- [ ] **Step 3: Write minimal implementation**

In `webapp.py` `create_app` (next to `/api/candidates`):

```python
    @app.get("/api/sessions")
    def sessions_list() -> dict:
        from . import store_sqlite as sq
        db = sq.db_path()
        if not db.exists():
            return {"sessions": []}
        rows = [{k: v for k, v in r.items() if k != "prompt"}
                for r in sq.list_sessions(db)]
        return {"sessions": rows}

    @app.get("/api/sessions/{sid}")
    def session_detail(sid: str) -> dict:
        from . import store_sqlite as sq
        db = sq.db_path()
        row = sq.get_session(db, sid) if db.exists() else None
        if row is None:
            raise HTTPException(404, f"no session {sid}")
        if row.get("file_path") and not Path(row["file_path"]).exists():
            row["transcript"] = "missing"
        return row
```

In `web/src/lib/api.ts`:

```typescript
export interface SessionRow {
  id: string; worktree_ref: string; state: string; runtime_name: string;
  initiator_command: string; file_path: string; created_at: string;
}
export interface SessionDetail extends SessionRow {
  prompt: string; runs: { id: number; command: string; args: string[]; exit_code: number | null; created_at: string }[];
  transcript?: string;
}
```

In `queries.ts`:

```typescript
export function useSessions() {
  return useQuery({ queryKey: ["sessions"], queryFn: api.sessions })
}
export function useSession(id: string) {
  return useQuery({ queryKey: ["session", id], queryFn: () => api.session(id) })
}
```

`Sessions.tsx`: table (id short, worktree_ref, runtime, command, state badge, date) + click-through detail (prompt `<pre>`, transcript-missing badge, runs list). Follow `Runs.tsx` table + `RunDetail.tsx` viewer patterns. Router: add `/sessions` route in `App.tsx` + nav entry (follow existing page registration).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_webapp.py -q -k sessions`
Expected: PASS. Then `cd web && npm run build` + `npx eslint .` clean.

- [ ] **Step 5: Commit**

```bash
git add src/harness/webapp.py web/src tests/test_webapp.py
git commit -m "feat: sessions API and web Sessions page"
```

---

### Task 6: Cutover — migrate command, store swap, docs

**Files:**
- Modify: `src/harness/cli.py` (add `migrate` command; swap `store.*` links/cache calls to `store_sqlite` — OR thin shim: keep `store.py` API, delegate to SQLite when `state.db` exists)
- Modify: `CHANGELOG.md`, `AGENTS.md`, `web/API_CONTRACT.md`
- Test: full suite + real-CLI smoke

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: working cutover; docs. No later tasks.

- [ ] **Step 1: Write the failing test**

```python
def test_migrate_command(isolated_config, tmp_path):
    import json
    d = Path(str(isolated_config))
    (d / "links.json").write_text(json.dumps({
        "jira:IPG-1": {"worktree": "/wt", "branch": "feat/1",
                       "repo": "/r"}}))
    r = _invoke("migrate", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["worktrees"] == 1
    assert not (d / "links.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k migrate_command`
Expected: FAIL with "No such command 'migrate'".

- [ ] **Step 3: Write minimal implementation**

```python
@app.command("migrate")
@_catch_harness_errors
def migrate_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """One-shot migration: links/pr_cache/harnesses JSON → state.db (issue #9).

    Verifies row counts, then deletes the JSON files (config.json kept).
    Idempotent: re-run is a no-op.
    """
    from . import store_sqlite as sq
    out = sq.migrate_json(store.config_dir(), sq.db_path())
    _print_result({"migrated": out}, json_output)
```

Store swap strategy (implementer's choice, smallest diff wins): keep `store.py` function names as the caller-facing API; when `state.db` exists, delegate `load_links/record_link/load_pr_cache/cache_pr_status/cache_ci_status` to `store_sqlite` (JSON↔row translation inside `store.py`); else legacy JSON path. This keeps all CLI/webapp/tests call sites working. `harnesses.json` guard moves to a `harness_guard` table OR stays file-based — implementer picks, but document the choice in AGENTS.md.

Docs: CHANGELOG Unreleased entry (SQLite sessions, timestamp ids, squash-merge cleanup, Runtime interface, migrate command); AGENTS.md status-caching paragraph (JSON → SQLite tables); API_CONTRACT `/api/sessions` section.

- [ ] **Step 4: Run tests + smoke to verify**

Run: `uv run pytest -q`
Expected: PASS. Smoke: `HARNESS_CONFIG_DIR=/tmp/fake-cfg uv run harness migrate --json` then `candidates --json` still lists `worktrees`.

- [ ] **Step 5: Commit**

```bash
git add src/harness/cli.py src/harness/store.py CHANGELOG.md AGENTS.md web/API_CONTRACT.md tests/test_cli.py
git commit -m "feat: SQLite cutover with migrate command and docs"
```

---

### Task 7: Final whole-branch review + close

Owner: controller (not a subagent task). Whole-branch review over `main..HEAD` with the Review Focus five; fix wave if needed; `uv run pytest -q` + `npm run build` + `eslint` green on merged main; `gh issue close 9 -c ...` and `gh issue close 10 -c ...` (never push).
