"""SQLite sessions + runs tables (split from store_sqlite; re-exported via facade)."""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

def _connect(path: Path):
    from .store_sqlite import connect
    return connect(path)

#: Cap on persisted run output lines (mirrors the live buffer policy at a
#: smaller scale: enough log to diagnose, bounded DB growth).
MAX_RUN_OUTPUT_LINES = 2000
def db_path(config_dir: Path | None = None) -> Path:
    from . import store as _store
    base = config_dir or _store.config_dir()
    return base / "state.db"

def session_file_path(session_id: str, harness_name: str,
                      config_dir: Path | None = None,
                      branch: str | None = None) -> Path:
    from . import store as _store
    base = (config_dir or _store.config_dir()) / "sessions" / harness_name
    slug = branch_slug(branch or "")
    if slug:
        base = base / slug
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.jsonl"


def branch_slug(branch: str) -> str:
    """Normalize a git branch name to one path-safe segment.

    `feature/new-feature` → `feature-new-feature` (same rule as
    `refs.slugify_repo`, duplicated to avoid a store→refs import cycle).
    Empty/blank → "".
    """
    import re
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (branch or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def gen_session_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime(
        "%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    return f"{ts}-{random.randint(1000, 9999):04d}"


#: Allowed values for sessions.session_type — an enum, never free text (NULL = unknown).
SESSION_TYPES = ("start", "review", "sync", "fix_comments")

#: Fix-comments prompt header — the only surviving discriminator for history
#: rows (initiator_command carries the harness name, never the subcommand).
_FIX_PROMPT_PREFIX = "Fix all open (not-resolved) review comments on this PR/MR:"


def derive_session_type(command: str, fix_comments: bool = False) -> str | None:
    """Subcommand + flag → session_type. Unknown → None, never guess."""
    c = (command or "").strip().lower()
    if c == "review" and fix_comments:
        return "fix_comments"
    if c in ("start", "review", "sync"):
        return c
    return None


def _backfill_session_type(initiator_command: str, prompt: str) -> str | None:
    """History backfill: prompt header first, then command starts-with."""
    if (prompt or "").startswith(_FIX_PROMPT_PREFIX):
        return "fix_comments"
    first = (initiator_command or "").split(" ", 1)[0].strip().lower()
    if first in ("start", "review", "sync"):
        return first
    if "--fix-comments" in (initiator_command or ""):
        return "fix_comments"
    return None

def insert_session(path: Path, *, worktree_ref: str, harness_name: str,
                   initiator_command: str, prompt: str,
                   file_path: str, session_id: str | None = None,
                   session_type: str | None = None,
                   metadata: dict | None = None) -> str:
    """Insert a running session; same-ms id collision retries with a fresh
    suffix (PK violation → new id, never a crash). Pass session_id to pin
    the row id (e.g. to match the transcript filename)."""
    import json
    if session_type is not None and session_type not in SESSION_TYPES:
        raise ValueError(f"session_type must be one of {', '.join(SESSION_TYPES)} (got {session_type!r})")
    created = datetime.now(timezone.utc).isoformat()
    with _connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        has_type = "session_type" in cols
        has_meta = "metadata" in cols
        for _ in range(3):
            sid = session_id or gen_session_id()
            try:
                if has_type or has_meta:
                    names = ["id", "worktree_ref", "state", "harness_name",
                             "initiator_command", "prompt", "file_path", "created_at"]
                    vals: list = [sid, worktree_ref, "running", harness_name,
                                  initiator_command, prompt, file_path, created]
                    if has_type:
                        names.append("session_type")
                        vals.append(session_type)
                    if has_meta:
                        names.append("metadata")
                        vals.append(json.dumps(metadata or {}))
                    conn.execute(
                        f"INSERT INTO sessions ({', '.join(names)})"
                        f" VALUES ({', '.join(['?'] * len(names))})",
                        vals)
                else:
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
    assert state in ("finished", "failed", "preview")
    with _connect(path) as conn:
        conn.execute("UPDATE sessions SET state = ? WHERE id = ?",
                     (state, sid))


def set_session_file_path(path: Path, sid: str, file_path: str) -> None:
    """Point a session row at the harness-minted transcript (best effort).

    omp mints its own filename under --session-dir, so the pre-launch
    guessed path is only a scope hint. After a blocking launch returns,
    the real .jsonl is known — this records it. Never raises.
    """
    try:
        with _connect(path) as conn:
            conn.execute("UPDATE sessions SET file_path = ? WHERE id = ?",
                         (file_path, sid))
    except Exception:
        pass


def newest_transcript(session_dir: Path) -> Path | None:
    """Newest .jsonl directly under *session_dir* (the harness-minted file).

    None when the dir holds no transcripts yet. Never raises.
    """
    try:
        files = [p for p in session_dir.iterdir()
                 if p.is_file() and p.suffix == ".jsonl"]
    except OSError:
        return None
    if not files:
        return None
    try:
        return max(files, key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        return None


def set_session_metadata(path: Path, sid: str, patch: dict) -> dict:
    """Merge *patch* into the session's metadata JSON; return the merged dict."""
    import json
    with _connect(path) as conn:
        row = conn.execute("SELECT metadata FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            raise KeyError(f"no session {sid}")
        try:
            meta = json.loads(row[0] or "{}")
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.update(patch or {})
        conn.execute("UPDATE sessions SET metadata = ? WHERE id = ?",
                     (json.dumps(meta), sid))
        return meta


def latest_review_session(path: Path, worktree_ref: str) -> dict | None:
    """Newest non-running review session for the ref (the 'current' one to continue)."""
    import json
    if not path.exists():
        return None
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE worktree_ref = ? AND session_type = 'review'"
                " AND state != 'running' ORDER BY created_at DESC LIMIT 1",
                (worktree_ref,)).fetchone()
        except sqlite3.OperationalError:
            return None  # pre-v6 schema: no session_type column yet
        if row is None:
            return None
        out = dict(row)
        try:
            out["metadata"] = json.loads(out.get("metadata") or "{}")
        except Exception:
            out["metadata"] = {}
        return out

def insert_run(path: Path, session_id: str, command: str,
               args: list[str], exit_code: int | None,
               output: list[str] | None = None, truncated: bool = False) -> int:
    import json
    created = datetime.now(timezone.utc).isoformat()
    lines = list(output or [])[-MAX_RUN_OUTPUT_LINES:]
    with _connect(path) as conn:
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
    with _connect(path) as conn:
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


def _parse_metadata(value: object) -> dict:
    import json
    try:
        out = json.loads(value or "{}")  # type: ignore[arg-type]
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}


def list_sessions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out = []
        for r in conn.execute("SELECT * FROM sessions ORDER BY created_at DESC"):
            d = dict(r)
            d["metadata"] = _parse_metadata(d.get("metadata"))
            out.append(d)
        return out


def get_session(path: Path, sid: str) -> dict | None:
    import json
    if not path.exists():
        return None
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM sessions WHERE id = ?",
                           (sid,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["metadata"] = _parse_metadata(out.get("metadata"))
        out["runs"] = [dict(r) for r in conn.execute(
            "SELECT * FROM runs WHERE session_id = ? ORDER BY id", (sid,))]
        for r in out["runs"]:
            r["args"] = json.loads(r["args"])
        return out


