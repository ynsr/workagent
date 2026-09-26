"""SQLite trackers + repos tables (split from store_sqlite; re-exported via facade)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .store_sqlite import _norm, _tracker_meta, connect, init_db, normalize_vendor

def load_trackers(path: Path) -> dict:
    """{tracker_id: {"repos", "vendor", "remote_url}} — SQLite source of truth."""
    if not path.exists():
        return {}
    init_db(path)
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out: dict = {}
        for t in conn.execute("SELECT key_ref, vendor, remote_url FROM trackers ORDER BY key_ref"):
            tid = t["key_ref"]
            rows = conn.execute(
                "SELECT r.path FROM tracker_repos tr JOIN repos r"
                " ON r.key_ref = tr.repo_key WHERE tr.tracker_key = ?"
                " ORDER BY r.path", (tid,))
            out[tid] = {"repos": [r["path"] for r in rows],
                        "vendor": t["vendor"], "remote_url": t["remote_url"]}
        return out


def load_tracker_rows(path: Path) -> list[dict]:
    """Flat tracker rows for CRUD UIs: key/vendor/remote_url + repo count."""
    if not path.exists():
        return []
    init_db(path)
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT t.key_ref AS key, t.vendor, t.remote_url,"
            " COUNT(tr.repo_key) AS repos"
            " FROM trackers t LEFT JOIN tracker_repos tr"
            " ON tr.tracker_key = t.key_ref GROUP BY t.key_ref ORDER BY t.key_ref")]


def upsert_tracker(path: Path, tid: str, vendor: str = "", remote_url: str = "") -> dict:
    """Create/update a tracker row; blank vendor derives from the key.

    remote_url is mandatory and stored as given (no key fallback);
    blank raises ValueError. Returns the stored row.
    """
    init_db(path)
    vendor = normalize_vendor(vendor)
    v, _u = _tracker_meta(tid)
    vendor = vendor or v
    remote_url = (remote_url or "").strip()
    if not remote_url:
        raise ValueError(f"remote_url is required for tracker {tid!r}")
    with connect(path) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES (?, ?, ?)"
                     " ON CONFLICT(key_ref) DO UPDATE SET vendor=excluded.vendor,"
                     " remote_url=excluded.remote_url", (tid, vendor, remote_url))
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT key_ref AS key, vendor, remote_url FROM trackers"
                                 " WHERE key_ref = ?", (tid,)).fetchone())


def _upsert_tracker_conn(conn, tid: str, vendor: str = "", remote_url: str = "") -> None:
    """Conn-scoped ensure (no nested connect → no lock).

    Blank vendor/remote_url never clobbers a customized row: INSERT fills
    derived defaults only when the row is new, and the conflict branch
    updates just the explicitly passed fields.
    """
    vendor = normalize_vendor(vendor)
    v, u = _tracker_meta(tid)
    dv, du = v, u
    if vendor and remote_url:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES (?, ?, ?)"
                     " ON CONFLICT(key_ref) DO UPDATE SET vendor=excluded.vendor,"
                     " remote_url=excluded.remote_url", (tid, vendor, remote_url))
    elif vendor:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES (?, ?, ?)"
                     " ON CONFLICT(key_ref) DO UPDATE SET vendor=excluded.vendor",
                     (tid, vendor, du))
    elif remote_url:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES (?, ?, ?)"
                     " ON CONFLICT(key_ref) DO UPDATE SET remote_url=excluded.remote_url",
                     (tid, dv, remote_url))
    else:
        conn.execute("INSERT OR IGNORE INTO trackers (key_ref, vendor, remote_url)"
                     " VALUES (?, ?, ?)", (tid, dv, du))


def delete_tracker(path: Path, tid: str) -> bool:
    """Delete a tracker row (links cascade); True when it existed."""
    init_db(path)
    with connect(path) as conn:
        return conn.execute("DELETE FROM trackers WHERE key_ref = ?", (tid,)).rowcount > 0


def load_repos(path: Path) -> dict:
    """{name: {path, trackers, remote, tool}} registry from SQLite.

    Trackers come only from the tracker_repos join (repos.tracker_key is
    gone — schema v2). ``trackers`` is the sorted linked-tracker list;
    ``tracker`` keeps the first for backward-compatible callers.
    """
    if not path.exists():
        return {}
    init_db(path)
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out: dict = {}
        for r in conn.execute("SELECT key_ref, name, path, remote, tool FROM repos ORDER BY name"):
            d = dict(r)
            tids = [x["tracker_key"] for x in conn.execute(
                "SELECT tracker_key FROM tracker_repos WHERE repo_key = ? ORDER BY tracker_key",
                (d["key_ref"],))]
            out[d["name"] or d["key_ref"]] = {
                "path": d["path"], "trackers": tids, "tracker": tids[0] if tids else "",
                "remote": d["remote"], "tool": d["tool"] or None}
        return out


def ensure_tracker(path: Path, tid: str) -> None:
    """Ensure a tracker row exists (vendor/remote_url derived from the key)."""
    init_db(path)
    with connect(path) as conn:
        _upsert_tracker_conn(conn, tid)


def add_tracker_repo(path: Path, tid: str, repo_path: str) -> bool:
    """Link *repo_path* under *tid*; True when newly recorded.

    Pure join write: ensures the tracker row (meta derived from the key)
    and a path-keyed repo row, then the tracker_repos link.
    """
    init_db(path)
    norm = _norm(repo_path)
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        _upsert_tracker_conn(conn, tid)
        row = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
        if row is None:
            key = norm
            name = key.rsplit("/", 1)[-1] if "/" in key else key
            conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                         (key, norm, name))
            repo_key = key
        else:
            repo_key = row["key_ref"]
        cur = conn.execute(
            "SELECT 1 FROM tracker_repos WHERE tracker_key = ? AND repo_key = ?",
            (tid, repo_key)).fetchone()
        if cur is None:
            conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES (?, ?)",
                         (tid, repo_key))
            return True
        return False


def remove_tracker_repo(path: Path, tid: str, repo_path: str | None = None) -> bool:
    """Drop one repo link (or the whole tracker when *repo_path* is None)."""
    init_db(path)
    with connect(path) as conn:
        if repo_path is None:
            cur = conn.execute("DELETE FROM trackers WHERE key_ref = ?", (tid,))
            return cur.rowcount > 0
        norm = _norm(repo_path)
        row = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
        if row is None:
            return False
        cur = conn.execute(
            "DELETE FROM tracker_repos WHERE tracker_key = ? AND repo_key = ?",
            (tid, row["key_ref"]))
        if cur.rowcount == 0:
            return False
        left = conn.execute("SELECT 1 FROM tracker_repos WHERE tracker_key = ?",
                            (tid,)).fetchone()
        if left is None:
            conn.execute("DELETE FROM trackers WHERE key_ref = ?", (tid,))
        return True


def register_repo_row(path: Path, name: str, repo_path: str, tracker_key: str,
                      remote: str = "", tool: str | None = None) -> None:
    """Upsert a named registry row and link it to *tracker_key* (join-only).

    tracker_key stays mandatory: an explicit key wins, else the caller
    derives it from the origin remote. The repo row itself carries no
    tracker column — the mapping lives in tracker_repos.
    """
    if not tracker_key:
        raise ValueError("tracker_key is mandatory")
    init_db(path)
    norm = _norm(repo_path)
    with connect(path) as conn:
        # Defer FK enforcement to commit for the whole txn (no-op if set
        # after the first write — issue #30): the same-path rename below
        # re-keys repos.key_ref while children still reference the old key.
        conn.execute("PRAGMA defer_foreign_keys = ON")
        _upsert_tracker_conn(conn, tracker_key)
        # Same normalized path under a different registry name: reuse the
        # existing row (update name/remote/tool) instead of dying on the
        hit = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
        if hit is not None and hit[0] != name:
            # Deferred-FK same-path rename (issue #30): child re-keys and
            # the parent rename land before commit-time checks.
            conn.execute("UPDATE OR IGNORE tracker_repos SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
            conn.execute("UPDATE worktrees SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
            conn.execute("UPDATE repos SET key_ref = ?, name = ?, remote = ?, tool = ?"
                         " WHERE key_ref = ?",
                         (name, name, remote or "", tool or "", hit[0]))
        conn.execute("INSERT INTO repos (key_ref, path, name, remote, tool)"
                     " VALUES (?, ?, ?, ?, ?)"
                     " ON CONFLICT(key_ref) DO UPDATE SET path=excluded.path, name=excluded.name,"
                     " remote=excluded.remote, tool=excluded.tool",
                     (name, norm, name, remote or "", tool or ""))
        conn.execute("INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key) VALUES (?, ?)",
                     (tracker_key, name))


def register_repo_row_unlinked(path: Path, name: str, repo_path: str,
                               remote: str = "", tool: str | None = None) -> None:
    """Refresh remote/tool on a repo row without touching its tracker links.

    Used by the host-tool refresh when the repo survived a `tracker remove`
    cascade unlinked: re-deriving a tracker there could hit the network,
    and the mandatory-tracker guard must not crash a read path.
    """
    init_db(path)
    norm = _norm(repo_path)
    with connect(path) as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        hit = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
        if hit is not None and hit[0] != name:
            conn.execute("UPDATE OR IGNORE tracker_repos SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
            conn.execute("UPDATE worktrees SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
            conn.execute("UPDATE repos SET key_ref = ?, name = ?, remote = ?, tool = ?"
                         " WHERE key_ref = ?",
                         (name, name, remote or "", tool or "", hit[0]))
        conn.execute("INSERT INTO repos (key_ref, path, name, remote, tool)"
                     " VALUES (?, ?, ?, ?, ?)"
                     " ON CONFLICT(key_ref) DO UPDATE SET path=excluded.path, name=excluded.name,"
                     " remote=excluded.remote, tool=excluded.tool",
                     (name, norm, name, remote or "", tool or ""))


def remove_repo_row(path: Path, name: str) -> bool:
    """Delete the *name* registry row; True when it existed.

    tracker_repos, worktrees, sessions, and runs cascade via FK
    (ON DELETE CASCADE); caller guards against live worktrees first.
    """
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM repos WHERE key_ref = ?", (name,))
        return cur.rowcount > 0


def worktree_count_for_repo(path: Path, name: str) -> int:
    """Number of linked worktrees registered under repo *name*."""
    init_db(path)
    with connect(path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM worktrees WHERE repo_key = ?", (name,)).fetchone()[0]


