"""SQLite worktree link rows (split from store_sqlite; re-exported via facade)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .store_sqlite import _norm, connect, init_db


def _upsert_tracker_conn(conn, tid, vendor="", remote_url=""):
    from . import store_sqlite as _sq
    return _sq._upsert_tracker_conn(conn, tid, vendor, remote_url)

def load_links_rows(path: Path, include_inactive: bool = False) -> dict:
    """Reconstruct the legacy links.json dict from worktree rows + payloads."""
    import json
    if not path.exists():
        return {}
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out = {}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
        has_active = "active" in cols
        q = ("SELECT w.*, r.path AS repo_path FROM worktrees w"
             " LEFT JOIN repos r ON r.key_ref = w.repo_key")
        if not include_inactive and has_active:
            q += " WHERE w.active != 0"
        for row in conn.execute(q):
            r = dict(row)
            entry = json.loads(r.pop("payload", "{}") or "{}")
            entry.update({
                "worktree": r["path"], "branch": r["branch"],
                "repo": r.get("repo_path") or r["repo_key"], "issue_url": r["issue_url"],
                "pr_url": r["pr_url"], "added_at": r["added_at"],
                "ref_key": r["ref_key"], "active": r.get("active", 1),
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
                "added_at", "ref_key", "issue", "active")}
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
            conn.execute(
                "INSERT INTO worktrees (ref_key, path, branch, repo_key,"
                " issue_url, pr_url, added_at, payload, active)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(ref_key) DO UPDATE SET path=excluded.path,"
                " branch=excluded.branch, repo_key=excluded.repo_key,"
                " issue_url=excluded.issue_url, pr_url=excluded.pr_url,"
                " added_at=excluded.added_at, payload=excluded.payload"
                + (", active=excluded.active" if has_active else ""),
                (key, str(e.get("worktree", "")), str(e.get("branch", "")),
                 repo_key, str(e.get("issue_url", "")),
                 str(e.get("pr_url", "")), str(e.get("added_at")
                 or datetime.now(timezone.utc).isoformat()),
                 json.dumps(extra), 1 if e.get("active", 1) else 0))
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


