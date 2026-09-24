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
  key_ref TEXT PRIMARY KEY, remote_url TEXT NOT NULL, vendor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repos (
  key_ref TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  remote TEXT NOT NULL DEFAULT '', tool TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS tracker_repos (
  tracker_key TEXT NOT NULL REFERENCES trackers(key_ref) ON DELETE CASCADE,
  repo_key TEXT NOT NULL REFERENCES repos(key_ref) ON DELETE CASCADE,
  PRIMARY KEY (tracker_key, repo_key));
CREATE TABLE IF NOT EXISTS worktrees (
  ref_key TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
  branch TEXT UNIQUE NOT NULL, repo_key TEXT REFERENCES repos(key_ref) ON DELETE CASCADE,
  issue_url TEXT NOT NULL DEFAULT '', pr_url TEXT NOT NULL DEFAULT '',
  added_at TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}');
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
CREATE TABLE IF NOT EXISTS issue_cache (
  source TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '[]',
  fetched_at TEXT NOT NULL DEFAULT '');
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _tracker_meta(tid: str) -> tuple[str, str]:
    """(vendor, remote_url) for *tid*; both mandatory, never ''.

    jira:PREFIX → ("jira", "<jira-site>/browse/PREFIX" or "jira:PREFIX").
    github:O/R → ("github", "https://github.com/O/R").
    gitlab:host/g/r → ("gitlab", "https://host/g/r").
    """
    if tid.startswith("jira:"):
        vendor = "jira"
        try:
            from .refs import jira_site as _site
            site = _site() or ""
        except Exception:
            site = ""
        prefix = tid.split(":", 1)[1]
        remote_url = f"{site.rstrip('/')}/browse/{prefix}" if site else tid
        return vendor, remote_url
    if tid.startswith("github:"):
        return "github", f"https://github.com/{tid.split(':', 1)[1]}"
    if tid.startswith("gitlab:"):
        rest = tid.split(":", 1)[1]
        host, _, repo = rest.partition("/")
        return "gitlab", f"https://{rest}" if "/" in rest and host else tid
    return "unknown", tid


def _migrate_v2(conn) -> None:
    """Schema v2: trackers gains mandatory vendor/remote_url; repos loses tracker_key.

    - Legacy trackers rows (nullable/'' vendor/remote_url) are backfilled
      from the key via _tracker_meta.
    - Legacy repos.tracker_key values seed missing tracker rows + links,
      then the column is dropped (SQLite ≥3.35 DROP COLUMN).
    Idempotent: re-runs are no-ops.
    """
    tcols = {r[1]: r for r in conn.execute("PRAGMA table_info(trackers)")}
    if tcols:
        if "vendor" not in tcols:
            conn.execute("ALTER TABLE trackers ADD COLUMN vendor TEXT NOT NULL DEFAULT 'unknown'")
        if "remote_url" not in tcols:
            conn.execute("ALTER TABLE trackers ADD COLUMN remote_url TEXT NOT NULL DEFAULT ''")
        for row in conn.execute("SELECT key_ref, vendor, remote_url FROM trackers"):
            tid, vendor, remote_url = row[0], row[1], row[2]
            if not vendor or not remote_url:
                v, u = _tracker_meta(tid)
                conn.execute("UPDATE trackers SET vendor = ?, remote_url = ? WHERE key_ref = ?",
                             (v or "unknown", u or tid, tid))
        conn.execute("UPDATE trackers SET vendor = 'unknown' WHERE vendor IS NULL OR vendor = ''")
        conn.execute("UPDATE trackers SET remote_url = key_ref WHERE remote_url IS NULL OR remote_url = ''")
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if "tracker_key" in rcols:
        for row in conn.execute("SELECT key_ref, path, COALESCE(tracker_key, '') FROM repos"):
            repo_key, repo_path, legacy = row[0], row[1], row[2]
            if not legacy:
                continue
            v, u = _tracker_meta(legacy)
            conn.execute("INSERT OR IGNORE INTO trackers (key_ref, vendor, remote_url)"
                         " VALUES (?, ?, ?)", (legacy, v, u))
            conn.execute("INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key)"
                         " VALUES (?, ?)", (legacy, repo_key))
        conn.execute("ALTER TABLE repos DROP COLUMN tracker_key")


def init_db(path: Path) -> Path:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate_v2(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(worktrees)")]
        if "payload" not in cols:
            conn.execute("ALTER TABLE worktrees ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
        rcols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
        if "name" not in rcols:
            conn.execute("ALTER TABLE repos ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        if "tool" not in rcols:
            conn.execute("ALTER TABLE repos ADD COLUMN tool TEXT NOT NULL DEFAULT ''")
    return path
def _norm(p: str) -> str:
    from pathlib import Path as _P
    return str(_P(p).expanduser().resolve()) if p else ""


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
    """Create/update a tracker row; vendor/remote_url default from the key.

    Both columns are mandatory (NOT NULL, never ''): blanks are derived
    via _tracker_meta. Returns the stored row.
    """
    init_db(path)
    v, u = _tracker_meta(tid)
    vendor = vendor or v
    remote_url = remote_url or u
    if not vendor:
        vendor = "unknown"
    if not remote_url:
        remote_url = tid
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
    v, u = _tracker_meta(tid)
    dv, du = v or "unknown", u or tid
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
    upsert_tracker(path, tid)

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
        _upsert_tracker_conn(conn, tracker_key)
        # Same normalized path under a different registry name: reuse the
        # existing row (update name/remote/tool) instead of dying on the
        # path UNIQUE constraint.
        hit = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
        if hit is not None and hit[0] != name:
            conn.execute("UPDATE repos SET key_ref = ?, name = ?, remote = ?, tool = ?"
                         " WHERE key_ref = ?",
                         (name, name, remote or "", tool or "", hit[0]))
            conn.execute("UPDATE OR IGNORE tracker_repos SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
            conn.execute("UPDATE worktrees SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
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
        hit = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
        if hit is not None and hit[0] != name:
            conn.execute("UPDATE repos SET key_ref = ?, name = ?, remote = ?, tool = ?"
                         " WHERE key_ref = ?",
                         (name, name, remote or "", tool or "", hit[0]))
            conn.execute("UPDATE OR IGNORE tracker_repos SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
            conn.execute("UPDATE worktrees SET repo_key = ? WHERE repo_key = ?",
                         (name, hit[0]))
        conn.execute("INSERT INTO repos (key_ref, path, name, remote, tool)"
                     " VALUES (?, ?, ?, ?, ?)"
                     " ON CONFLICT(key_ref) DO UPDATE SET path=excluded.path, name=excluded.name,"
                     " remote=excluded.remote, tool=excluded.tool",
                     (name, norm, name, remote or "", tool or ""))
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM repos WHERE key_ref = ?", (name,))
        return cur.rowcount > 0


def backfill_trackers_repos(path: Path, cfg: dict,
                            derive_tracker=None) -> dict:
    """One-shot backfill of config.json {repos, trackers} into state.db.

    Returns {"trackers": N, "repos": M, "links": L}. Creates tracker rows
    (vendor/remote_url derived from the key) and repo rows, then the
    tracker_repos links: explicit mapping wins, else *derive_tracker*
    (origin-remote derivation); still empty → ValueError naming the repo.
    """
    init_db(path)
    counts = {"trackers": 0, "repos": 0, "links": 0}
    with connect(path) as conn:
        # Idempotency guard: every repo row already linked to ≥1 tracker
        # is a no-op. Unlinked rows (e.g. auto-created by migrate_json
        # before this backfill ran) still need linking below.
        n = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
        empty = conn.execute("SELECT COUNT(*) FROM repos r WHERE NOT EXISTS"
                             " (SELECT 1 FROM tracker_repos tr WHERE tr.repo_key = r.key_ref)").fetchone()[0]
        if n and not empty:
            return counts
    cfg_repos = cfg.get("repos", {}) or {}
    cfg_trackers = cfg.get("trackers", {}) or {}
    # Reverse map: resolved repo path → tracker id (first write wins;
    # host-scoped ids beat jira: prefixes — same rule as _repo_tracker_map).
    path_to_tid: dict = {}
    for tid, entry in cfg_trackers.items():
        for r in (entry or {}).get("repos", []):
            try:
                key = _norm(str(r))
            except Exception:
                continue
            cur = path_to_tid.get(key, "")
            if not cur or (cur.startswith("jira:") and str(tid).startswith(("github:", "gitlab:"))):
                path_to_tid[key] = tid
    with connect(path) as conn:
        for tid in cfg_trackers:
            v, u = _tracker_meta(tid)
            counts["trackers"] += conn.execute(
                "INSERT OR IGNORE INTO trackers (key_ref, vendor, remote_url) VALUES (?, ?, ?)",
                (tid, v, u)).rowcount
        for name, v in cfg_repos.items():
            raw_path = str((v or {}).get("path", ""))
            norm = _norm(raw_path)
            tid = path_to_tid.get(norm, "")
            if not tid and derive_tracker is not None:
                try:
                    tid = derive_tracker(raw_path) or ""
                except Exception:
                    tid = ""
            if not tid:
                raise ValueError(f"cannot derive tracker for repo {name!r} ({raw_path}): pass --tracker")
            vm, um = _tracker_meta(tid)
            conn.execute("INSERT OR IGNORE INTO trackers (key_ref, vendor, remote_url)"
                         " VALUES (?, ?, ?)", (tid, vm, um))
            # Existing rows may be keyed by path (older migrate_json runs):
            # update in place (keep key_ref so worktrees.repo_key refs stay
            # valid) instead of dying on the path UNIQUE.
            row = conn.execute("SELECT key_ref FROM repos WHERE path = ? OR key_ref = ?",
                               (norm, name)).fetchone()
            if row is None:
                conn.execute("INSERT INTO repos (key_ref, path, name, remote, tool)"
                             " VALUES (?, ?, ?, ?, ?)",
                             (name, norm, name, str((v or {}).get("remote", "") or ""),
                              str((v or {}).get("tool", "") or "")))
                counts["repos"] += 1
                repo_key = name
            else:
                repo_key = row[0]
                counts["repos"] += conn.execute(
                    "UPDATE repos SET name = ?, remote = ?, tool = ? WHERE key_ref = ?",
                    (name, str((v or {}).get("remote", "") or ""),
                     str((v or {}).get("tool", "") or ""), repo_key)).rowcount
            counts["links"] += conn.execute(
                "INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key) VALUES (?, ?)",
                (tid, repo_key)).rowcount
        for tid, entry in cfg_trackers.items():
            for r in (entry or {}).get("repos", []):
                norm = _norm(str(r))
                row = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
                if row is None:
                    key = norm
                    name = key.rsplit("/", 1)[-1] if "/" in key else key
                    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                                 (key, norm, name or key))
                    repo_key = key
                else:
                    repo_key = row[0]
                counts["links"] += conn.execute(
                    "INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key) VALUES (?, ?)",
                    (tid, repo_key)).rowcount
    return counts


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
                    "INSERT INTO sessions (id, worktree_ref, state, runtime_name,"
                    " initiator_command, prompt, file_path, created_at)"
                    " VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
                    (sid, worktree_ref, runtime_name, initiator_command,
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


def load_links_rows(path: Path) -> dict:
    """Reconstruct the legacy links.json dict from worktree rows + payloads."""
    import json
    if not path.exists():
        return {}
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        out = {}
        for row in conn.execute("SELECT w.*, r.path AS repo_path FROM worktrees w"
                                " LEFT JOIN repos r ON r.key_ref = w.repo_key"):
            r = dict(row)
            entry = json.loads(r.pop("payload", "{}") or "{}")
            entry.update({
                "worktree": r["path"], "branch": r["branch"],
                "repo": r.get("repo_path") or r["repo_key"], "issue_url": r["issue_url"],
                "pr_url": r["pr_url"], "added_at": r["added_at"],
                "ref_key": r["ref_key"],
            })
            if "issue" not in entry and r["ref_key"].startswith("jira:"):
                entry["issue"] = r["ref_key"].split(":", 1)[1]
            out[r["ref_key"]] = entry
        return out


def save_links_rows(path: Path, links: dict) -> None:
    """Merge a legacy links dict into worktree rows (cutover writes).

    Upserts only the given keys and deletes rows absent from the dict —
    never a blanket DELETE, so session rows (FK → worktrees) survive
    ordinary link mutations.
    """
    import json
    init_db(path)
    with connect(path) as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        for key, e in links.items():
            extra = {k: v for k, v in e.items() if k not in (
                "worktree", "branch", "repo", "issue_url", "pr_url",
                "added_at", "ref_key", "issue")}
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
            conn.execute(
                "INSERT INTO worktrees (ref_key, path, branch, repo_key,"
                " issue_url, pr_url, added_at, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(ref_key) DO UPDATE SET path=excluded.path,"
                " branch=excluded.branch, repo_key=excluded.repo_key,"
                " issue_url=excluded.issue_url, pr_url=excluded.pr_url,"
                " added_at=excluded.added_at, payload=excluded.payload",
                (key, str(e.get("worktree", "")), str(e.get("branch", "")),
                 repo_key, str(e.get("issue_url", "")),
                 str(e.get("pr_url", "")), str(e.get("added_at", "")),
                 json.dumps(extra)))
        if links:
            conn.execute(
                "DELETE FROM worktrees WHERE ref_key NOT IN (%s)" %
                ",".join("?" * len(links)), tuple(links))
        else:
            conn.execute("DELETE FROM worktrees")


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
            extra = {k: v for k, v in e.items() if k not in (
                "worktree", "branch", "repo", "issue_url", "pr_url",
                "added_at", "ref_key", "issue")}
            repo = str(e.get("repo", ""))
            repo_key = ""
            if repo:
                row = conn.execute("SELECT key_ref FROM repos WHERE path = ?",
                                   (repo,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                        (repo, repo, repo.rsplit("/", 1)[-1]))
                    repo_key = repo
                else:
                    repo_key = row[0]
            conn.execute(
                "INSERT INTO worktrees (ref_key, path, branch, repo_key,"
                " issue_url, pr_url, added_at, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, str(e.get("worktree", "")), str(e.get("branch", "")),
                 repo_key, str(e.get("issue_url", "")),
                 str(e.get("pr_url", "")), str(e.get("added_at", "")),
                 json.dumps(extra)))
            counts["worktrees"] += 1
        for branch, entry in pr_cache.items():
            full = dict(entry)
            full.setdefault("pr", None)
            checked = str(full.get("checked_at", ""))
            conn.execute(
                "INSERT INTO pr_cache (branch, payload, checked_at)"
                " VALUES (?, ?, ?)",
                (branch, json.dumps(full), checked))
            counts["pr_cache"] += 1
    if counts["worktrees"] != len(links) or counts["pr_cache"] != len(pr_cache):
        raise HarnessError("migration count mismatch — files left in place",
                           exit_code=2)
    for name in ("links.json", "pr_cache.json", "harnesses.json"):
        p = config_dir / name
        if p.exists():
            p.unlink()
    return counts

def get_issue_cache(path: Path, source: str) -> tuple[list, str | None]:
    """Cached issue rows for *source* + fetched_at ISO, or ([], None)."""
    import json
    init_db(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM issue_cache WHERE source = ?",
            (source,)).fetchone()
    if row is None:
        return [], None
    return json.loads(row[0]), row[1]


def set_issue_cache(path: Path, source: str, rows: list[dict]) -> None:
    """Upsert cached issue rows for *source* with a fresh fetched_at."""
    import json
    from datetime import datetime, timezone
    init_db(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO issue_cache (source, payload, fetched_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,"
            " fetched_at=excluded.fetched_at",
            (source, json.dumps(rows),
             datetime.now(timezone.utc).isoformat()))


def clear_issue_cache(path: Path) -> None:
    """Drop all cached issue rows (reset-cache path)."""
    init_db(path)
    with connect(path) as conn:
        conn.execute("DELETE FROM issue_cache")
