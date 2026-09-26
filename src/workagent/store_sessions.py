"""SQLite sessions + runs tables (split from store_sqlite; re-exported via facade)."""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .store_sqlite import connect

#: Cap on persisted run output lines (mirrors the live buffer policy at a
#: smaller scale: enough log to diagnose, bounded DB growth).
MAX_RUN_OUTPUT_LINES = 2000
def db_path(config_dir: Path | None = None) -> Path:
    from . import store as _store
    base = config_dir or _store.config_dir()
    return base / "state.db"


def session_file_path(session_id: str, harness_name: str,
                      config_dir: Path | None = None) -> Path:
    from . import store as _store
    base = (config_dir or _store.config_dir()) / "sessions" / harness_name
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.jsonl"


def gen_session_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime(
        "%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    return f"{ts}-{random.randint(1000, 9999):04d}"


def insert_session(path: Path, *, worktree_ref: str, harness_name: str,
                   initiator_command: str, prompt: str,
                   file_path: str, session_id: str | None = None) -> str:
    """Insert a running session; same-ms id collision retries with a fresh
    suffix (PK violation → new id, never a crash). Pass session_id to pin
    the row id (e.g. to match the transcript filename)."""
    created = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        for _ in range(3):
            sid = session_id or gen_session_id()
            try:
                conn.execute(
                    "INSERT INTO sessions (id, worktree_ref, state, harness_name,"
                    " initiator_command, prompt, file_path, created_at)"
                    " VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
                    (sid, worktree_ref, harness_name, initiator_command,
                     prompt, file_path, created))
                return sid
            except sqlite3.IntegrityError as e:
                if "FOREIGN KEY" in str(e):
                    raise
                if session_id is not None:
                    raise
                continue
    raise Exception("session id collision — retry the launch")


def finish_session(path: Path, sid: str, state: str) -> None:
    assert state in ("finished", "failed")
    with connect(path) as conn:
        conn.execute("UPDATE sessions SET state = ? WHERE id = ?",
                     (state, sid))


def insert_run(path: Path, session_id: str, command: str,
               args: list[str], exit_code: int | None,
               output: list[str] | None = None, truncated: bool = False) -> int:
    import json
    created = datetime.now(timezone.utc).isoformat()
    lines = list(output or [])[-MAX_RUN_OUTPUT_LINES:]
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (session_id, command, args, exit_code, created_at,"
            " output, truncated) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, command, json.dumps(args), exit_code, created,
             "\n".join(lines), 1 if truncated else 0))
        return cur.lastrowid

def list_runs(path: Path, limit: int = 200) -> list[dict]:
    """Persisted session-linked runs, newest first (drives GET /api/runs)."""
    import json
    if not path.exists():
        return []
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT r.id, r.session_id, r.command, r.args, r.exit_code,"
            " r.created_at, r.output, r.truncated,"
            " s.file_path AS session_file, s.worktree_ref"
            " FROM runs r LEFT JOIN sessions s ON s.id = r.session_id"
            " ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["args"] = json.loads(d["args"])
            except Exception:
                d["args"] = []
            out.append(d)
        return out


def list_sessions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC")]


def get_session(path: Path, sid: str) -> dict | None:
    import json
    if not path.exists():
        return None
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


