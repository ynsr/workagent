"""SQLite worktree link rows (split from store_sqlite; re-exported via facade)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .store_sqlite import _norm, connect, init_db
from .store_trackers import _insert_path_keyed_repo


def _upsert_tracker_conn(conn, tid, vendor="", remote_url=""):
    from . import store_sqlite as _sq
    return _sq._upsert_tracker_conn(conn, tid, vendor, remote_url)


def _strip_message_refs_conn(conn, gone: set[str]) -> int:
    """Drop *gone* refs from messages.worktrees JSON lists (rows stay).

    A message keeps living when its worktree dies; only the launch link to
    that worktree is removed. Returns the number of message rows touched.
    """
    if not gone:
        return 0
    touched = 0
    for row in conn.execute("SELECT id, worktrees FROM messages").fetchall():
        try:
            refs = json.loads(row[1] or "[]")
        except Exception:
            continue
        if not isinstance(refs, list):
            continue
        kept = [r for r in refs if r not in gone]
        if len(kept) != len(refs):
            conn.execute("UPDATE messages SET worktrees = ? WHERE id = ?",
                         (json.dumps(kept), row[0]))
            touched += 1
    return touched


def _purge_dependents_conn(conn, keys) -> dict:
    """Delete the records hanging off *keys* after their worktree rows went.

    runs go with the worktree row itself (v26 FK ON DELETE CASCADE). flows,
    messages' worktree links and child ``parent_key_ref`` labels carry no FK,
    so they are deleted/cleared here. Keys already removed from worktrees are
    the normal case — the caller passes the refs it just dropped.
    """
    keys = [k for k in keys if k]
    if not keys:
        return {"flows": 0, "messages": 0, "parent_links": 0}
    marks = ",".join("?" * len(keys))
    flows = conn.execute(f"DELETE FROM flows WHERE worktree_ref IN ({marks})",
                         tuple(keys)).rowcount
    messages = _strip_message_refs_conn(conn, set(keys))
    parents = conn.execute(
        f"UPDATE worktrees SET parent_key_ref = '' WHERE parent_key_ref IN ({marks})",
        tuple(keys)).rowcount
    return {"flows": flows, "messages": messages, "parent_links": parents}


def purge_orphans_conn(conn, dry_run: bool = False) -> dict:
    """Delete records whose ``worktree_ref`` names no worktrees row.

    Covers the tables a hard delete can leave dangling when the worktree row
    vanished before the cascade existed: runs (pre-v26 leftovers), flows,
    messages' worktree links, and child ``parent_key_ref`` labels. The
    orphan runs' transcript targets (``file_path`` + ``metadata.session_dir``)
    are returned so the caller can delete the files after the rows go —
    prodtest_items keep their ref on purpose (issue-scoped ledger
    attribution survives teardown). *dry_run* reports without deleting.
    """
    linked = {r[0] for r in conn.execute("SELECT ref_key FROM worktrees")}
    run_rows = conn.execute(
        "SELECT worktree_ref, file_path, metadata FROM runs"
        " WHERE worktree_ref IS NOT NULL AND worktree_ref <> ''").fetchall()
    run_refs = {r[0] for r in run_rows if r[0] not in linked}
    targets: set[str] = set()
    scoped_dirs: set[str] = set()
    run_count = 0
    for ref, file_path, metadata in run_rows:
        if ref not in run_refs:
            continue
        run_count += 1
        if file_path:
            targets.add(str(file_path))
        try:
            meta = json.loads(metadata or "{}")
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}
        session_dir = str(meta.get("session_dir", "") or "")
        if session_dir:
            targets.add(session_dir)
            if meta.get("branch_path"):
                scoped_dirs.add(session_dir)

    flow_count = conn.execute(
        "SELECT COUNT(*) FROM flows WHERE worktree_ref <> ''"
        " AND worktree_ref NOT IN (SELECT ref_key FROM worktrees)").fetchone()[0]

    dangling_msg: set[str] = set()
    msg_count = 0
    for (raw,) in conn.execute("SELECT worktrees FROM messages").fetchall():
        try:
            refs = json.loads(raw or "[]")
        except Exception:
            continue
        if not isinstance(refs, list):
            continue
        gone = {r for r in refs if r and r not in linked}
        if gone:
            msg_count += 1
            dangling_msg |= gone

    parent_count = conn.execute(
        "SELECT COUNT(*) FROM worktrees WHERE parent_key_ref <> ''"
        " AND parent_key_ref NOT IN (SELECT ref_key FROM worktrees)").fetchone()[0]

    from .store_sqlite import _live_pr_cache_keys_conn
    live_pr_keys = _live_pr_cache_keys_conn(conn)
    orphan_pr_keys = [
        r[0] for r in conn.execute("SELECT branch FROM pr_cache").fetchall()
        if r[0] not in live_pr_keys
    ]
    out = {"runs": run_count, "flows": flow_count, "messages": msg_count,
           "parent_links": parent_count, "pr_cache": len(orphan_pr_keys),
           "transcripts": sorted(targets),
           "scoped_dirs": sorted(scoped_dirs)}
    if dry_run or not any(out[k] for k in ("runs", "flows", "messages", "parent_links", "pr_cache")):
        return out
    # Correlated predicates, not the precomputed ref sets: linked/runs were
    # read in autocommit, so a worktree created in between must not have its
    # fresh run (or flow) swept.
    conn.execute("DELETE FROM runs WHERE worktree_ref IS NOT NULL"
                 " AND worktree_ref <> ''"
                 " AND worktree_ref NOT IN (SELECT ref_key FROM worktrees)")
    conn.execute("DELETE FROM flows WHERE worktree_ref <> ''"
                 " AND worktree_ref NOT IN (SELECT ref_key FROM worktrees)")
    _strip_message_refs_conn(conn, dangling_msg)
    conn.execute("UPDATE worktrees SET parent_key_ref = ''"
                 " WHERE parent_key_ref <> ''"
                 " AND parent_key_ref NOT IN (SELECT ref_key FROM worktrees)")
    for i in range(0, len(orphan_pr_keys), 200):
        batch = orphan_pr_keys[i:i + 200]
        conn.execute(
            f"DELETE FROM pr_cache WHERE branch IN ({','.join('?' for _ in batch)})",
            batch,
        )
    return out


def purge_orphans(path: Path, dry_run: bool = False) -> dict:
    """Scan (and unless *dry_run*, delete) records with no worktree parent."""
    init_db(path)
    with connect(path) as conn:
        return purge_orphans_conn(conn, dry_run=dry_run)


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
                # ref_key may carry a repo suffix (jira:IPG-934@projectx);
                # never leak it into `issue` — downstream parse_ref handles
                # only bare keys (release discovery strips defensively too).
                entry["issue"] = r["ref_key"].split(":", 1)[1].split("@", 1)[0]
            out[r["ref_key"]] = entry
        return out


def _evict_collisions_conn(conn, key: str, wt_path: str, repo_key: str,
                           branch: str) -> None:
    """Free the path / (repo, branch) *key* is about to take (issue #119).

    Only soft-deleted rows are evicted, with their dependents purged. A live
    row holding either raises instead of being silently dropped.
    """
    from .errors import HarnessError
    clauses, params = [], []
    if wt_path:
        norm = _norm(wt_path)
        clauses.append("path IN (?, ?)")
        params += [wt_path, norm]
    if repo_key and branch:
        clauses.append("(repo_key = ? AND branch = ?)")
        params += [repo_key, branch]
    if not clauses:
        return
    rows = conn.execute(
        "SELECT ref_key, deleted_at FROM worktrees WHERE ref_key != ? AND ("
        + " OR ".join(clauses) + ")", (key, *params)).fetchall()
    live = [r[0] for r in rows if not r[1]]
    if live:
        raise HarnessError(
            f"cannot record {key}: worktree path or branch already recorded "
            f"by live link {live[0]}; reuse that key or clean it up first",
            exit_code=1)
    stale = [r[0] for r in rows]
    if stale:
        conn.execute("DELETE FROM worktrees WHERE ref_key IN (%s)"
                     % ",".join("?" * len(stale)), tuple(stale))
        _purge_dependents_conn(conn, stale)


def save_links_rows(path: Path, links: dict) -> None:
    """Merge a legacy links dict into worktree rows (cutover writes).

    Upserts only the given keys and deletes rows absent from the dict —
    never a blanket DELETE, so run rows survive ordinary link mutations.
    Deletes gone keys first; soft-deleted rows colliding on path or
    (repo, branch) are evicted with their dependents, live ones raise
    (issue #119).
    `active` is never reset by a re-save: entries carrying it are honored,
    others keep their stored value (new rows default to 1 via the column
    default). Pruned keys lose their runs with them (v26 FK cascade) plus
    flows, message worktree links, and child parent labels.
    """
    init_db(path)
    with connect(path) as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        if links:
            marks = ",".join("?" * len(links))
            gone = [r[0] for r in conn.execute(
                f"SELECT ref_key FROM worktrees WHERE ref_key NOT IN ({marks})",
                tuple(links))]
            conn.execute(f"DELETE FROM worktrees WHERE ref_key NOT IN ({marks})",
                         tuple(links))
        else:
            gone = [r[0] for r in conn.execute("SELECT ref_key FROM worktrees")]
            conn.execute("DELETE FROM worktrees")
        _purge_dependents_conn(conn, gone)

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
                    repo_key = _insert_path_keyed_repo(
                        conn, norm, norm.rsplit("/", 1)[-1] or norm)
                    if with_tid:
                        conn.execute("INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key)"
                                     " VALUES (?, ?)", (with_tid, repo_key))
                else:
                    repo_key = row[0]
            has_active = "active" in e
            has_deleted = "deleted_at" in e
            has_related = "related_repos" in e
            has_parent = "parent_key_ref" in e
            wt_path_str = str(e.get("worktree", ""))
            _evict_collisions_conn(conn, key, wt_path_str, repo_key,
                                   str(e.get("branch", "")))
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
                (key, wt_path_str, str(e.get("branch", "")),
                 repo_key, str(e.get("issue_url", "")),
                 str(e.get("pr_url", "")), str(e.get("added_at")
                 or datetime.now(timezone.utc).isoformat()),
                 json.dumps(extra), 1 if e.get("active", 1) else 0,
                 str(e.get("deleted_at", "") or ""),
                 json.dumps(e.get("related_repos", [])),
                 str(e.get("parent_key_ref", "") or "")))


def set_worktree_active(path: Path, key: str, active: bool) -> None:
    """Flip a worktree's active flag (pure DB; no git/host side effects)."""
    from .errors import HarnessError
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("UPDATE worktrees SET active = ? WHERE ref_key = ?",
                           (1 if active else 0, key))
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)


def delete_worktree_row(path: Path, key: str) -> dict:
    """Delete a worktree row and every dependent record; files/branch/PR stay.

    runs go with the row (v26 FK ON DELETE CASCADE); flows, message worktree
    links, and child parent labels are purged explicitly. Callers that also
    tear down disk snapshot the transcript targets before calling this.
    Returns cascade counts ``{runs, flows, messages, parent_links}``.
    """
    from .errors import HarnessError
    init_db(path)
    with connect(path) as conn:
        wrow = conn.execute(
            "SELECT w.branch, COALESCE(r.path, w.repo_key), w.payload"
            " FROM worktrees w LEFT JOIN repos r ON r.key_ref = w.repo_key"
            " WHERE w.ref_key = ?",
            (key,),
        ).fetchone()
        run_count = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE worktree_ref = ?", (key,)
        ).fetchone()[0]
        cur = conn.execute("DELETE FROM worktrees WHERE ref_key = ?", (key,))
        deps = _purge_dependents_conn(conn, [key])
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)
        if wrow and wrow[0]:
            b, repo_p = str(wrow[0]), str(wrow[1] or "")
            pr_keys = [k for k in dict.fromkeys((f"{repo_p}\x1f{b}" if repo_p else b, b)) if k]
            conn.execute(
                f"DELETE FROM pr_cache WHERE branch IN ({','.join('?' for _ in pr_keys)})",
                pr_keys,
            )
    return {"runs": run_count, **deps}


def soft_delete_worktree(path: Path, key: str) -> str:
    """Stamp deleted_at (soft-delete); row, its runs, and files all survive.

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



def _prodtest_blocked_conn(conn, ref_key: str, payload_issue: str = "") -> bool:
    """True when a pending/running prodtest item blocks GC for *ref_key*."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(prodtest_items)")}
    except Exception:
        return False
    if "issue_key" not in cols:
        return False
    candidates = {ref_key}
    for cand in (ref_key, payload_issue or ""):
        if cand.startswith("jira:"):
            candidates.add(cand.split(":", 1)[1].split("@", 1)[0])
        elif cand:
            candidates.add(cand)
    for cand in candidates:
        try:
            row = conn.execute(
                "SELECT status FROM prodtest_items WHERE issue_key = ?",
                (cand,)).fetchone()
        except Exception:
            continue
        if row and row[0] in ("pending_prodtest", "running"):
            return True
    return False


SOFT_DELETE_RETENTION_DAYS = 7


def gc_soft_deleted(path: Path, retention_days: int = SOFT_DELETE_RETENTION_DAYS) -> list[dict]:
    """Hard-delete rows soft-deleted longer than *retention_days* ago.

    Returns [{ref_key, path, branch, repo}] for rows the caller must tear
    down on disk (git-wt cleanup) before dropping. Row deletion happens
    here — the worktree row takes its runs along (v26 FK cascade) and its
    flows/message links/parent labels with it — and disk teardown is the
    caller's job so this stays a pure DB function.
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
            "SELECT w.ref_key, w.path, w.branch, w.payload,"
            " COALESCE(r.path, w.repo_key) AS repo"
            " FROM worktrees w LEFT JOIN repos r ON r.key_ref = w.repo_key"
            " WHERE w.deleted_at IS NOT NULL AND w.deleted_at != ''"
            " AND w.deleted_at < ?", (cutoff,))]
        kept = []
        for r in rows:
            try:
                issue = str(json.loads(r.get("payload") or "{}").get("issue", "") or "")
            except Exception:
                issue = ""
            if _prodtest_blocked_conn(conn, r["ref_key"], issue):
                kept.append(r)
        rows = [r for r in rows if r not in kept]
        for r in kept:
            del r["payload"]
        for r in rows:
            key = r["ref_key"]
            run_count = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE worktree_ref = ?", (key,)
            ).fetchone()[0]
            conn.execute("DELETE FROM worktrees WHERE ref_key = ?", (key,))
            deps = _purge_dependents_conn(conn, [key])
            if r.get("branch"):
                b = str(r["branch"])
                rp = str(r.get("repo") or "")
                pr_keys = [k for k in dict.fromkeys((f"{rp}\x1f{b}" if rp else b, b)) if k]
                conn.execute(
                    f"DELETE FROM pr_cache WHERE branch IN ({','.join('?' for _ in pr_keys)})",
                    pr_keys,
                )
            r["cascaded"] = {"runs": run_count, **deps}
            r.pop("payload", None)
    return rows


