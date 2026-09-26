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
CREATE TABLE IF NOT EXISTS trackers (
  key_ref TEXT PRIMARY KEY NOT NULL, remote_url TEXT NOT NULL, vendor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repos (
  key_ref TEXT PRIMARY KEY NOT NULL, path TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  remote TEXT NOT NULL DEFAULT '', tool TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS tracker_repos (
  tracker_key TEXT NOT NULL REFERENCES trackers(key_ref) ON DELETE CASCADE,
  repo_key TEXT NOT NULL REFERENCES repos(key_ref) ON DELETE CASCADE,
  PRIMARY KEY (tracker_key, repo_key));
CREATE TABLE IF NOT EXISTS worktrees (
  ref_key TEXT PRIMARY KEY NOT NULL, path TEXT UNIQUE NOT NULL,
  branch TEXT UNIQUE NOT NULL, repo_key TEXT REFERENCES repos(key_ref) ON DELETE CASCADE,
  issue_url TEXT NOT NULL DEFAULT '', pr_url TEXT NOT NULL DEFAULT '',
  added_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS pr_cache (
  branch TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL, checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY NOT NULL, worktree_ref TEXT NOT NULL REFERENCES worktrees(ref_key) ON DELETE CASCADE,
  state TEXT NOT NULL, runtime_name TEXT NOT NULL DEFAULT '',
  initiator_command TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  command TEXT NOT NULL, args TEXT NOT NULL,
  exit_code INTEGER, created_at TEXT NOT NULL,
  output TEXT NOT NULL DEFAULT '', truncated INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS issue_cache (
  source TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


#: Allowed values for trackers.vendor — an enum, never free text.
VENDORS = ("jira", "github")

#: Legacy vendor values that migrate forward to an enum member.
_VENDOR_REMAP = {"gitlab": "github"}


def normalize_vendor(vendor: str) -> str:
    """Validate *vendor* against the VENDORS enum; '' stays '' (derive later).

    Raises ValueError (→ HarnessError at the CLI boundary) for anything
    outside the enum.
    """
    v = (vendor or "").strip().lower()
    if not v:
        return ""
    if v in VENDORS:
        return v
    if v in _VENDOR_REMAP:
        return _VENDOR_REMAP[v]
    raise ValueError(f"vendor must be one of {', '.join(VENDORS)} (got {vendor!r})")


def _tracker_meta(tid: str) -> tuple[str, str]:
    """(vendor, remote_url) for *tid*; both mandatory, never ''.

    jira:PREFIX → ("jira", "<jira-site>/browse/PREFIX" or "jira:PREFIX").
    github:O/R → ("github", "https://github.com/O/R").
    gitlab:host/g/r → ("github", "https://host/g/r") — gitlab hosts are
    served by glab under the github-compatible vendor enum member.
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
        return "github", f"https://{rest}" if "/" in rest and host else tid
    return "github", tid

def _migrate_runs_output(conn) -> None:
    """Schema v3: runs gains output/truncated (persisted run log).

    ALTER TABLE is enough (both columns carry defaults, so old rows read
    back as empty/non-truncated). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if not cols:
        return
    if "output" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN output TEXT NOT NULL DEFAULT ''")
    if "truncated" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN truncated INTEGER NOT NULL DEFAULT 0")

def _migrate_v2(conn) -> None:
    """Schema v2: trackers gains mandatory vendor/remote_url; repos loses tracker_key.

    - Legacy trackers rows (nullable/'' vendor/remote_url) are backfilled
      from the key via _tracker_meta.
    - Legacy repos.tracker_key values seed missing tracker rows + links,
      then the column is dropped (SQLite ≥3.35 DROP COLUMN).
    - Every required column ends NOT NULL with NO DEFAULT: legacy tables
      carrying ``DEFAULT ''`` (or NULLable required columns) are rebuilt
      from the canonical DDL after their data is backfilled with real
      values. Idempotent: re-runs are no-ops.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    tcols = {r[1]: r for r in conn.execute("PRAGMA table_info(trackers)")}
    if tcols:
        if "vendor" not in tcols:
            conn.execute("ALTER TABLE trackers ADD COLUMN vendor TEXT NOT NULL DEFAULT 'github'")
        if "remote_url" not in tcols:
            conn.execute("ALTER TABLE trackers ADD COLUMN remote_url TEXT NOT NULL DEFAULT ''")
        for row in conn.execute("SELECT key_ref, vendor, remote_url FROM trackers"):
            tid, vendor, remote_url = row[0], row[1], row[2]
            v, u = _tracker_meta(tid)
            if not vendor or vendor not in VENDORS:
                vendor = v
            if not remote_url:
                remote_url = u
            conn.execute("UPDATE trackers SET vendor = ?, remote_url = ? WHERE key_ref = ?",
                         (vendor, remote_url, tid))
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
    # Backfill remaining required columns with real data before the
    # rebuild pass copies the rows into canonical-shaped tables.
    for row in conn.execute("SELECT key_ref, path FROM repos"
                            " WHERE name IS NULL OR name = ''").fetchall():
        key, p = row[0], row[1]
        name = p.rsplit("/", 1)[-1] if p else key
        conn.execute("UPDATE repos SET name = ? WHERE key_ref = ?", (name or key, key))
    conn.execute("UPDATE worktrees SET added_at = ? WHERE added_at IS NULL OR added_at = ''",
                 (now,))
    conn.execute("UPDATE pr_cache SET checked_at = ? WHERE checked_at IS NULL OR checked_at = ''",
                 (now,))
    conn.execute("UPDATE pr_cache SET payload = '{}' WHERE payload IS NULL OR payload = ''")
    _rebuild_required_columns(conn)


# Required (NOT NULL, no DEFAULT) columns per table — the code always
# fills these with real data; optional columns keep their DEFAULT.
_REQUIRED_COLS = {
    "trackers": ("key_ref", "remote_url", "vendor"),
    "repos": ("key_ref", "path", "name"),
    "tracker_repos": ("tracker_key", "repo_key"),
    "worktrees": ("ref_key", "path", "branch", "added_at"),
    "pr_cache": ("branch", "payload", "checked_at"),
    "sessions": ("id", "worktree_ref", "state", "created_at"),
    "runs": ("session_id", "command", "args", "created_at"),
    "issue_cache": ("source", "payload", "fetched_at"),
}


def _canonical_ddl() -> dict[str, str]:
    """{table: CREATE TABLE …} from SCHEMA (single source of truth)."""
    ddl: dict[str, str] = {}
    for stmt in SCHEMA.split(";"):
        s = stmt.strip()
        if s.startswith("CREATE TABLE IF NOT EXISTS "):
            ddl[s.split()[5]] = s.replace("CREATE TABLE IF NOT EXISTS ",
                                          "CREATE TABLE ", 1)
    return ddl


def _rebuild_required_columns(conn) -> list[str]:
    """Rebuild tables whose required columns are NULLable or DEFAULTed.

    SQLite cannot drop a column DEFAULT in place, so a deviating table is
    recreated from the canonical DDL and its rows copied. Runs with the
    connection's transaction committed and foreign_keys off (children
    referencing a rebuilt parent survive the drop/rename window).
    Returns the rebuilt table names.
    """
    if conn.in_transaction:
        conn.commit()
    was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if was_on:
        conn.execute("PRAGMA foreign_keys = OFF")
    rebuilt: list[str] = []
    try:
        for table, ddl in _canonical_ddl().items():
            info = list(conn.execute(f"PRAGMA table_info({table})"))
            if not info:
                continue
            by_name = {r[1]: r for r in info}
            bad = any(c not in by_name or by_name[c][3] != 1 or by_name[c][4] is not None
                      for c in _REQUIRED_COLS.get(table, ()))
            if not bad:
                continue
            tmp = f"{table}_rebuild"
            conn.execute(f'DROP TABLE IF EXISTS "{tmp}"')
            conn.execute(ddl.replace(f"CREATE TABLE {table}",
                                     f'CREATE TABLE "{tmp}"', 1))
            new_cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{tmp}")')}
            cols = ", ".join(f'"{r[1]}"' for r in info if r[1] in new_cols)
            conn.execute(f'INSERT INTO "{tmp}" ({cols}) SELECT {cols} FROM "{table}"')
            conn.execute(f'DROP TABLE "{table}"')
            conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{table}"')
            rebuilt.append(table)
        if conn.in_transaction:
            conn.commit()
    finally:
        if was_on:
            conn.execute("PRAGMA foreign_keys = ON")
    if "runs" in rebuilt:
        # AUTOINCREMENT sequence: keep it at/above the copied max(id).
        conn.execute("UPDATE sqlite_sequence SET seq ="
                     " (SELECT COALESCE(MAX(id), 0) FROM runs) WHERE name = 'runs'")
        conn.commit()
    return rebuilt


def init_db(path: Path) -> Path:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate_v2(conn)
        _migrate_runs_output(conn)
    return path
def _norm(p: str) -> str:
    from pathlib import Path as _P
    return str(_P(p).expanduser().resolve()) if p else ""


from .store_trackers import (  # noqa: F401,E402
    add_tracker_repo,
    delete_tracker,
    ensure_tracker,
    load_repos,
    load_tracker_rows,
    load_trackers,
    register_repo_row,
    register_repo_row_unlinked,
    remove_repo_row,
    remove_tracker_repo,
    upsert_tracker,
    worktree_count_for_repo,
)

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
        for tid, entry in cfg_trackers.items():
            v, u = _tracker_meta(tid)
            stored = str((entry or {}).get("remote_url", "") or u)
            counts["trackers"] += conn.execute(
                "INSERT OR IGNORE INTO trackers (key_ref, vendor, remote_url) VALUES (?, ?, ?)",
                (tid, str((entry or {}).get("vendor", "") or v), stored)).rowcount
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
            entry_u = str((cfg_trackers.get(tid) or {}).get("remote_url", "") or um)
            conn.execute("INSERT OR IGNORE INTO trackers (key_ref, vendor, remote_url)"
                         " VALUES (?, ?, ?)", (tid, vm, entry_u))
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


from .store_sessions import (  # noqa: F401,E402
    db_path,
    finish_session,
    gen_session_id,
    get_session,
    insert_run,
    insert_session,
    list_runs,
    list_sessions,
    session_file_path,
)

from .store_links import load_links_rows, save_links_rows  # noqa: F401,E402

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
                 str(e.get("pr_url", "")), str(e.get("added_at")
                 or datetime.now(timezone.utc).isoformat()),
                 json.dumps(extra)))
            counts["worktrees"] += 1
        for branch, entry in pr_cache.items():
            full = dict(entry)
            full.setdefault("pr", None)
            checked = str(full.get("checked_at")
                          or datetime.now(timezone.utc).isoformat())
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
