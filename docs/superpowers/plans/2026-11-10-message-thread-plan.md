# Message & Thread Interaction System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the message/thread interaction MVP: messages table, `message` CLI, category-guarded action/flow runs, `/api/messages`, and the `/messages` web page.

**Architecture:** New `store_messages.py` + `cli_message.py` modules behind the existing `store.py` facade and Typer `app`; schema via the `_migrate_*` ALTER TABLE chain; web backend mirrors CLI flags in `web_args.py` SPECS; frontend reuses StatusTable primitives in a new route.

**Tech Stack:** Python (Typer, sqlite3), FastAPI (`serve` extra), React + TS (Vite, existing StatusTable components).

**Spec:** `docs/superpowers/specs/2026-11-10-message-thread-design.md`

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line goes to stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- New/renamed flags: update `BOOL_FLAGS` + `VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend control + `tests/test_cli.py` + `tests/test_webapp.py` in the same PR (parity checklist).
- `test_specs_mirror_cli_flags` (introspects the real Typer app) must stay green.
- DB migrations follow the `_migrate_*` pattern in `store_sqlite.py`; idempotent (skipped when column present).
- Existing `custom_actions`/`flow_templates` rows backfill `category = 'worktree'`.
- Sends stay drafts-only: `draft-reply` uses `mm messages draft save`, never direct send.

## Review Focus

- `message add` with zero repos or an unknown repo key must fail exit 2 naming the repo (spec: ≥1 valid repo).
- `message action run` / `message flow run` with a `worktree`-category action/template must fail exit 2, not silently run.
- `message flow run` without `--ref` on a chain containing a worktree-kind step must exit 2 naming the step.
- Ambiguous message refs (substring matching >1 row) must exit 2 listing candidates, never pick one.
- `mm` send-guard errors (`send_disabled`) must surface verbatim, never be swallowed or retried as a send.

---

## File structure

- Create `src/workagent/store_messages.py` — messages CRUD + ref resolution (Task 1).
- Modify `src/workagent/store_sqlite.py` — SCHEMA + `_migrate_messages` + `_migrate_category` (Task 1).
- Modify `src/workagent/store.py` — facade: `create_message/list_messages/get_message/update_message/delete_message/resolve_message` (Task 1).
- Modify `src/workagent/store_custom_actions.py`, `src/workagent/store_flows.py` — `category` param + validation (Task 2).
- Modify `src/workagent/cli_action.py`, `src/workagent/cli_flow.py` — `--category` flags, list/show columns (Task 2).
- Create `src/workagent/cli_message.py` — `message` Typer group: CRUD + adhoc/validate/draft-reply/action-run/flow-run (Tasks 3–4).
- Modify `src/workagent/cli.py` — import `cli_message` to register the group (Task 3).
- Modify `src/workagent/web_args.py`, `src/workagent/webapp.py` — `message` SPECS/flags + `/api/messages` endpoints (Task 5).
- Modify `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/src/lib/api.ts` — contract + client (Task 5).
- Create `web/src/pages/Messages.tsx` (+ `MessageDetail.tsx`, `MessageRowActions.tsx`) — `/messages` route (Task 6).
- Create `tests/test_store_messages.py`, `tests/test_cli_message.py` — backend tests (Tasks 1, 3–4).
- Modify `tests/test_custom_actions.py`, `tests/test_flows.py`, `tests/test_webapp.py` — category + endpoint coverage (Tasks 2, 5).

---

### Task 1: Messages table + store module

**Files:**
- Modify: `src/workagent/store_sqlite.py` (SCHEMA + 2 migrations)
- Create: `src/workagent/store_messages.py`
- Modify: `src/workagent/store.py` (facade functions)
- Test: `tests/test_store_messages.py`

**Interfaces:**
- Consumes: `store_sqlite.connect`, `store_sqlite.db_path`, `repos` table (`key_ref`).
- Produces: `store.create_message(...) -> dict`, `store.list_messages(thread_id="") -> list[dict]`, `store.get_message(ref) -> dict | None`, `store.update_message(ref, **fields) -> dict`, `store.delete_message(ref) -> None`, `store.resolve_message(ref) -> dict` (raises `HarnessError` exit 2 on miss/ambiguity). Row dict keys: `id, message_id, thread_id, app_name, sender_username, content, ts, repos (list), added_at`.

- [ ] **Step 1: Write the failing store test**

```python
"""tests/test_store_messages.py — messages CRUD (Task 1 TDD)."""
def test_create_and_get_round_trip(tmp_path):
    from workagent import store_sqlite as sq
    from workagent import store_messages as sm
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                 ("r1", "/tmp/r1", "r1"))
    conn.commit()
    row = sm.create_message(db, message_id="p1", app_name="mattermost",
                            sender_username="u", content="hello", ts="2026-11-10T00:00:00+00:00",
                            repos=["r1"])
    assert row["message_id"] == "p1"
    assert row["repos"] == ["r1"]
    assert sm.get_message(db, "p1")["content"] == "hello"


def test_create_requires_known_repo(tmp_path):
    from workagent import store_sqlite as sq
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    sq.init_db(db)
    try:
        sm.create_message(db, message_id="p9", repos=["nope"])
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_messages.py -v`
Expected: FAIL with "store_messages not defined" (collection error).

- [ ] **Step 3: Implement schema + store module (minimal)**

In `src/workagent/store_sqlite.py`, append to SCHEMA after `prodtest_items`:

```sql
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL,
  thread_id TEXT NOT NULL DEFAULT '', app_name TEXT NOT NULL DEFAULT 'mattermost',
  sender_username TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL DEFAULT '', repos TEXT NOT NULL DEFAULT '[]',
  added_at TEXT NOT NULL);
```

Add `_migrate_messages(conn)` (CREATE TABLE IF NOT EXISTS + nothing else; fresh DBs get it from SCHEMA) and `_migrate_category(conn)` following `_migrate_display_cols`:

```python
def _migrate_category(conn) -> None:
    """Schema v19: custom_actions/flow_templates gain category."""
    for table in ("flow_templates", "custom_actions"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        if "category" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN category TEXT NOT NULL DEFAULT 'worktree'")
```

Call both from `init_db` alongside the other `_migrate_*` calls. Create `src/workagent/store_messages.py`:

```python
"""Messages store: registered chat messages (messages table)."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

APPS = ("mattermost",)

def _connect(db: Path):
    from .store_sqlite import connect
    return connect(db)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _row(r) -> dict:
    d = dict(r) if isinstance(r, sqlite3.Row) else dict(r)
    try:
        d["repos"] = json.loads(d.get("repos") or "[]")
    except (TypeError, ValueError):
        d["repos"] = []
    return d

def _check_repos(conn, repos) -> list[str]:
    from .errors import HarnessError
    keys = list(repos or [])
    if not keys:
        raise HarnessError("at least one --repo is required", exit_code=2)
    missing = [k for k in keys if conn.execute(
        "SELECT 1 FROM repos WHERE key_ref = ?", (k,)).fetchone() is None]
    if missing:
        raise HarnessError(f"unknown repos: {', '.join(missing)}", exit_code=2)
    return keys

def create_message(db: Path, *, message_id: str, thread_id: str = "",
                   app_name: str = "mattermost", sender_username: str = "",
                   content: str = "", ts: str = "", repos=None) -> dict:
    from .errors import HarnessError
    if not (message_id or "").strip():
        raise HarnessError("--message-id is required", exit_code=2)
    if (app_name or "mattermost") not in APPS:
        raise HarnessError(f"app_name must be one of {', '.join(APPS)}", exit_code=2)
    with _connect(db) as conn:
        keys = _check_repos(conn, repos)
        try:
            cur = conn.execute(
                "INSERT INTO messages (message_id, thread_id, app_name, sender_username, content, ts, repos, added_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id.strip(), thread_id or "", app_name or "mattermost",
                 sender_username or "", content or "", ts or "",
                 json.dumps(keys), _now_iso()))
        except sqlite3.IntegrityError:
            raise HarnessError(f"message {message_id!r} already registered", exit_code=2) from None
        conn.row_factory = sqlite3.Row
        return _row(conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone())

def list_messages(db: Path, thread_id: str = "") -> list[dict]:
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if thread_id:
            rows = conn.execute("SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (thread_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
        return [_row(r) for r in rows]

def get_message(db: Path, ref: str) -> dict | None:
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        for q, arg in (("SELECT * FROM messages WHERE id = ?", ref),
                       ("SELECT * FROM messages WHERE message_id = ?", ref)):
            try:
                r = conn.execute(q, (arg,)).fetchone()
            except sqlite3.InterfaceError:
                continue
            if r is not None:
                return _row(r)
    return None

def resolve_message(db: Path, ref: str) -> dict:
    from .errors import HarnessError
    hit = get_message(db, ref)
    if hit is not None:
        return hit
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = [_row(r) for r in conn.execute(
            "SELECT * FROM messages WHERE message_id LIKE ? OR thread_id = ? OR content LIKE ?",
            (f"%{ref}%", ref, f"%{ref}%"))]
    if not rows:
        raise HarnessError(f"unknown message: {ref!r}", exit_code=2)
    if len(rows) > 1:
        raise HarnessError("ambiguous message ref %r: %s" % (ref, ", ".join(r["message_id"] for r in rows)), exit_code=2)
    return rows[0]

def update_message(db: Path, ref: str, **fields) -> dict:
    from .errors import HarnessError
    allowed = {"thread_id", "app_name", "sender_username", "content", "ts", "repos", "new_message_id"}
    unknown = set(fields) - allowed
    if unknown:
        raise HarnessError(f"unknown fields: {sorted(unknown)}", exit_code=2)
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        base = resolve_message(db, ref)
        if "repos" in fields:
            fields["repos"] = json.dumps(_check_repos(conn, fields["repos"]))
        if "new_message_id" in fields:
            fields["message_id"] = fields.pop("new_message_id")
        if "app_name" in fields and fields["app_name"] not in APPS:
            raise HarnessError(f"app_name must be one of {', '.join(APPS)}", exit_code=2)
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE messages SET {sets} WHERE id = ?", (*fields.values(), base["id"]))
        return _row(conn.execute("SELECT * FROM messages WHERE id = ?", (base["id"],)).fetchone())

def delete_message(db: Path, ref: str) -> None:
    from .errors import HarnessError
    with _connect(db) as conn:
        base = resolve_message(db, ref)
        conn.execute("DELETE FROM messages WHERE id = ?", (base["id"],))
```

Facade in `src/workagent/store.py` (next to `list_actions`):

```python
from . import store_messages as _msg

def create_message(**fields) -> dict:
    from . import store_sqlite as _sq
    return _msg.create_message(_sq.db_path(), **fields)

def list_messages(thread_id: str = "") -> list:
    from . import store_sqlite as _sq
    return _msg.list_messages(_sq.db_path(), thread_id)

def get_message(ref: str) -> dict | None:
    from . import store_sqlite as _sq
    return _msg.get_message(_sq.db_path(), ref)

def resolve_message(ref: str) -> dict:
    from . import store_sqlite as _sq
    return _msg.resolve_message(_sq.db_path(), ref)

def update_message(ref: str, **fields) -> dict:
    from . import store_sqlite as _sq
    return _msg.update_message(_sq.db_path(), ref, **fields)

def delete_message(ref: str) -> None:
    from . import store_sqlite as _sq
    return _msg.delete_message(_sq.db_path(), ref)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_messages.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add edge tests (unique id, bad app, unknown update field)**

Append to `tests/test_store_messages.py`: duplicate `message_id` → HarnessError exit 2; `app_name="slack"` → exit 2; `update_message("p1", bogus=1)` → exit 2; `list_messages(thread_id="t")` filters. Run full file; expected PASS.

- [ ] **Step 6: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_messages.py src/workagent/store.py tests/test_store_messages.py
git commit -m "[44] messages table + store module with repos validation"
```

---

### Task 2: `category` on actions + flow templates

**Files:**
- Modify: `src/workagent/store_custom_actions.py`, `src/workagent/store_flows.py`
- Modify: `src/workagent/cli_action.py`, `src/workagent/cli_flow.py`
- Test: `tests/test_custom_actions.py`, `tests/test_flows.py` (append)

**Interfaces:**
- Consumes: Task 1 `_migrate_category` (column exists).
- Produces: `create_action(..., category="message")`, `update_action(..., category="message")`, same for templates; `CATEGORIES = ("worktree", "message")` in both store modules; CLI `--category` (default `worktree`); `list`/`show` include `category`.

- [ ] **Step 1: Write the failing test**

```python
def test_action_category_round_trip(tmp_path):
    from workagent import store_sqlite as sq
    from workagent import store_custom_actions as sca
    db = tmp_path / "s.db"
    sq.init_db(db)
    row = sca.create_action(db, name="Msg triage", prompt="p", category="message")
    assert row["category"] == "message"
    assert sca.get_action(db, row["label"])["category"] == "message"


def test_action_category_rejects_bad_value(tmp_path):
    from workagent import store_sqlite as sq
    from workagent import store_custom_actions as sca
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    sq.init_db(db)
    try:
        sca.create_action(db, name="X", prompt="p", category="sms")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")
```

(same two tests for `store_flows.create_template` in `tests/test_flows.py` with `steps='[]'`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_custom_actions.py::test_action_category_round_trip tests/test_flows.py::test_template_category_round_trip -v`
Expected: FAIL (unexpected keyword `category`).

- [ ] **Step 3: Implement category in both stores**

In each of `store_custom_actions.py` / `store_flows.py`: add `CATEGORIES = ("worktree", "message")`; add `category: str = "worktree"` param; validate (`HarnessError` exit 2 on other values); include in INSERT column list + `_row` passthrough (SELECT * already returns it); whitelist in `update_*` `allowed` set. Default rows read back `"worktree"` via the migration backfill.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_custom_actions.py tests/test_flows.py -q`
Expected: PASS.

- [ ] **Step 5: Add CLI `--category` + list/show columns**

`cli_action.py action_add/action_update`: add `category: str = typer.Option("worktree", "--category", help="...")`, pass through to `store.create_action/update_action`; `action_list` row + header gains `category`; `action_show` output gains `category`. Mirror in `cli_flow.py` for `flow template add/update/list/show`. Invalid values surface as exit 2 from the store validation.

- [ ] **Step 6: Commit**

```bash
git add src/workagent/store_custom_actions.py src/workagent/store_flows.py src/workagent/cli_action.py src/workagent/cli_flow.py tests/test_custom_actions.py tests/test_flows.py
git commit -m "[44] category enum on actions and flow templates"
```

---

### Task 3: `message` CLI — CRUD

**Files:**
- Create: `src/workagent/cli_message.py`
- Modify: `src/workagent/cli.py` (register import)
- Test: `tests/test_cli_message.py`

**Interfaces:**
- Consumes: `store.*_message` facade (Task 1); `cli_core.app/_print_result/_print_rows/_catch_harness_errors`.
- Produces: `message add/list/show/update/remove` commands; message ref resolution via `store.resolve_message`.

- [ ] **Step 1: Write the failing CLI test**

```python
"""tests/test_cli_message.py — message CLI (Tasks 3-4 TDD)."""
def test_message_add_list_round_trip(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "add", "--message-id", "p1",
                                      "--repo", "r1", "--content", "hi", "--json"])
    assert res.exit_code == 0, res.output
    res2 = runner.invoke(cli_mod.app, ["message", "list", "--json"])
    assert res2.exit_code == 0, res2.output
    assert "p1" in res2.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_message.py -v`
Expected: FAIL (no such command `message`).

- [ ] **Step 3: Implement `message` CRUD group**

Create `src/workagent/cli_message.py`:

```python
"""`message` command: registered chat-message CRUD + runs."""
from __future__ import annotations
from typing import Optional
import typer
from . import store
from .cli_core import _catch_harness_errors, _print_result, _print_rows, app

message_app = typer.Typer(help="Manage registered chat messages.", no_args_is_help=True)
app.add_typer(message_app, name="message")


@message_app.command("add")
@_catch_harness_errors
def message_add(
    message_id: str = typer.Option(..., "--message-id", help="App-side id (e.g. Mattermost post id)."),
    thread_id: str = typer.Option("", "--thread-id", help="Thread root id (default: top-level)."),
    app_name: str = typer.Option("mattermost", "--app", help="Source app."),
    sender: str = typer.Option("", "--sender", help="Sender username."),
    content: str = typer.Option("", "--content", help="Message text."),
    ts: str = typer.Option("", "--ts", help="Sent timestamp."),
    repo: list[str] = typer.Option([], "--repo", help="Associated repo (repeatable, ≥1)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    row = store.create_message(message_id=message_id, thread_id=thread_id, app_name=app_name,
                               sender_username=sender, content=content, ts=ts, repos=list(repo or []))
    _print_result({"added": row["message_id"], "id": row["id"]}, json_output)


@message_app.command("list")
def message_list(
    thread: str = typer.Option("", "--thread", help="Only this thread_id."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV."),
) -> None:
    items = [{"id": r["id"], "message_id": r["message_id"], "thread_id": r["thread_id"],
              "app": r["app_name"], "sender": r["sender_username"],
              "repos": ",".join(r.get("repos") or []), "ts": r["ts"]}
             for r in store.list_messages(thread)]
    _print_rows(items, json_output, csv_output,
                ["id", "message_id", "thread_id", "app", "sender", "repos", "ts"],
                "Messages", "(no messages)")


@message_app.command("show")
@_catch_harness_errors
def message_show(ref: str = typer.Argument(..., help="Message id/message_id/thread."),
                 json_output: bool = typer.Option(False, "--json", help="Output as JSON.")) -> None:
    _print_result(store.resolve_message(ref), json_output)


@message_app.command("update")
@_catch_harness_errors
def message_update(ref: str = typer.Argument(...),
                   thread_id: Optional[str] = typer.Option(None, "--thread-id"),
                   sender: Optional[str] = typer.Option(None, "--sender"),
                   content: Optional[str] = typer.Option(None, "--content"),
                   ts: Optional[str] = typer.Option(None, "--ts"),
                   repo: Optional[list[str]] = typer.Option(None, "--repo"),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    fields = {k: v for k, v in {"thread_id": thread_id, "sender_username": sender,
                                "content": content, "ts": ts}.items() if v is not None}
    if repo is not None:
        fields["repos"] = list(repo)
    _print_result(store.update_message(ref, **fields), json_output)


@message_app.command("remove")
@_catch_harness_errors
def message_remove(ref: str = typer.Argument(...),
                   yes: bool = typer.Option(False, "--yes", help="Skip confirmation."),
                   json_output: bool = typer.Option(False, "--json")) -> None:
    import sys as _sys
    row = store.resolve_message(ref)
    if not yes and _sys.stdin.isatty():
        from .cli_core import eprint, _fail, EXIT_USAGE
        eprint(f"remove message {row['message_id']}? [y/N] ", end="")
        if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    store.delete_message(ref)
    _print_result({"removed": row["message_id"]}, json_output)
```

In `src/workagent/cli.py` add `from . import cli_message  # noqa: F401  (registers message)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_message.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_message.py src/workagent/cli.py tests/test_cli_message.py
git commit -m "[44] message CLI CRUD (add/list/show/update/remove)"
```

---

### Task 4: `message` runs — adhoc/validate/draft-reply/action/flow

**Files:**
- Modify: `src/workagent/cli_message.py`
- Modify: `src/workagent/cli_action.py` (message prompt branch)
- Test: `tests/test_cli_message.py` (append)

**Interfaces:**
- Consumes: Task 1–3; `cli_harness._run_harness/_guard_harness`, `pick` chooser, `worktrees.resolve_any`; `store.get_action/get_template`.
- Produces: `message adhoc/validate/draft-reply/action run/flow run`; hybrid resolve `_resolve_exec(ref_msg, ref_opt) -> (workdir, worktree_key | "")`.

- [ ] **Step 1: Write the failing run tests**

```python
def test_message_action_rejects_worktree_category(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    import workagent.store_custom_actions as sca
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    msg = sca.create_action(db, name="WT only", prompt="p", category="worktree")
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "action", "run", msg["label"], "p1", "--yes", "--json"])
    assert res.exit_code == 2
    assert "worktree" in (res.output + res.stderr).lower()


def test_message_draft_reply_dry_run(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", thread_id="t1", repos=["r1"])
    calls = []
    monkeypatch.setattr("workagent.cli_message._mm", lambda *a: calls.append(a) or {"channel_id": "c1"})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "draft-reply", "p1", "--text", "ack", "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    assert "c1" in res.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_message.py -v`
Expected: FAIL (no such command `action` under message / `draft-reply`).

- [ ] **Step 3: Implement hybrid resolve + run commands**

In `cli_message.py` add:

```python
def _resolve_exec(msg: dict, ref_opt: str) -> tuple[str, str]:
    """(workdir, worktree_key|''. --ref wins; else repos[] like start's picker."""
    from . import worktrees
    if (ref_opt or "").strip():
        links = store.load_links()
        _key, entry, _ = worktrees.resolve_any(ref_opt.strip(), links)
        return str(entry.get("worktree", "")), _key
    repos = list(msg.get("repos") or [])
    if len(repos) == 1:
        r = store.get_repo(repos[0])  # facade must exist; else query repos table here
        return str((r or {}).get("path", "")), ""
    from . import repos as _repos
    picked = _repos.resolve_for_message(repos)  # implement as pick.py chooser; --repo flag bypasses
    return picked, ""
```

Full run commands (minimal, mirroring `adhoc` confirms/modes):
- `message adhoc <msg> --prompt P [--ref R] [--repo R] [--launch/--terminal/--dry-run/--yes/--json]` — prompt = `Message <message_id> (<app_name>, from <sender> at <ts>):\n<content>\nThread: <thread_id>\nRepos: <repos>\n\n<P>`; `--repo` narrows multi-repo messages; hybrid resolve for workdir; `_run_harness` like `adhoc`.
- `message validate <msg> [...]` — same with fixed template: `"Validate the claims in this message against the linked repos. For each claim report CONFIRMED/REFUTED with file:line evidence. ..."`.
- `message draft-reply <msg> --text T [--dry-run/--json]` — `_mm("messages","get-msg", id)` → channel_id; `--dry-run` prints argv; else `_mm("messages","draft","save","--channel", ch, "--reply-to", thread_root, "--text", T)`; `HarnessError` on `send_disabled` text surfaces verbatim (no retry).
- `message action run <label> <msg> [--ref/--repo/...]` — `store.get_action`; `category != "message"` → `_fail(..., EXIT_USAGE)`; prompt branch with message header; pre/post scripts run with cwd=workdir (reuse `release_gates.run_repo_command`); preview mode notes post like `action run`.
- `message flow run <label> <msg> [--ref/--repo/--dry-run/--yes/--json]` — `store.get_template`; category guard; pre-scan `validate_template` steps: `action` steps must reference `message`-category actions, worktree-kind steps (`start/review/sync/cleanup/release`) require `--ref` (exit 2 naming step); then delegate per-step execution to the existing flow runner with workdir override.

`_mm(*argv)` helper: `run_cmd(["mm", *argv])` (JSON parse when not dry-run).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_message.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_message.py src/workagent/cli_action.py tests/test_cli_message.py
git commit -m "[44] message runs: adhoc/validate/draft-reply/action/flow"
```

---

### Task 5: Web backend — `/api/messages` + runs passthrough

**Files:**
- Modify: `src/workagent/web_args.py`, `src/workagent/webapp.py`
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/src/lib/api.ts`
- Test: `tests/test_webapp.py` (append)

**Interfaces:**
- Consumes: Tasks 1–4 CLI surface.
- Produces: `GET /api/messages`, `GET /api/messages/{id}`, `POST /api/runs {"command": "message", ...}`; `api.ts` `listMessages/getMessage`.

- [ ] **Step 1: Write the failing web test**

```python
def test_messages_endpoint_lists_rows(client=None):
    from workagent import store_sqlite as sq
    ...
```

Concretely (follow existing `test_webapp.py` client fixture style):

```python
def test_api_messages_round_trip():
    from fastapi.testclient import TestClient
    from workagent import store_sqlite as sq
    from workagent.webapp import create_app
    import tempfile
    tmp = tempfile.mkdtemp()
    db = sq.init_db(Path(tmp) / "s.db")
    ...seed repo + message via store_messages...
    c = TestClient(create_app(Path(tmp) / "dist", "127.0.0.1", 3344, []))
    r = c.get("/api/messages")
    assert r.status_code == 200
    assert "p1" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webapp.py::test_api_messages_round_trip -v`
Expected: FAIL (404 `/api/messages`).

- [ ] **Step 3: Implement endpoints + SPECS**

`webapp.py`: `GET /api/messages` → `{"messages": [...]}` (each: message fields + `repo_names`); `GET /api/messages/{id}` → entry or 404 `{error:{code:"not_found"}}`. `web_args.py`: `SPECS["message"]` with key `message:<first-positional>` for run subcommands else `config`; `SUBCOMMANDS["message"] = {"add","list","show","update","remove","adhoc","validate","draft-reply","action","flow"}`; `BOOL_FLAGS`/`VAL_FLAGS` mirror every CLI flag from Tasks 3–4 (`--message-id/--thread-id/--app/--sender/--content/--ts/--repo/--thread/--prompt/--text/--ref/--launch/--terminal/--dry-run/--yes/--json/--csv/--harness/--extra-prompt/--env-file/--session-file`). Update contract + inventory + `api.ts`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webapp.py -q`
Expected: PASS including `test_specs_mirror_cli_flags`.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/webapp.py src/workagent/web_args.py web/API_CONTRACT.md web/FEATURE_INVENTORY.md web/src/lib/api.ts tests/test_webapp.py
git commit -m "[44] web backend: /api/messages + message runs passthrough"
```

---

### Task 6: `/messages` UI page

**Files:**
- Create: `web/src/pages/Messages.tsx`, `web/src/components/MessageDetail.tsx`, `web/src/components/MessageRowActions.tsx`
- Modify: `web/src/App.tsx` (route), nav component
- Test: `web/src/pages/Messages.test.tsx` (filter helpers)

**Interfaces:**
- Consumes: Task 5 `api.ts` client; existing `StatusTable*`, `RowActions`, run/SSE plumbing.
- Produces: `/messages` route with filters + expandable detail + overlay run buttons (`category=message`-filtered action/flow dropdowns).

- [ ] **Step 1: Write the failing frontend test**

```ts
// Messages.test.tsx — messageFilter helper
import { describe, expect, it } from "vitest"
import { filterMessages } from "./Messages"
describe("filterMessages", () => {
  it("filters by sender + text", () => {
    const rows = [{ message_id: "p1", sender_username: "u1", content: "deploy broke", thread_id: "t", app_name: "mattermost", repos: ["r1"] }]
    expect(filterMessages(rows, { q: "deploy", sender: "u1" })).toHaveLength(1)
    expect(filterMessages(rows, { q: "nothing", sender: "" })).toHaveLength(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/pages/Messages.test.tsx` (from `web/`)
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the page**

`Messages.tsx`: table columns app/sender/thread/repos/updated + search + app/sender/repo/thread filters + thread-grouping toggle; expandable `MessageDetail` (full content, timestamps, repo list, thread siblings link); `MessageRowActions` overlay: Adhoc / Validate / Draft reply / Run action / Run flow / Delete via existing run modal + `POST /api/runs {"command":"message",...}`; Dashboard worktree dropdowns add `?category=worktree` param. `filterMessages` pure helper exported for tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/pages/Messages.test.tsx && npx tsc --noEmit` (from `web/`)
Expected: PASS, no type errors.

- [ ] **Step 5: Full gate + smoke**

Run: `uv run pytest -q` (repo root) then smoke: `workagent message add ... && workagent message list && workagent message adhoc <id> --prompt t --dry-run`.

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/Messages.tsx web/src/pages/Messages.test.tsx web/src/components/MessageDetail.tsx web/src/components/MessageRowActions.tsx web/src/App.tsx
git commit -m "[44] /messages UI page with row overlay actions"
```

---

## Self-review

- **Spec coverage:** §1 schema → Task 1; §2 store → Task 1 (+ category Task 2); §3 CLI → Tasks 3–4 (CRUD, adhoc/validate/draft-reply/action/flow, `--category`); §4 web → Task 5 (endpoints, SPECS, contract, api.ts); §5 UI → Task 6 (route, filters, overlays, worktree-dropdown filter); §6 tests → each task's steps + gates. Worktree-step `--ref` requirement → Task 4 Step 3 pre-scan. Drafts-only guard → Task 4 `draft-reply` via `draft save` + Review Focus line.
- **Placeholders:** none — every step has literal code/commands; `resolve_for_message` is defined inline in Task 4 Step 3 (`pick.py` chooser, `--repo` bypasses).
- **Type consistency:** row keys (`id, message_id, thread_id, app_name, sender_username, content, ts, repos, added_at`) identical across Tasks 1/3/5/6; `category` values `("worktree","message")` everywhere; run target keys `message:<id>`.
- **Review Focus:** each of the five lines maps to a Task 4/1/3 test (repos validation → Task 1 Step 5; category rejection → Task 4 Step 1; worktree-step `--ref` → Task 4 Step 3 pre-scan test to add while implementing; ambiguity → `resolve_message` test to add in Task 1 Step 5; send-guard verbatim → Task 4 draft-reply error-path test to add while implementing).
