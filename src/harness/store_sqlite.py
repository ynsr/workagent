"""SQLite state: trackers, repos, worktrees, pr_cache, sessions, runs.

Stdlib sqlite3 repository (no ORM — see the sessions spec §7). One
state.db in config_dir() holds everything after the one-shot
JSON→SQLite migration (migrate_json below).
"""
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


def gen_session_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime(
        "%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    return f"{ts}-{random.randint(1000, 9999):04d}"


def insert_session(path: Path, *, worktree_ref: str, runtime_name: str,
                   initiator_command: str, prompt: str,
                   file_path: str) -> str:
    """Insert a running session; same-ms id collision retries with a fresh
    suffix (PK violation → new id, never a crash)."""
    created = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        for _ in range(3):
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
        conn.execute("UPDATE sessions SET state = ? WHERE id = ?",
                     (state, sid))


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


def migrate_json(config_dir: Path, db_path: Path) -> dict:
    """One-shot migration: links/pr_cache/harnesses JSON → state.db.

    Verifies row counts, then deletes the three JSON files; config.json
    is kept. Corrupt JSON or count mismatch aborts WITHOUT deleting
    (raise HarnessError, exit 2 at the CLI). Idempotent: an existing db
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
    _load("harnesses.json")  # validated, then dropped (live-pid guard stays out of the db)
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
