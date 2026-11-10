"""Messages store: registered chat messages (messages table)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

#: Supported source apps — an enum, never free text.
APPS = ("mattermost",)


def _connect(db: Path):
    from .store_sqlite import connect
    return connect(db)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row | tuple) -> dict:
    d = dict(r) if isinstance(r, sqlite3.Row) else dict(r)
    try:
        d["repos"] = json.loads(d.get("repos") or "[]")
    except (TypeError, ValueError):
        d["repos"] = []
    return d


def _check_repos(conn, repos) -> list[str]:
    """Validate repos is a non-empty list of known repo keys."""
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
    """Insert a registered message; repos must reference known repo keys."""
    from .errors import HarnessError
    if not (message_id or "").strip():
        raise HarnessError("--message-id is required", exit_code=2)
    if (app_name or "mattermost") not in APPS:
        raise HarnessError(f"app_name must be one of {', '.join(APPS)}",
                           exit_code=2)
    with _connect(db) as conn:
        keys = _check_repos(conn, repos)
        try:
            cur = conn.execute(
                "INSERT INTO messages (message_id, thread_id, app_name,"
                " sender_username, content, ts, repos, added_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id.strip(), thread_id or "", app_name or "mattermost",
                 sender_username or "", content or "", ts or "",
                 json.dumps(keys), _now_iso()))
        except sqlite3.IntegrityError:
            raise HarnessError(
                f"message {message_id!r} already registered",
                exit_code=2) from None
        conn.row_factory = sqlite3.Row
        return _row(conn.execute("SELECT * FROM messages WHERE id = ?",
                                 (cur.lastrowid,)).fetchone())


def list_messages(db: Path, thread_id: str = "") -> list[dict]:
    """All messages (optionally scoped to one thread), oldest first."""
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if thread_id:
            rows = conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY id",
                (thread_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY id").fetchall()
        return [_row(r) for r in rows]


def get_message(db: Path, ref: str) -> dict | None:
    """One message by row id or message_id; None when unknown."""
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute("SELECT * FROM messages WHERE id = ?",
                             (ref,)).fetchone()
        except (sqlite3.InterfaceError, ValueError):
            r = None
        if r is not None:
            return _row(r)
        r = conn.execute("SELECT * FROM messages WHERE message_id = ?",
                         (ref,)).fetchone()
        return _row(r) if r is not None else None


def resolve_message(db: Path, ref: str) -> dict:
    """Exact (id/message_id) → substring fallback; miss/ambiguity → exit 2."""
    from .errors import HarnessError
    hit = get_message(db, ref)
    if hit is not None:
        return hit
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = [_row(r) for r in conn.execute(
            "SELECT * FROM messages WHERE message_id LIKE ?"
            " OR thread_id = ? OR content LIKE ?",
            (f"%{ref}%", ref, f"%{ref}%"))]
    if not rows:
        raise HarnessError(f"unknown message: {ref!r}", exit_code=2)
    if len(rows) > 1:
        raise HarnessError(
            "ambiguous message ref %r: %s"
            % (ref, ", ".join(r["message_id"] for r in rows)),
            exit_code=2)
    return rows[0]


def update_message(db: Path, ref: str, **fields) -> dict:
    """Update mutable fields (pass ``new_message_id`` to rename)."""
    from .errors import HarnessError
    allowed = {"thread_id", "app_name", "sender_username", "content", "ts",
               "repos", "new_message_id"}
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
            raise HarnessError(
                f"app_name must be one of {', '.join(APPS)}", exit_code=2)
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE messages SET {sets} WHERE id = ?",
                         (*fields.values(), base["id"]))
        return _row(conn.execute("SELECT * FROM messages WHERE id = ?",
                                 (base["id"],)).fetchone())


def delete_message(db: Path, ref: str) -> None:
    """Delete one message; unknown ref → HarnessError exit 2."""
    with _connect(db) as conn:
        base = resolve_message(db, ref)
        conn.execute("DELETE FROM messages WHERE id = ?", (base["id"],))
