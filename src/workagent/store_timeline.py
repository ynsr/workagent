"""Normalized PR checks, review threads, and worktree feedback timeline events."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

FAILING_CONCLUSIONS: frozenset[str] = frozenset(
    {
        "failure",
        "failed",
        "timed_out",
        "action_required",
        "startup_failure",
        "cancelled",
        "canceled",
    }
)


def is_failing_check(check: dict[str, Any]) -> bool:
    """Return True when a normalized PR check represents a failure."""
    return (
        str(check.get("conclusion") or "").lower() in FAILING_CONCLUSIONS
        or str(check.get("status") or "").lower() == "failed"
    )


def failing_check_names(checks: Sequence[dict[str, Any]]) -> list[str]:
    """Return names of failing checks from a sequence of normalized check dicts."""
    return [
        str(c["name"])
        for c in checks
        if is_failing_check(c) and c.get("name")
    ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _worktree_exists(conn: sqlite3.Connection, worktree_ref: str) -> bool:
    if not worktree_ref:
        return False
    row = conn.execute(
        "SELECT 1 FROM worktrees WHERE ref_key = ? LIMIT 1", (worktree_ref,)
    ).fetchone()
    return row is not None


def replace_pr_checks(
    path: Path, worktree_ref: str, checks: Sequence[dict[str, Any]]
) -> int:
    """Replace all pr_checks rows for *worktree_ref*. Returns count written.

    No-ops and returns 0 if *worktree_ref* does not exist in ``worktrees``.
    """
    from . import store_sqlite as sq

    sq.init_db(path)
    now = _now_iso()
    with sq.connect(path) as conn:
        if not _worktree_exists(conn, worktree_ref):
            return 0
        conn.execute("DELETE FROM pr_checks WHERE worktree_ref = ?", (worktree_ref,))
        count = 0
        for chk in checks or ():
            name = str(chk.get("name") or "").strip()
            if not name:
                continue
            conn.execute(
                "INSERT INTO pr_checks"
                " (worktree_ref, name, commit_sha, status, conclusion, url, log_tail, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    worktree_ref,
                    name,
                    str(chk.get("commit_sha") or ""),
                    str(chk.get("status") or ""),
                    str(chk.get("conclusion") or ""),
                    str(chk.get("url") or ""),
                    str(chk.get("log_tail") or ""),
                    str(chk.get("updated_at") or now),
                ),
            )
            count += 1
        return count


def list_pr_checks(path: Path, worktree_ref: str) -> list[dict[str, Any]]:
    """Return normalized PR check rows for *worktree_ref*."""
    from . import store_sqlite as sq

    if not worktree_ref or not path.exists():
        return []
    sq.init_db(path)
    with sq.connect(path) as conn:
        rows = conn.execute(
            "SELECT id, worktree_ref, name, commit_sha, status, conclusion,"
            " url, log_tail, updated_at"
            " FROM pr_checks WHERE worktree_ref = ? ORDER BY id ASC",
            (worktree_ref,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "worktree_ref": r[1],
            "name": r[2],
            "commit_sha": r[3],
            "status": r[4],
            "conclusion": r[5],
            "url": r[6],
            "log_tail": r[7],
            "updated_at": r[8],
        }
        for r in rows
    ]


def replace_pr_review_threads(
    path: Path, worktree_ref: str, threads: Sequence[dict[str, Any]]
) -> int:
    """Replace all pr_review_threads rows for *worktree_ref*. Returns count written.

    No-ops and returns 0 if *worktree_ref* does not exist in ``worktrees``.
    """
    from . import store_sqlite as sq

    sq.init_db(path)
    now = _now_iso()
    with sq.connect(path) as conn:
        if not _worktree_exists(conn, worktree_ref):
            return 0
        conn.execute(
            "DELETE FROM pr_review_threads WHERE worktree_ref = ?", (worktree_ref,)
        )
        seen: set[str] = set()
        count = 0
        for th in threads or ():
            tid = str(th.get("thread_id") or "").strip()
            if not tid or tid in seen:
                continue
            seen.add(tid)
            line_val = th.get("line")
            try:
                line_int = int(line_val) if line_val is not None and str(line_val) != "" else None
            except (TypeError, ValueError):
                line_int = None
            conn.execute(
                "INSERT INTO pr_review_threads"
                " (worktree_ref, thread_id, author, file, line, body, resolved, is_bot, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    worktree_ref,
                    tid,
                    str(th.get("author") or ""),
                    str(th.get("file") or ""),
                    line_int,
                    str(th.get("body") or ""),
                    1 if th.get("resolved") else 0,
                    1 if th.get("is_bot") else 0,
                    str(th.get("updated_at") or now),
                ),
            )
            count += 1
        return count


def list_pr_review_threads(
    path: Path, worktree_ref: str, *, unresolved_only: bool = False
) -> list[dict[str, Any]]:
    """Return normalized PR review thread rows for *worktree_ref*."""
    from . import store_sqlite as sq

    if not worktree_ref or not path.exists():
        return []
    sq.init_db(path)
    where = "worktree_ref = ? AND resolved = 0" if unresolved_only else "worktree_ref = ?"
    with sq.connect(path) as conn:
        rows = conn.execute(
            f"SELECT id, worktree_ref, thread_id, author, file, line, body,"
            f" resolved, is_bot, updated_at"
            f" FROM pr_review_threads WHERE {where} ORDER BY id ASC",
            (worktree_ref,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "worktree_ref": r[1],
            "thread_id": r[2],
            "author": r[3],
            "file": r[4],
            "line": r[5],
            "body": r[6],
            "resolved": bool(r[7]),
            "is_bot": bool(r[8]),
            "updated_at": r[9],
        }
        for r in rows
    ]


_STATE_TRANSITION_EVENTS = frozenset({
    "ci_passed",
    "ci_failed",
    "ci_running",
    "review_comments_arrived",
    "review_comments_resolved",
})


def append_worktree_event(
    path: Path,
    worktree_ref: str,
    event_type: str,
    summary: str = "",
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> int | None:
    """Append a chronological event for *worktree_ref*.

    Consecutive identical state-transition events (same ``event_type`` and
    ``summary``) are deduplicated so concurrent status refreshes cannot
    record redundant history lines.

    Returns the new (or existing deduplicated) row id, or ``None`` if
    *worktree_ref* does not exist.
    """
    from . import store_sqlite as sq

    if not worktree_ref or not event_type:
        return None
    sq.init_db(path)
    ts = created_at or _now_iso()
    ev_type_s = str(event_type)
    summary_s = str(summary or "")
    payload = json.dumps(details or {}, sort_keys=True)
    conn = sq.connect(path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not _worktree_exists(conn, worktree_ref):
            conn.execute("COMMIT")
            return None
        if ev_type_s in _STATE_TRANSITION_EVENTS:
            last = conn.execute(
                "SELECT id, event_type, summary FROM worktree_events"
                " WHERE worktree_ref = ?"
                " ORDER BY created_at DESC, id DESC LIMIT 1",
                (worktree_ref,),
            ).fetchone()
            if last is not None and last[1] == ev_type_s and last[2] == summary_s:
                conn.execute("COMMIT")
                return int(last[0])
        cur = conn.execute(
            "INSERT INTO worktree_events"
            " (worktree_ref, event_type, summary, details, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (worktree_ref, ev_type_s, summary_s, payload, ts),
        )
        row_id = int(cur.lastrowid) if cur.lastrowid is not None else None
        conn.execute("COMMIT")
        return row_id
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def list_worktree_events(
    path: Path, worktree_ref: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Return chronological events for *worktree_ref* (oldest→newest, capped at *limit* most recent)."""
    from . import store_sqlite as sq

    if not worktree_ref or not path.exists():
        return []
    sq.init_db(path)
    lim = max(1, min(int(limit or 100), 500))
    with sq.connect(path) as conn:
        rows = conn.execute(
            "SELECT id, worktree_ref, event_type, summary, details, created_at"
            " FROM ("
            "   SELECT id, worktree_ref, event_type, summary, details, created_at"
            "   FROM worktree_events WHERE worktree_ref = ?"
            "   ORDER BY created_at DESC, id DESC LIMIT ?"
            " ) ORDER BY created_at ASC, id ASC",
            (worktree_ref, lim),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            det = json.loads(r[4] or "{}")
            if not isinstance(det, dict):
                det = {}
        except json.JSONDecodeError:
            det = {}
        out.append(
            {
                "id": r[0],
                "worktree_ref": r[1],
                "event_type": r[2],
                "summary": r[3],
                "details": det,
                "created_at": r[5],
            }
        )
    return out
