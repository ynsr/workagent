"""SQLite worktree link rows (split from store_sqlite; re-exported via facade)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .store_sqlite import _norm, connect, init_db


def _upsert_tracker_conn(conn, tid, vendor="", remote_url=""):
    from . import store_sqlite as _sq
    return _sq._upsert_tracker_conn(conn, tid, vendor, remote_url)

def load_links_rows(path: Path, include_inactive: bool = False,
                    include_deleted: bool = False) -> dict:
    """Reconstruct the legacy links.json dict from worktree rows + payloads."""
    import json
    if not path.exists():
        return {}
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out = {}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
        has_active = "active" in cols
        has_deleted = "deleted_at" in cols
        q = ("SELECT w.*, r.path AS repo_path FROM worktrees w"
             " LEFT JOIN repos r ON r.key_ref = w.repo_key")
        where = []
        if not include_inactive and has_active:
            where.append("w.active != 0")
        if not include_deleted and has_deleted:
            where.append("(w.deleted_at IS NULL OR w.deleted_at = '')")
        if where:
            q += " WHERE " + " AND ".join(where)
        for row in conn.execute(q):
            r = dict(row)
            entry = json.loads(r.pop("payload", "{}") or "{}")
            entry.update({
                "worktree": r["path"], "branch": r["branch"],
                "repo": r.get("repo_path") or r["repo_key"], "issue_url": r["issue_url"],
                "pr_url": r["pr_url"], "added_at": r["added_at"],
                "ref_key": r["ref_key"], "active": r.get("active", 1),
                "deleted_at": r.get("deleted_at", "") or "",
                "related_repos": json.loads(r.get("related_repos") or "[]"),
                "parent_key_ref": r.get("parent_key_ref", "") or "",
            })
            if "issue" not in entry and r["ref_key"].startswith("jira:"):
                entry["issue"] = r["ref_key"].split(":", 1)[1]
            out[r["ref_key"]] = entry
        return out


def save_links_rows(path: Path, links: dict) -> None:
    """Merge a legacy links dict into worktree rows (cutover writes).

    Upserts only the given keys and deletes rows absent from the dict —
    never a blanket DELETE, so session rows (FK → worktrees) survive
    ordinary link mutations. `active` is never reset by a re-save:
    entries carrying it are honored, others keep their stored value
    (new rows default to 1 via the column default).
    """
    import json
    init_db(path)
    with connect(path) as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        for key, e in links.items():
            extra = {k: v for k, v in e.items() if k not in (
                "worktree", "branch", "repo", "issue_url", "pr_url",
                "added_at", "ref_key", "issue", "active", "deleted_at",
                "related_repos", "parent_key_ref")}
            repo = str(e.get("repo", ""))
            repo_key = ""
            if repo:
                norm = _norm(repo)
                row = conn.execute(
                    "SELECT key_ref FROM repos WHERE path = ? OR key_ref = ?",
                    (norm, repo)).fetchone()
                if row is None:
                    with_tid = extra.get("tracker", "")
                    if with_tid:
                        _upsert_tracker_conn(conn, with_tid)
                    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                                 (norm, norm, norm.rsplit("/", 1)[-1] or norm))
                    repo_key = norm
                    if with_tid:
                        conn.execute("INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key)"
                                     " VALUES (?, ?)", (with_tid, repo_key))
                else:
                    repo_key = row[0]
            has_active = "active" in e
            has_deleted = "deleted_at" in e
            has_related = "related_repos" in e
            has_parent = "parent_key_ref" in e
            conn.execute(
                "INSERT INTO worktrees (ref_key, path, branch, repo_key,"
                " issue_url, pr_url, added_at, payload, active, deleted_at,"
                " related_repos, parent_key_ref)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(ref_key) DO UPDATE SET path=excluded.path,"
                " branch=excluded.branch, repo_key=excluded.repo_key,"
                " issue_url=excluded.issue_url, pr_url=excluded.pr_url,"
                " added_at=excluded.added_at, payload=excluded.payload"
                + (", active=excluded.active" if has_active else "")
                + (", deleted_at=excluded.deleted_at" if has_deleted else "")
                + (", related_repos=excluded.related_repos" if has_related else "")
                + (", parent_key_ref=excluded.parent_key_ref" if has_parent else ""),
                (key, str(e.get("worktree", "")), str(e.get("branch", "")),
                 repo_key, str(e.get("issue_url", "")),
                 str(e.get("pr_url", "")), str(e.get("added_at")
                 or datetime.now(timezone.utc).isoformat()),
                 json.dumps(extra), 1 if e.get("active", 1) else 0,
                 str(e.get("deleted_at", "") or ""),
                 json.dumps(e.get("related_repos", [])),
                 str(e.get("parent_key_ref", "") or "")))
        if links:
            conn.execute(
                "DELETE FROM worktrees WHERE ref_key NOT IN (%s)" %
                ",".join("?" * len(links)), tuple(links))
        else:
            conn.execute("DELETE FROM worktrees")


def set_worktree_active(path: Path, key: str, active: bool) -> None:
    """Flip a worktree's active flag (pure DB; no git/host side effects)."""
    from .errors import HarnessError
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("UPDATE worktrees SET active = ? WHERE ref_key = ?",
                           (1 if active else 0, key))
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)


def delete_worktree_row(path: Path, key: str) -> None:
    """Delete a worktree row only; files/branch/PR untouched.

    Note: sessions/runs CASCADE off the worktree row, so this key's
    session history is deleted too.
    """
    from .errors import HarnessError
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM worktrees WHERE ref_key = ?", (key,))
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)

def soft_delete_worktree(path: Path, key: str) -> str:
    """Stamp deleted_at (soft-delete); row, sessions, and files all survive.

    Returns the ISO timestamp written. Idempotent: re-stamping refreshes
    the clock (extends the 7-day GC window).
    """
    from .errors import HarnessError
    init_db(path)
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        cur = conn.execute("UPDATE worktrees SET deleted_at = ? WHERE ref_key = ?",
                           (now, key))
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)
    return now


def undelete_worktree(path: Path, key: str) -> None:
    """Clear deleted_at (restore a soft-deleted row)."""
    from .errors import HarnessError
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("UPDATE worktrees SET deleted_at = '' WHERE ref_key = ?",
                           (key,))
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)


SOFT_DELETE_RETENTION_DAYS = 7


def gc_soft_deleted(path: Path, retention_days: int = SOFT_DELETE_RETENTION_DAYS) -> list[dict]:
    """Hard-delete rows soft-deleted longer than *retention_days* ago.

    Returns [{ref_key, path, branch, repo}] for rows the caller must tear
    down on disk (git-wt cleanup) before dropping. Row deletion happens
    here (sessions/runs CASCADE); disk teardown is the caller's job so
    this stays a pure DB function.
    """
    from datetime import timedelta
    init_db(path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
        if "deleted_at" not in cols:
            return []
        rows = [dict(r) for r in conn.execute(
            "SELECT w.ref_key, w.path, w.branch,"
            " COALESCE(r.path, w.repo_key) AS repo"
            " FROM worktrees w LEFT JOIN repos r ON r.key_ref = w.repo_key"
            " WHERE w.deleted_at IS NOT NULL AND w.deleted_at != ''"
            " AND w.deleted_at < ?", (cutoff,))]
        for r in rows:
            conn.execute("DELETE FROM worktrees WHERE ref_key = ?", (r["ref_key"],))
    return rows


