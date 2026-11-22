"""SQLite state: trackers, repos, worktrees, pr_cache, runs.

Stdlib sqlite3 repository (no ORM — storage contract in
docs/superpowers/specs/2026-11-15-remove-session-entity-design.md; the
sessions table it removes is described in the archived 2026-09-26
session-type design). One
state.db in config_dir() holds everything after the one-shot
JSON→SQLite migration (migrate_json below).
"""
from __future__ import annotations

import json
import random
import shutil
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trackers (
  key_ref TEXT PRIMARY KEY NOT NULL, remote_url TEXT NOT NULL, vendor TEXT NOT NULL,
  fetch_enabled INTEGER NOT NULL DEFAULT 1, is_enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS repos (
  key_ref TEXT PRIMARY KEY NOT NULL, path TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  remote TEXT NOT NULL DEFAULT '', tool TEXT NOT NULL DEFAULT '',
  fetch_enabled INTEGER NOT NULL DEFAULT 1,
  default_branch TEXT NOT NULL DEFAULT '', release_branch TEXT NOT NULL DEFAULT '',
  commands TEXT NOT NULL DEFAULT '{}',
  overlay_buttons TEXT NOT NULL DEFAULT '{}',
  auto_fix_ci INTEGER NOT NULL DEFAULT 0,
  auto_fix_reviews INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tracker_repos (
  tracker_key TEXT NOT NULL REFERENCES trackers(key_ref) ON DELETE CASCADE,
  repo_key TEXT NOT NULL REFERENCES repos(key_ref) ON DELETE CASCADE,
  PRIMARY KEY (tracker_key, repo_key));
CREATE TABLE IF NOT EXISTS worktrees (
  ref_key TEXT PRIMARY KEY NOT NULL, path TEXT UNIQUE NOT NULL,
  branch TEXT NOT NULL, repo_key TEXT REFERENCES repos(key_ref) ON DELETE CASCADE,
  issue_url TEXT NOT NULL DEFAULT '', pr_url TEXT NOT NULL DEFAULT '',
  added_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
  active INTEGER NOT NULL DEFAULT 1,
  deleted_at TEXT NOT NULL DEFAULT '',
  related_repos TEXT NOT NULL DEFAULT '[]',
  parent_key_ref TEXT NOT NULL DEFAULT '',
  UNIQUE (repo_key, branch));
CREATE TABLE IF NOT EXISTS pr_cache (
  branch TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL, checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command TEXT NOT NULL, args TEXT NOT NULL,
  exit_code INTEGER, created_at TEXT NOT NULL,
  output TEXT NOT NULL DEFAULT '', truncated INTEGER NOT NULL DEFAULT 0,
  validation TEXT NOT NULL DEFAULT '{}',
  flow_id INTEGER REFERENCES flows(id) ON DELETE SET NULL,
  worktree_ref TEXT REFERENCES worktrees(ref_key) ON DELETE CASCADE,
  harness_name TEXT NOT NULL DEFAULT '',
  initiator_command TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL DEFAULT '',
  session_type TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'finished',
  continued_from_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  parent_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  step_index INTEGER,
  finished_at TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER,
  model_id TEXT NOT NULL DEFAULT '',
  effort TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER,
  output_tokens INTEGER,
  cached_tokens INTEGER,
  cost_usd REAL);
CREATE TABLE IF NOT EXISTS issue_cache (
  source TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pr_candidates_cache (
  source TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS release_diff_cache (
  repo_key TEXT PRIMARY KEY NOT NULL, count INTEGER NOT NULL,
  fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS custom_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL, icon TEXT NOT NULL DEFAULT 'Zap',
  prompt TEXT NOT NULL DEFAULT '', pre_execution TEXT NOT NULL DEFAULT '',
  post_execution TEXT NOT NULL DEFAULT '',
  default_options TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
  protected INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0,
  dedicated_top_bar INTEGER NOT NULL DEFAULT 0,
  dedicated_overlay INTEGER NOT NULL DEFAULT 0,
  run_immediately INTEGER NOT NULL DEFAULT 0,
  category TEXT NOT NULL DEFAULT 'worktree');
CREATE TABLE IF NOT EXISTS flow_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL, icon TEXT NOT NULL DEFAULT 'Zap',
  steps TEXT NOT NULL DEFAULT '[]',
  pre_execution TEXT NOT NULL DEFAULT '',
  post_execution TEXT NOT NULL DEFAULT '',
  parent_labels TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
  protected INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0,
  dedicated_top_bar INTEGER NOT NULL DEFAULT 0,
  dedicated_overlay INTEGER NOT NULL DEFAULT 0,
  run_immediately INTEGER NOT NULL DEFAULT 0,
  category TEXT NOT NULL DEFAULT 'worktree');
CREATE TABLE IF NOT EXISTS flows (
  id INTEGER PRIMARY KEY AUTOINCREMENT, template_label TEXT NOT NULL,
  worktree_ref TEXT NOT NULL DEFAULT '',
  repo_snapshot TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  current_step INTEGER NOT NULL DEFAULT 0,
  step_results TEXT NOT NULL DEFAULT '[]',
  effective_steps TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, modified_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_flows_worktree ON flows(worktree_ref);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL,
  thread_id TEXT NOT NULL DEFAULT '', app_name TEXT NOT NULL DEFAULT 'mattermost',
  sender_username TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL DEFAULT '', repos TEXT NOT NULL DEFAULT '[]',
  related_repos TEXT NOT NULL DEFAULT '[]',
  worktrees TEXT NOT NULL DEFAULT '[]',
  added_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS prodtest_items (
   issue_key TEXT PRIMARY KEY NOT NULL, plan_path TEXT NOT NULL DEFAULT '',
   plan_revision INTEGER NOT NULL DEFAULT 0, plan_risk TEXT NOT NULL DEFAULT '',
   plan_diff_hash TEXT NOT NULL DEFAULT '', release_sha TEXT NOT NULL DEFAULT '',
   released_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
   last_run_id TEXT NOT NULL DEFAULT '', worktree_ref TEXT NOT NULL DEFAULT '',
   run_results TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pr_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worktree_ref TEXT NOT NULL REFERENCES worktrees(ref_key) ON DELETE CASCADE,
  name TEXT NOT NULL,
  commit_sha TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  conclusion TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  log_tail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_pr_checks_worktree ON pr_checks(worktree_ref);
CREATE TABLE IF NOT EXISTS pr_review_threads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worktree_ref TEXT NOT NULL REFERENCES worktrees(ref_key) ON DELETE CASCADE,
  thread_id TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  file TEXT NOT NULL DEFAULT '',
  line INTEGER,
  body TEXT NOT NULL DEFAULT '',
  resolved INTEGER NOT NULL DEFAULT 0,
  is_bot INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE (worktree_ref, thread_id));
CREATE INDEX IF NOT EXISTS idx_pr_review_threads_worktree ON pr_review_threads(worktree_ref);
CREATE TABLE IF NOT EXISTS worktree_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worktree_ref TEXT NOT NULL REFERENCES worktrees(ref_key) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_worktree_events_worktree ON worktree_events(worktree_ref, created_at);
CREATE TABLE IF NOT EXISTS change_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL,
  row_key TEXT NOT NULL,
  op TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  cron_expr TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  target_kind TEXT NOT NULL,
  target_label TEXT NOT NULL,
  args_json TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT NOT NULL DEFAULT '',
  next_run_at TEXT NOT NULL DEFAULT '',
  last_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  lease_until TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  modified_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled, next_run_at);
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


def _migrate_runs_validation(conn) -> None:
    """Schema v17: runs gains validation (per-attempt validate_headless trace).
    ALTER TABLE is enough (NOT NULL DEFAULT '{}', so old rows read back as
    no validation data). Idempotent: skipped once the column exists.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if not cols:
        return
    if "validation" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN validation TEXT NOT NULL DEFAULT '{}'")


def _migrate_runs_parent(conn) -> None:
    """Schema v27: runs gains parent_run_id and step_index (run hierarchy).

    Both columns are nullable, so ALTER TABLE is enough and idempotent.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if not cols:
        return
    if "parent_run_id" not in cols:
        conn.execute(
            "ALTER TABLE runs ADD COLUMN parent_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL")
    if "step_index" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN step_index INTEGER")


def _migrate_runs_telemetry(conn) -> None:
    """Schema v28: runs gains finished_at, duration_ms, model_id, effort,
    input_tokens, output_tokens, cached_tokens, and cost_usd (run telemetry).

    All columns are nullable or have safe defaults, so ALTER TABLE is idempotent.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if not cols:
        return
    for name, decl in (
        ("finished_at", "TEXT NOT NULL DEFAULT ''"),
        ("duration_ms", "INTEGER"),
        ("model_id", "TEXT NOT NULL DEFAULT ''"),
        ("effort", "TEXT NOT NULL DEFAULT ''"),
        ("input_tokens", "INTEGER"),
        ("output_tokens", "INTEGER"),
        ("cached_tokens", "INTEGER"),
        ("cost_usd", "REAL"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")


def _migrate_display_cols(conn) -> None:
    """Schema v18: flow_templates/custom_actions gain protected/sort_order/
    dedicated_top_bar/dedicated_overlay (management-table display flags).

    ALTER TABLE backfills DEFAULT 0; sort_order backfills to id so existing
    rows keep creation order. Idempotent: skipped when present.
    """
    for table in ("flow_templates", "custom_actions"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        if "protected" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
        if "sort_order" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        if "dedicated_top_bar" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN dedicated_top_bar INTEGER NOT NULL DEFAULT 0")
        if "dedicated_overlay" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN dedicated_overlay INTEGER NOT NULL DEFAULT 0")
        conn.execute(f"UPDATE {table} SET sort_order = id WHERE sort_order = 0")
def _migrate_messages(conn) -> None:
    """Schema v19: messages table (registered chat messages).

    CREATE TABLE IF NOT EXISTS is enough (fresh DBs get it from SCHEMA).
    Idempotent: no-op when present.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS messages ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL, "
                 "thread_id TEXT NOT NULL DEFAULT '', app_name TEXT NOT NULL DEFAULT 'mattermost', "
                 "sender_username TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', "
                 "ts TEXT NOT NULL DEFAULT '', repos TEXT NOT NULL DEFAULT '[]', "
                 "worktrees TEXT NOT NULL DEFAULT '[]', "
                 "added_at TEXT NOT NULL)")


def _migrate_messages_worktrees(conn) -> None:
    """Schema v21: messages gains worktrees (JSON worktree ref_key list).

    Mirrors the repos column for issue #46: ALTER TABLE is enough
    (NOT NULL DEFAULT '[]', so old rows read back as unlinked).
    Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if not cols:
        return
    if "worktrees" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN worktrees TEXT NOT NULL DEFAULT '[]'")


def _migrate_messages_related_repos(conn) -> None:
    """Schema v22: messages gains related_repos (JSON repo-key list).

    Start-pattern counterpart for messages (issue #46): the message's own
    repos[] stays the launch scope; related_repos are extra checkouts whose
    paths are appended to the agent prompt. ALTER TABLE is enough
    (NOT NULL DEFAULT '[]'). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if not cols:
        return
    if "related_repos" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN related_repos TEXT NOT NULL DEFAULT '[]'")

def _migrate_prodtest_cols(conn) -> None:
    """Schema v25: prodtest_items gains worktree_ref + run_results (issue #50).

    ALTER TABLE is enough (both columns carry defaults, so old rows read
    back as unlinked/no-results). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(prodtest_items)")}
    if not cols:
        return
    if "worktree_ref" not in cols:
        conn.execute("ALTER TABLE prodtest_items ADD COLUMN worktree_ref TEXT NOT NULL DEFAULT ''")
    if "run_results" not in cols:
        conn.execute("ALTER TABLE prodtest_items ADD COLUMN run_results TEXT NOT NULL DEFAULT '{}'")


CHANGE_LOG_MAX_ROWS = 5000

#: Tables tracked by SQLite CDC triggers: (table_name, row_key_column).
CDC_TABLES: tuple[tuple[str, str], ...] = (
    ("worktrees", "ref_key"),
    ("runs", "id"),
    ("flows", "id"),
    ("messages", "message_id"),
    ("pr_cache", "branch"),
    ("prodtest_items", "issue_key"),
    ("schedules", "id"),
)

_CDC_TRIGGER_NAMES: frozenset[str] = frozenset(
    f"trg_cdc_{table}_{op}"
    for table, _ in CDC_TABLES
    for op in ("insert", "update", "delete")
)


def _migrate_schedules(conn) -> None:
    """Schema v29: recurring schedules table for flows, actions, and commands."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schedules ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT UNIQUE NOT NULL, "
        "cron_expr TEXT NOT NULL, "
        "timezone TEXT NOT NULL DEFAULT 'UTC', "
        "target_kind TEXT NOT NULL, "
        "target_label TEXT NOT NULL, "
        "args_json TEXT NOT NULL DEFAULT '{}', "
        "enabled INTEGER NOT NULL DEFAULT 1, "
        "last_run_at TEXT NOT NULL DEFAULT '', "
        "next_run_at TEXT NOT NULL DEFAULT '', "
        "last_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL, "
        "lease_until TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, "
        "modified_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled, next_run_at)"
    )


def _migrate_change_log(conn) -> None:
    """Schema v27: bounded change_log table + AFTER INSERT/UPDATE/DELETE triggers.

    Idempotent: uses CREATE TABLE/TRIGGER IF NOT EXISTS. Must run after any
    table rebuild (e.g. _migrate_runs_worktree_fk) so triggers attach to the
    final table definition.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS change_log ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "table_name TEXT NOT NULL, "
        "row_key TEXT NOT NULL, "
        "op TEXT NOT NULL, "
        "created_at REAL NOT NULL)"
    )
    ts_expr = "(julianday('now') - 2440587.5) * 86400.0"
    for table, pk_col in CDC_TABLES:
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS trg_cdc_{table}_insert "
            f"AFTER INSERT ON {table} BEGIN "
            f"INSERT INTO change_log (table_name, row_key, op, created_at) "
            f"VALUES ('{table}', CAST(NEW.{pk_col} AS TEXT), 'insert', {ts_expr}); "
            f"END"
        )
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS trg_cdc_{table}_update "
            f"AFTER UPDATE ON {table} BEGIN "
            f"INSERT INTO change_log (table_name, row_key, op, created_at) "
            f"VALUES ('{table}', CAST(NEW.{pk_col} AS TEXT), 'update', {ts_expr}); "
            f"END"
        )
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS trg_cdc_{table}_delete "
            f"AFTER DELETE ON {table} BEGIN "
            f"INSERT INTO change_log (table_name, row_key, op, created_at) "
            f"VALUES ('{table}', CAST(OLD.{pk_col} AS TEXT), 'delete', {ts_expr}); "
            f"END"
        )


def prune_change_log(path: Path, max_rows: int = CHANGE_LOG_MAX_ROWS) -> int:
    """Prune oldest change_log rows so at most *max_rows* remain. Returns deleted count."""
    if not path.exists() or max_rows < 1:
        return 0
    with connect(path) as conn:
        cur = conn.execute(
            "DELETE FROM change_log WHERE seq < ("
            "SELECT seq FROM change_log ORDER BY seq DESC LIMIT 1 OFFSET ?"
            ")",
            (max_rows - 1,),
        )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def latest_change_seq(path: Path) -> int:
    """Highest seq currently in change_log (0 when empty or absent)."""
    if not path.exists():
        return 0
    with connect(path) as conn:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM change_log").fetchone()
        return int(row[0]) if row and row[0] is not None else 0


def read_change_log(path: Path, after_seq: int = 0, limit: int = 500) -> list[dict]:
    """Return change_log rows with seq > *after_seq* ordered ascending."""
    if not path.exists():
        return []
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT seq, table_name, row_key, op, created_at "
            "FROM change_log WHERE seq > ? ORDER BY seq ASC LIMIT ?",
            (int(after_seq), int(limit)),
        ).fetchall()
    return [
        {
            "seq": int(r[0]),
            "table": str(r[1]),
            "row_key": str(r[2]),
            "op": str(r[3]),
            "created_at": float(r[4]),
        }
        for r in rows
    ]


def _migrate_runs_worktree_fk(conn) -> None:
    """Schema v26: runs.worktree_ref → nullable FK to worktrees(ref_key).

    Repo-scoped runs (pre-link attempts, web runs) carry no worktree: their
    '' sentinel becomes NULL, the FK's "no parent" value, and ON DELETE
    CASCADE replaces the explicit runs purge in every worktree teardown.
    SQLite cannot add a FK in place, so the table is rebuilt from the
    canonical DDL with foreign_keys off. Historical dangling refs are left
    to store_links.purge_orphans (it snapshots their transcripts before
    dropping the rows). Idempotent: skipped once the cascade FK and the
    nullable column are present *and* no '' sentinel rows remain — the v16
    fold and `_rebuild_required_columns` rebuild `runs` from the canonical
    DDL with foreign_keys off, so they arrive at the v26 shape while still
    carrying folded '' rows.
    """
    info = {r[1]: r for r in conn.execute("PRAGMA table_info(runs)")}
    if not info:
        return
    nullable = "worktree_ref" in info and info["worktree_ref"][3] == 0
    if nullable:
        # Normalize before any early return: a shape-complete v26 table can
        # still hold '' rows left by a fold/rebuild (a no-op on a clean DB).
        conn.execute("UPDATE runs SET worktree_ref = NULL WHERE worktree_ref = ''")
    if list(conn.execute("PRAGMA table_info(sessions)")):
        # pre-v16: the fold rebuilds runs from the same canonical DDL.
        return
    fk = next((r for r in conn.execute("PRAGMA foreign_key_list(runs)")
               if r[2] == "worktrees" and (r[3] or "") == "worktree_ref"), None)
    if fk is not None and (fk[6] or "").upper() == "CASCADE" and nullable:
        return
    if conn.in_transaction:
        conn.commit()
    was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if was_on:
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        prior_seq = _runs_seq(conn)
        ddl = _canonical_ddl()["runs"]
        conn.execute('DROP TABLE IF EXISTS "runs_rebuild"')
        conn.execute(ddl.replace("CREATE TABLE runs", 'CREATE TABLE "runs_rebuild"', 1))
        new_cols = {r[1] for r in conn.execute('PRAGMA table_info("runs_rebuild")')}
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")
                    if r[1] in new_cols]
        names = ", ".join(f'"{c}"' for c in old_cols)
        conn.execute(f'INSERT INTO "runs_rebuild" ({names}) SELECT {names} FROM runs')
        conn.execute("DROP TABLE runs")
        conn.execute('ALTER TABLE "runs_rebuild" RENAME TO runs')
        _restore_runs_seq(conn, prior_seq)
        conn.execute("UPDATE runs SET worktree_ref = NULL WHERE worktree_ref = ''")
        if conn.in_transaction:
            conn.commit()
    finally:
        if was_on:
            conn.execute("PRAGMA foreign_keys = ON")


def _migrate_category(conn) -> None:
    """Schema v19: custom_actions/flow_templates gain category.

    ALTER TABLE backfills DEFAULT 'worktree' so existing rows keep worktree
    scoping. Idempotent: skipped when present.
    """
    for table in ("flow_templates", "custom_actions"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        if "category" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN category TEXT NOT NULL DEFAULT 'worktree'")


def _migrate_flow_template_icon(conn) -> None:
    """Schema v24: flow_templates gains icon (Lucide name, default Zap).

    Mirrors custom_actions.icon for issue #62: ALTER TABLE backfills
    DEFAULT 'Zap' so existing templates keep the fallback icon.
    Idempotent: skipped when present.
    """
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(flow_templates)")}
    if tcols and "icon" not in tcols:
        conn.execute("ALTER TABLE flow_templates ADD COLUMN icon TEXT NOT NULL DEFAULT 'Zap'")


def _migrate_run_immediately(conn) -> None:
    """Schema v20: custom_actions/flow_templates gain run_immediately.

    ALTER TABLE backfills DEFAULT 0 so existing rows keep form-first runs.
    Idempotent: skipped when present.
    """
    for table in ("flow_templates", "custom_actions"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        if "run_immediately" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN run_immediately INTEGER NOT NULL DEFAULT 0")



def _migrate_flow_template_lineage(conn) -> None:
    """Schema v16: flow_templates gains parent_labels; flows gains effective_steps.

    ALTER TABLE is enough (both columns carry defaults, so old rows read
    back as []/NULL). Idempotent: skipped when present.
    """
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(flow_templates)")}
    if tcols and "parent_labels" not in tcols:
        conn.execute("ALTER TABLE flow_templates ADD COLUMN parent_labels TEXT NOT NULL DEFAULT '[]'")
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(flows)")}
    if fcols and "effective_steps" not in fcols:
        conn.execute("ALTER TABLE flows ADD COLUMN effective_steps TEXT NOT NULL DEFAULT ''")

def _migrate_flow_linkage(conn) -> None:
    """Schema v15: runs gains flow_id (sessions carried it pre-fold).

    flow_id is the query path (metadata.flow_id stays as history).
    Idempotent: ADD COLUMN / backfill skip what is already present. The
    sessions table is folded into runs and dropped by
    migrate_sessions_into_runs (v16), so nothing rebuilds it here.
    """
    import json as _json
    info = list(conn.execute("PRAGMA table_info(sessions)"))
    if not info:
        return
    cols = {r[1] for r in info}
    if "flow_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN flow_id INTEGER REFERENCES flows(id) ON DELETE SET NULL")
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if rcols and "flow_id" not in rcols:
        conn.execute("ALTER TABLE runs ADD COLUMN flow_id INTEGER REFERENCES flows(id) ON DELETE SET NULL")

    # Backfill flow_id from metadata.flow_id (integer ids of live flows only).
    try:
        live = {r[0] for r in conn.execute("SELECT id FROM flows")}
    except Exception:
        live = set()
    for sid, meta in conn.execute("SELECT id, metadata FROM sessions"):
        try:
            m = _json.loads(meta or "{}")
        except Exception:
            continue
        f = m.get("flow_id") if isinstance(m, dict) else None
        if isinstance(f, int) and f in live:
            conn.execute("UPDATE sessions SET flow_id = ? WHERE id = ? AND flow_id IS NULL", (f, sid))
    try:
        conn.execute("UPDATE runs SET flow_id = (SELECT flow_id FROM sessions WHERE sessions.id = runs.session_id)"
                     " WHERE flow_id IS NULL")
    except Exception:
        pass

def _migrate_sessions_harness(conn) -> None:
    """Schema v5: sessions.runtime_name → harness_name (the runtime→harness rename).
    Code writes ``harness_name`` but pre-rename DBs still carry
    ``runtime_name`` — every insert_session then fails with ``no such
    column`` and the run mirror silently skips (no session row, no runs
    row). Idempotent: skipped when harness_name exists; renames in
    place so existing session history survives.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if not cols or "harness_name" in cols:
        return
    if "runtime_name" in cols:
        conn.execute("ALTER TABLE sessions RENAME COLUMN runtime_name TO harness_name")
    else:
        conn.execute("ALTER TABLE sessions ADD COLUMN harness_name TEXT NOT NULL DEFAULT ''")

def _migrate_worktrees_branch_scope(conn) -> None:
    """Schema v7: worktrees.branch UNIQUE → composite UNIQUE (repo_key, branch).

    Two repos can carry the same branch name (e.g. feat/IPG-1002--x in
    projectx and ipg-commons) — branch is unique *within* a repo, not
    globally. Rebuilds the table from the canonical DDL when the legacy
    bare-branch UNIQUE index is still present. Idempotent: skipped once
    the composite index exists and no bare-branch UNIQUE remains.
    """
    info = list(conn.execute("PRAGMA table_info(worktrees)"))
    if not info:
        return
    idx = list(conn.execute("PRAGMA index_list(worktrees)"))
    cols_of: dict[str, set] = {}
    for entry in idx:
        name = entry[1]
        try:
            cols_of[name] = {row[2] for row in conn.execute(f'PRAGMA index_info("{name}")')}
        except Exception:
            continue
    has_composite = any(cols == {"repo_key", "branch"} for cols in cols_of.values())
    bare_branch_unique = any(
        bool(entry[2]) and cols_of.get(entry[1]) == {"branch"} for entry in idx)
    if has_composite and not bare_branch_unique:
        return
    if conn.in_transaction:
        conn.commit()
    was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if was_on:
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        ddl = _canonical_ddl()["worktrees"]
        conn.execute('DROP TABLE IF EXISTS "worktrees_rebuild"')
        conn.execute(ddl.replace("CREATE TABLE worktrees", 'CREATE TABLE "worktrees_rebuild"', 1))
        new_cols = {r[1] for r in conn.execute('PRAGMA table_info("worktrees_rebuild")')}
        old_cols = [r[1] for r in info if r[1] in new_cols]
        cols = ", ".join(f'"{c}"' for c in old_cols)
        conn.execute(f'INSERT INTO "worktrees_rebuild" ({cols}) SELECT {cols} FROM "worktrees"')
        conn.execute('DROP TABLE "worktrees"')
        conn.execute('ALTER TABLE "worktrees_rebuild" RENAME TO "worktrees"')
        if conn.in_transaction:
            conn.commit()
    finally:
        if was_on:
            conn.execute("PRAGMA foreign_keys = ON")

def _migrate_worktrees_active(conn) -> None:
    """Schema v4: worktrees gains active (1 = active, 0 = deactivated).

    ALTER TABLE is enough (NOT NULL DEFAULT 1, so old rows read back as
    active). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
    if not cols:
        return
    if "active" not in cols:
        conn.execute("ALTER TABLE worktrees ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
def _migrate_worktrees_deleted_at(conn) -> None:
    """Schema v12: worktrees gains deleted_at ('' = live, ISO ts = soft-deleted).

    ALTER TABLE is enough (NOT NULL DEFAULT '', so old rows read back as
    live). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
    if not cols:
        return
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE worktrees ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''")


def _migrate_worktrees_related(conn) -> None:
    """Worktrees gains related_repos (JSON repo-key list, parent rows) and
    parent_key_ref (child → parent ref_key, '' = top-level).

    ALTER TABLE is enough (NOT NULL DEFAULTs, so old rows read back as
    top-level childless). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
    if not cols:
        return
    if "related_repos" not in cols:
        conn.execute("ALTER TABLE worktrees ADD COLUMN related_repos TEXT NOT NULL DEFAULT '[]'")
    if "parent_key_ref" not in cols:
        conn.execute("ALTER TABLE worktrees ADD COLUMN parent_key_ref TEXT NOT NULL DEFAULT ''")


def _default_fetch_enabled(tid: str = "", tool: str = "") -> int:
    """Default fetch gate: GitHub off (slow/rate-limited, opt-in), else on.

    GitLab-backed rows stay on: gitlab: tracker ids and glab-tooled repos
    are detected explicitly because they share vendor='github'.
    """
    if (tool or "").strip().lower() == "gh":
        return 0
    t = (tid or "").strip().lower()
    if t.startswith("github:"):
        return 0
    return 1

def _migrate_fetch_enabled(conn) -> None:
    """Schema v8: trackers/repos gain fetch_enabled (1 = fetch, 0 = skip).

    ALTER TABLE backfills new rows via DEFAULT 1; existing rows take the
    vendor-aware default (GitHub off, Jira/GitLab on). Idempotent.
    """
    for table in ("trackers", "repos"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols or "fetch_enabled" in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN fetch_enabled INTEGER NOT NULL DEFAULT 1")
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(trackers)")}
    if tcols and "fetch_enabled" in tcols:
        for (tid,) in conn.execute("SELECT key_ref FROM trackers WHERE fetch_enabled = 1"):
            if _default_fetch_enabled(tid=tid) == 0:
                conn.execute("UPDATE trackers SET fetch_enabled = 0 WHERE key_ref = ?", (tid,))
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if rcols and "fetch_enabled" in rcols:
        for key_ref, tool in conn.execute("SELECT key_ref, tool FROM repos WHERE fetch_enabled = 1"):
            if _default_fetch_enabled(tool=tool or "") == 0:
                conn.execute("UPDATE repos SET fetch_enabled = 0 WHERE key_ref = ?", (key_ref,))

def _migrate_tracker_enabled(conn) -> None:
    """Schema v9: trackers gain is_enabled (1 = active, 0 = disabled).

    ALTER TABLE backfills DEFAULT 1 — every existing tracker stays enabled.
    Disabled trackers are skipped by the issue/PR fetch gates and by the
    domain blocklist below. Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trackers)")}
    if not cols or "is_enabled" in cols:
        return
    conn.execute("ALTER TABLE trackers ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1")

def _migrate_repos_release(conn) -> None:
    """Schema v10: repos gain default_branch/release_branch ('' = unset).

    ALTER TABLE backfills DEFAULT '' — existing rows get empty branches
    until `backfill_release_branches` seeds them. Idempotent: skipped
    when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if not cols:
        return
    if "default_branch" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN default_branch TEXT NOT NULL DEFAULT ''")
    if "release_branch" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN release_branch TEXT NOT NULL DEFAULT ''")


def _migrate_repos_commands(conn) -> None:
    """Schema v11: repos gain commands JSON object ('{}' = no gates).

    ALTER TABLE backfills DEFAULT '{}' — existing rows get no gate.
    Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if not cols:
        return
    if "commands" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN commands TEXT NOT NULL DEFAULT '{}'")
def _migrate_repos_overlay_buttons(conn) -> None:
    """Schema v23: repos gain overlay_buttons JSON object ('{}' = all visible).

    Per-repo StatusTable overlay visibility: {button_id: bool}. Missing key
    or '{}' = every button visible (backward compatible). Idempotent:
    skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if not cols:
        return
    if "overlay_buttons" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN overlay_buttons TEXT NOT NULL DEFAULT '{}'")


def _migrate_repos_auto_fix(conn) -> None:
    """Add opt-in auto_fix_ci and auto_fix_reviews columns to repos (issue #154).

    Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if not cols:
        return
    if "auto_fix_ci" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN auto_fix_ci INTEGER NOT NULL DEFAULT 0")
    if "auto_fix_reviews" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN auto_fix_reviews INTEGER NOT NULL DEFAULT 0")



def _migrate_repos_unique_name(conn) -> None:
    """Schema v23: repos.name UNIQUE (registry keys by name — issue #54).

    Legacy DBs predate the constraint and may hold duplicate names (or
    ''-named auto-created rows): first row (rowid order) keeps its name,
    later rows take ``<name>-2``/``<name>-3`` ('' → ``repo-2``/``repo-3``),
    skipping taken names. Names that are already unique are kept verbatim
    (even with surrounding whitespace); only a collision or empty name
    forces a rewrite. Then the UNIQUE index is created. Idempotent:
    re-runs no-op once the index exists and names are unique.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if not cols or "name" not in cols:
        return
    taken = set()
    for (key_ref, name) in conn.execute("SELECT key_ref, name FROM repos ORDER BY rowid"):
        raw = name or ""
        base = raw.strip() or "repo"
        if raw == base and raw and base not in taken:
            taken.add(base)
            continue
        if not raw or base in taken:
            i = 2
            while f"{base}-{i}" in taken:
                i += 1
            conn.execute("UPDATE repos SET name = ? WHERE key_ref = ?", (f"{base}-{i}", key_ref))
            taken.add(f"{base}-{i}")
            continue
        taken.add(base)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_name ON repos(name)")


def _migrate_sessions_type(conn) -> None:
    """Schema v6: sessions gains session_type + metadata (session-type spec).
    Backfill reads the fix-comments prompt header (the only surviving
    discriminator — initiator_command carries the harness name, never the
    subcommand), else the command's first word, else NULL (never guess).
    Idempotent: skips present columns; backfills only NULL-typed rows.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if not cols:
        return
    if "session_type" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN session_type TEXT")
    if "metadata" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
    for sid, cmd, prompt in conn.execute(
            "SELECT id, initiator_command, prompt FROM sessions WHERE session_type IS NULL"):
        conn.execute("UPDATE sessions SET session_type = ? WHERE id = ?",
                     (_backfill_session_type(cmd or "", prompt or ""), sid))


#: Allowed transcript roots under config_dir(): v16 renamed sessions/ → runs/.
RUNS_DIRNAME = "runs"
LEGACY_SESSIONS_DIRNAME = "sessions"

#: runs columns the v16 fold absorbs from sessions (name → ALTER DDL tail).
_FOLD_COLS = (
    ("worktree_ref", "TEXT NOT NULL DEFAULT ''"),
    ("harness_name", "TEXT NOT NULL DEFAULT ''"),
    ("initiator_command", "TEXT NOT NULL DEFAULT ''"),
    ("prompt", "TEXT NOT NULL DEFAULT ''"),
    ("file_path", "TEXT NOT NULL DEFAULT ''"),
    ("session_type", "TEXT"),
    ("metadata", "TEXT NOT NULL DEFAULT '{}'"),
    ("state", "TEXT NOT NULL DEFAULT 'finished'"),
    ("continued_from_run_id", "INTEGER REFERENCES runs(id) ON DELETE SET NULL"),
)


def _runs_seq(conn) -> int:
    """High-water id ever issued for runs (0 when the sequence row is absent)."""
    try:
        row = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'runs'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def _restore_runs_seq(conn, prior: int) -> None:
    """Keep the runs sequence at/above *prior* and the copied max(id).

    Rebuilding the table drops its sqlite_sequence entry, which resets the
    high-water mark to the copied max(id): a previously issued (and since
    deleted) id would be handed out again. *prior* is read before the
    rebuild (_runs_seq) so no issued id is ever reused.
    """
    top = int(conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()[0])
    want = max(prior, top)
    row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'runs'").fetchone()
    if row is None:
        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('runs', ?)",
                     (want,))
    else:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?)"
                     " WHERE name = 'runs'", (want,))


def runs_dir(config_dir: Path | None = None) -> Path:
    """Transcript root (``<config>/runs``), created on demand."""
    from . import store as _store
    base = (config_dir or _store.config_dir()) / RUNS_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _transcript_alt(file_path: str, src: Path, dst: Path) -> str:
    """*file_path* re-rooted from *src* onto *dst* ('' when not under src)."""
    if not file_path:
        return ""
    try:
        rel = Path(file_path).relative_to(src)
    except ValueError:
        return ""
    return str(dst / rel)


def resolve_transcript_path(file_path: str,
                            config_dir: Path | None = None) -> Path | None:
    """Stored transcript path → the readable file (runs/ first, sessions/ fallback).

    v16 moved transcripts from ``sessions/`` to ``runs/`` and rewrote the
    stored paths; a row that still names the old root (pre-migration DB,
    restored backup, transcript that could not move) resolves through the
    fallback instead of failing to resume. An empty stored path (runs carry
    ``file_path DEFAULT ''``) resolves to None — never to the CWD.
    """
    if not file_path:
        return None
    stored = Path(file_path).expanduser()
    if config_dir is None:
        from . import store as _store
        config_dir = _store.config_dir()
    new_root = config_dir / RUNS_DIRNAME
    old_root = config_dir / LEGACY_SESSIONS_DIRNAME
    candidates = [stored]
    for src, dst in ((old_root, new_root), (new_root, old_root)):
        alt = _transcript_alt(str(stored), src, dst)
        if alt:
            candidates.append(Path(alt))
    for cand in candidates:
        if cand.exists():
            return cand
    return stored


def _move_transcripts(src: Path, dst: Path) -> None:
    """Merge the legacy transcript tree *src* into *dst* (never overwrites)."""
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in list(src.iterdir()):
        target = dst / child.name
        if target.exists():
            if child.is_dir() and target.is_dir():
                _move_transcripts(child, target)
            continue
        shutil.move(str(child), str(target))
    try:
        src.rmdir()
    except OSError:
        pass


def _rehome_transcripts(conn, config_dir: Path | None = None) -> None:
    """Move ``sessions/`` → ``runs/`` and repoint stored paths (idempotent).

    Runs on every init pass, not just the one that drops sessions: a crash
    between the fold's commit and the tree move leaves rows naming the old
    root. The stored path is rewritten to the ``runs/`` target
    unconditionally — a not-yet-moved file still reads through
    ``resolve_transcript_path``'s old-root fallback — and the next process
    retries the move while ``sessions/`` exists.
    """
    if config_dir is None:
        row = conn.execute("PRAGMA database_list").fetchone()
        config_dir = Path(row[2]).parent if row and row[2] else None
    if config_dir is None:
        return
    old_root = config_dir / LEGACY_SESSIONS_DIRNAME
    new_root = config_dir / RUNS_DIRNAME
    _move_transcripts(old_root, new_root)
    for rid, fp in conn.execute(
            "SELECT id, file_path FROM runs WHERE file_path != ''").fetchall():
        moved = _transcript_alt(fp, old_root, new_root)
        if moved:
            conn.execute("UPDATE runs SET file_path = ? WHERE id = ?",
                         (moved, rid))


def migrate_sessions_into_runs(conn, config_dir: Path | None = None) -> bool:
    """Schema v16: fold sessions into runs, drop the table, move transcripts.

    Every run inherits its session's transcript identity (worktree_ref,
    harness_name, initiator_command, prompt, file_path, session_type,
    metadata, state) and the runs of one transcript form a chain through
    continued_from_run_id (each later run points at the transcript's first
    run) — that is how fix-continue stays resolvable without sessions.
    Preview rows keep state='preview': they carry the single-line
    ``harness command:`` banner, while a real execution adds the harness log
    as a second line and keeps the session's finished|failed state. Then the
    runs table is rebuilt without the sessions FK, the sessions table
    dropped, and the on-disk tree moved ``sessions/`` → ``runs/`` with stored
    paths rewritten (``_rehome_transcripts``, retried by every later init
    pass). One-way: there is no down migration. A DB left
    half-folded (sessions present, runs already rebuilt without
    ``session_id``) still completes — its transcript linkage is
    unrecoverable, so the fold drops sessions without joining on it.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if not cols:
        return False
    for name, decl in _FOLD_COLS:
        if name not in cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")
    if not list(conn.execute("PRAGMA table_info(sessions)")):
        # Already folded: still retry a tree move a crash may have skipped.
        _rehome_transcripts(conn, config_dir)
        return False
    # One-way migration: no down migration. The real mitigation is copying
    # state.db + the transcript trees before upgrading (backup export only
    # covers actions/flow templates). Notice on stderr only — stdout stays
    # data-only.
    print("note: folding sessions into runs (v16, one-way — no down"
          " migration); copy state.db plus the runs/ and sessions/ trees"
          " before upgrading to roll back", file=sys.stderr)
    # A DB left half-folded by an intermediate build of this branch still
    # has sessions but its runs table was already rebuilt without
    # session_id (the pre-fold rebuild committed, then the fold raised).
    # That linkage is unrecoverable, so the fold must still drop sessions —
    # it just cannot join on the missing column.
    if "session_id" in cols:
        conn.execute(
            "UPDATE runs SET"
            " worktree_ref = COALESCE(s.worktree_ref, ''),"
            " harness_name = COALESCE(s.harness_name, ''),"
            " initiator_command = COALESCE(s.initiator_command, ''),"
            " prompt = COALESCE(s.prompt, ''),"
            " file_path = COALESCE(s.file_path, ''),"
            " session_type = s.session_type,"
            " metadata = COALESCE(s.metadata, '{}'),"
            " state = COALESCE(s.state, 'finished')"
            " FROM sessions s WHERE s.id = runs.session_id")
    # Preview rows are the single-line ``harness command:`` banner (empty
    # validation). Real executions store a second output line (the harness
    # log / validation trace), so they keep the session's finished|failed
    # state instead of being stamped preview.
    conn.execute("UPDATE runs SET state = 'preview'"
                 " WHERE output LIKE 'harness command:%'"
                 " AND instr(output, char(10)) = 0")
    if "session_id" in cols:
        conn.execute(
            "UPDATE runs SET continued_from_run_id ="
            " (SELECT MIN(r2.id) FROM runs r2"
            "  WHERE r2.session_id = runs.session_id)"
            " WHERE id <> (SELECT MIN(r2.id) FROM runs r2"
            "              WHERE r2.session_id = runs.session_id)")
    if conn.in_transaction:
        conn.commit()
    was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if was_on:
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        prior_seq = _runs_seq(conn)
        ddl = _canonical_ddl()["runs"]
        conn.execute('DROP TABLE IF EXISTS "runs_rebuild"')
        conn.execute(ddl.replace("CREATE TABLE runs", 'CREATE TABLE "runs_rebuild"', 1))
        new_cols = {r[1] for r in conn.execute('PRAGMA table_info("runs_rebuild")')}
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")
                    if r[1] in new_cols]
        names = ", ".join(f'"{c}"' for c in old_cols)
        conn.execute(f'INSERT INTO "runs_rebuild" ({names}) SELECT {names} FROM runs')
        conn.execute("DROP TABLE runs")
        conn.execute('ALTER TABLE "runs_rebuild" RENAME TO runs')
        # AUTOINCREMENT sequence: the rebuild reset it to the copied
        # max(id); restore the pre-fold high-water mark so an id issued
        # before the migration (and since deleted) is never handed out
        # again to a stale reference.
        _restore_runs_seq(conn, prior_seq)
        conn.execute("DROP TABLE sessions")
        if conn.in_transaction:
            conn.commit()
    finally:
        if was_on:
            conn.execute("PRAGMA foreign_keys = ON")
    _rehome_transcripts(conn, config_dir)
    return True

#: Cap on persisted run output lines (mirrors the live buffer policy at a
#: larger scale: enough log to diagnose, bounded DB growth).
MAX_RUN_OUTPUT_LINES = 10000

#: Allowed values for runs.session_type — an enum, never free text (NULL = unknown).
SESSION_TYPES = ("start", "review", "sync", "fix_comments", "custom_action",
                 "flow", "prodtest_plan", "prodtest_run")

#: Allowed values for runs.state — the lifecycle of one execution row.
RUN_STATES = ("running", "finished", "failed", "preview")

#: Fix-comments prompt header — the only surviving discriminator for history
#: rows (initiator_command carries the harness name, never the subcommand).
_FIX_PROMPT_PREFIX = "Fix all open (not-resolved) review comments on this PR/MR:"


def db_path(config_dir: Path | None = None) -> Path:
    """State DB path under *config_dir* (default: store.config_dir())."""
    from . import store as _store
    base = config_dir or _store.config_dir()
    return base / "state.db"


def _slug_segment(segment: str) -> str:
    """Slugify one path segment (branch parts stay nested, never flattened)."""
    import re
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (segment or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def branch_path(branch: str) -> str:
    """Branch name → nested path: `fix/a-b` stays `fix/a-b` (slugs per part).

    Empty/blank → "". Segments that slug to "" are dropped; a branch of
    only separators yields "".
    """
    parts = [p for p in (branch or "").split("/") if p.strip()]
    slugged = [_slug_segment(p) for p in parts]
    return "/".join(p for p in slugged if p)


def branch_slug(branch: str) -> str:
    """Normalize a git branch name to one path-safe segment.

    `feature/new-feature` → `feature-new-feature` (same rule as
    `refs.slugify_repo`, duplicated to avoid a store→refs import cycle).
    Empty/blank → "".
    """
    return _slug_segment(branch)


def gen_session_id(now: datetime | None = None) -> str:
    """Transcript id (UTC timestamp + random suffix) for a harness launch."""
    ts = (now or datetime.now(timezone.utc)).strftime(
        "%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    return f"{ts}-{random.randint(1000, 9999):04d}"


def derive_session_type(command: str, fix_comments: bool = False) -> str | None:
    """Subcommand + flag → session_type. Unknown → None, never guess."""
    c = (command or "").strip().lower()
    if c == "review" and fix_comments:
        return "fix_comments"
    if c in ("start", "review", "sync", "flow"):
        return c
    if c in ("action", "adhoc"):
        return "custom_action"
    first_two = " ".join(c.split()[:2])
    if first_two == "prodtest plan":
        return "prodtest_plan"
    if first_two == "prodtest run":
        return "prodtest_run"
    return None


def _backfill_session_type(initiator_command: str, prompt: str) -> str | None:
    """History backfill: prompt header first, then command starts-with."""
    if (prompt or "").startswith(_FIX_PROMPT_PREFIX):
        return "fix_comments"
    first = (initiator_command or "").split(" ", 1)[0].strip().lower()
    if first in ("start", "review", "sync", "flow"):
        return first
    if first == "action":
        return "custom_action"
    if "--fix-comments" in (initiator_command or ""):
        return "fix_comments"
    return None


def session_file_path(session_id: str, harness_name: str,
                      config_dir: Path | None = None,
                      branch: str | None = None,
                      session_type: str | None = None) -> Path:
    """Transcript path for a harness launch under ``<config>/runs/…``.

    Layout ``runs/<harness>/<branch path>/<kind>/<id>.jsonl``; the dir is
    created so a ``--session-dir`` argument always points at a real tree.
    """
    from . import store as _store
    base = (config_dir or _store.config_dir()) / RUNS_DIRNAME / harness_name
    bpath = branch_path(branch or "")
    if bpath:
        base = base / bpath
    stype = (session_type or "").strip().lower().replace("-", "_")
    if stype in SESSION_TYPES:
        base = base / stype
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{session_id}.jsonl"


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


def _parse_json_dict(value: object) -> dict:
    """JSON-object column → dict ({} on NULL/garbage/non-object)."""
    try:
        out = json.loads(value or "{}")  # type: ignore[arg-type]
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}


def _run_dict(row: sqlite3.Row) -> dict:
    """One runs row with its JSON columns parsed."""
    out = dict(row)
    try:
        out["args"] = json.loads(out.get("args") or "[]")
    except Exception:
        out["args"] = []
    for col in ("validation", "metadata"):
        if col in out:
            out[col] = _parse_json_dict(out.get(col))
    # v26 stores "no worktree" as NULL (the FK's optional parent); readers
    # and the API keep the historical '' shape.
    if "worktree_ref" in out and out["worktree_ref"] is None:
        out["worktree_ref"] = ""
    return out


def insert_run(path: Path, command: str, args: list[str],
               exit_code: int | None = None, *,
               worktree_ref: str | None = None,
               harness_name: str = "", initiator_command: str = "",
               prompt: str = "", file_path: str = "",
               session_type: str | None = None,
               metadata: dict | None = None,
               state: str = "finished",
               continued_from_run_id: int | None = None,
               parent_run_id: int | None = None,
               step_index: int | None = None,
               output: list[str] | None = None,
               truncated: bool = False,
               validation: dict | None = None,
               flow_id: int | None = None,
               finished_at: str = "",
               duration_ms: int | None = None,
               model_id: str = "",
               effort: str = "",
               input_tokens: int | None = None,
               output_tokens: int | None = None,
               cached_tokens: int | None = None,
               cost_usd: float | None = None) -> int:
    """Insert one execution row; returns its id.

    A run owns its transcript identity (``worktree_ref``, harness,
    initiator command, prompt, ``file_path``, ``session_type``, metadata) —
    the folded sessions entity lives here. ``session_type``/``state`` are
    enums, never free text; an unknown value is rejected. ``worktree_ref``
    is stored as NULL for rows with no worktree (pre-link attempts, web
    runs); web and terminal runs pass the ref they were spawned for.

    ``continued_from_run_id`` links a fix-continue row to the transcript's
    first run. A row inserted without an explicit ``flow_id`` joins its
    transcript's flow tagging (newest run of the same ``file_path``).
    """
    if session_type is not None and session_type not in SESSION_TYPES:
        raise ValueError(f"session_type must be one of {', '.join(SESSION_TYPES)}"
                         f" (got {session_type!r})")
    if state not in RUN_STATES:
        raise ValueError(f"state must be one of {', '.join(RUN_STATES)}"
                         f" (got {state!r})")
    init_db(path)
    created = datetime.now(timezone.utc).isoformat()
    out_lines = list(output or [])
    lines = out_lines[-MAX_RUN_OUTPUT_LINES:]
    truncated = bool(truncated) or len(out_lines) > len(lines)
    import os as _os
    env_parent = False
    if parent_run_id is None:
        # WORKAGENT_PARENT_RUN_ID is the explicit propagation channel
        # (harness sessions set it for descendant work; see cli_harness).
        # WORKAGENT_RUN_ID is the web supervisor's spawn identity
        # (web_runs._spawn sets only it), so accept it as a fallback: this
        # keeps web-launched orchestrator grandchildren linked. A stale
        # value that names no live row is dropped by the guard below.
        raw_pid = ((_os.environ.get("WORKAGENT_PARENT_RUN_ID") or "").strip()
                   or (_os.environ.get("WORKAGENT_RUN_ID") or "").strip())
        if raw_pid:
            try:
                pid_val = int(raw_pid)
                if pid_val > 0:
                    parent_run_id = pid_val
                    env_parent = True
            except ValueError:
                pass
    if step_index is None:
        raw_step = (_os.environ.get("WORKAGENT_STEP_INDEX") or "").strip()
        if raw_step:
            try:
                step_index = int(raw_step)
            except ValueError:
                pass
    names = ["command", "args", "exit_code", "created_at",
             "output", "truncated", "validation", "worktree_ref",
             "harness_name", "initiator_command", "prompt", "file_path",
             "session_type", "metadata", "state", "continued_from_run_id",
             "parent_run_id", "step_index",
             "finished_at", "duration_ms", "model_id", "effort",
             "input_tokens", "output_tokens", "cached_tokens", "cost_usd"]
    vals: list = [command, json.dumps(args), exit_code, created,
                  "\n".join(lines), 1 if truncated else 0,
                  json.dumps(validation or {}), worktree_ref or None,
                  harness_name, initiator_command, prompt, file_path,
                  session_type, json.dumps(metadata or {}), state,
                  continued_from_run_id, parent_run_id, step_index,
                  finished_at or "", duration_ms, model_id or "", effort or "",
                  input_tokens, output_tokens, cached_tokens, cost_usd]
    with connect(path) as conn:
        if env_parent and parent_run_id is not None:
            if conn.execute("SELECT 1 FROM runs WHERE id = ?",
                            (parent_run_id,)).fetchone() is None:
                vals[names.index("parent_run_id")] = None
        if flow_id is not None:
            names.append("flow_id")
            vals.append(flow_id)
        elif file_path:
            row = conn.execute(
                "SELECT flow_id FROM runs WHERE file_path = ?"
                " AND flow_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                (file_path,)).fetchone()
            if row is not None and row[0] is not None:
                names.append("flow_id")
                vals.append(row[0])
        cur = conn.execute(
            f"INSERT INTO runs ({', '.join(names)})"
            f" VALUES ({', '.join(['?'] * len(names))})", vals)
        return cur.lastrowid


def get_run(path: Path, run_id: int,
            include_deleted_worktrees: bool = False) -> dict | None:
    """One run row (JSON columns parsed); None when unknown."""
    if not path.exists():
        return None
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.* FROM runs r"
             " LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref"
             " WHERE r.id = ?")
        if not include_deleted_worktrees:
            q += " AND " + _NOT_DELETED
        row = conn.execute(q, (run_id,)).fetchone()
    return _run_dict(row) if row is not None else None


def finish_run(path: Path, run_id: int, state: str, *,
               duration_ms: int | None = None,
               model_id: str | None = None,
               effort: str | None = None,
               input_tokens: int | None = None,
               output_tokens: int | None = None,
               cached_tokens: int | None = None,
               cost_usd: float | None = None) -> None:
    """Set a run's terminal state (finished/failed/preview) and optional telemetry."""
    if state not in ("finished", "failed", "preview"):
        raise ValueError(f"unknown run state: {state!r}")
    now = datetime.now(timezone.utc)
    finished_iso = now.isoformat()
    with connect(path) as conn:
        if duration_ms is None:
            row = conn.execute("SELECT created_at FROM runs WHERE id = ?",
                               (run_id,)).fetchone()
            if row and row[0]:
                try:
                    created_dt = datetime.fromisoformat(str(row[0]))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    duration_ms = max(0, int((now - created_dt).total_seconds() * 1000))
                except Exception:
                    duration_ms = None
        sets = ["state = ?", "finished_at = ?"]
        vals: list = [state, finished_iso]
        if duration_ms is not None:
            sets.append("duration_ms = ?")
            vals.append(int(duration_ms))
        if model_id:
            sets.append("model_id = ?")
            vals.append(str(model_id))
        if effort:
            sets.append("effort = ?")
            vals.append(str(effort))
        if input_tokens is not None:
            sets.append("input_tokens = ?")
            vals.append(int(input_tokens))
        if output_tokens is not None:
            sets.append("output_tokens = ?")
            vals.append(int(output_tokens))
        if cached_tokens is not None:
            sets.append("cached_tokens = ?")
            vals.append(int(cached_tokens))
        if cost_usd is not None:
            sets.append("cost_usd = ?")
            vals.append(float(cost_usd))
        conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?",
                     (*vals, run_id))


def set_run_file_path(path: Path, run_id: int, file_path: str) -> None:
    """Point a run at the harness-minted transcript (best effort).

    omp mints its own filename under --session-dir, so the pre-launch
    guessed path is only a scope hint. After a blocking launch returns the
    real .jsonl is known — this records it. Never raises.
    """
    try:
        with connect(path) as conn:
            conn.execute("UPDATE runs SET file_path = ? WHERE id = ?",
                         (file_path, run_id))
    except Exception:
        pass


def set_run_metadata(path: Path, run_id: int, patch: dict) -> dict:
    """Merge *patch* into the run's metadata JSON; return the merged dict."""
    with connect(path) as conn:
        row = conn.execute("SELECT metadata FROM runs WHERE id = ?",
                           (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"no run {run_id}")
        meta = _parse_json_dict(row[0])
        meta.update(patch or {})
        conn.execute("UPDATE runs SET metadata = ? WHERE id = ?",
                     (json.dumps(meta), run_id))
        return meta


def set_run_flow_id(path: Path, run_id: int, flow_id: int) -> None:
    """Tag a run with *flow_id* (column + metadata history field)."""
    with connect(path) as conn:
        conn.execute("UPDATE runs SET flow_id = ? WHERE id = ?", (flow_id, run_id))
    set_run_metadata(path, run_id, {"flow_id": flow_id})


def latest_review_run(path: Path, worktree_ref: str,
                      include_deleted_worktrees: bool = False) -> dict | None:
    """Newest non-running review run for the ref with a real transcript.

    An adoption row stamps ``worktree_ref``/``session_type='review'`` while
    deliberately keeping ``file_path=''``; it names no transcript, so it must
    never become the "current one to continue".
    """
    if not path.exists():
        return None
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.* FROM runs r"
             " LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref"
             " WHERE r.worktree_ref = ? AND r.session_type = 'review'"
             " AND r.state != 'running' AND r.file_path != ''")
        if not include_deleted_worktrees:
            q += " AND " + _NOT_DELETED
        q += " ORDER BY r.created_at DESC, r.id DESC LIMIT 1"
        row = conn.execute(q, (worktree_ref,)).fetchone()
    return _run_dict(row) if row is not None else None


def run_by_file_path(path: Path, file_path: str,
                     include_deleted_worktrees: bool = False) -> dict | None:
    """Run whose file_path equals *file_path* (the pinned transcript), newest
    first. A harness-minted filename never matches by stem — the pinned path
    itself is the reliable join key."""
    if not file_path or not path.exists():
        return None
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.* FROM runs r"
             " LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref"
             " WHERE r.file_path = ?")
        if not include_deleted_worktrees:
            q += " AND " + _NOT_DELETED
        q += " ORDER BY r.created_at DESC, r.id DESC LIMIT 1"
        row = conn.execute(q, (file_path,)).fetchone()
    return _run_dict(row) if row is not None else None


def run_by_file_stem(path: Path, stem: str,
                     include_deleted_worktrees: bool = False) -> dict | None:
    """Newest run whose stored ``file_path`` names ``<stem>.jsonl``.

    Legacy rows were keyed by the transcript stem (session PK); after the
    fold the pinned path itself is the join key, so this is the fallback
    for a row whose ``file_path`` was never updated to the harness-minted
    filename. The stem is compared, never the whole path, so a row stays
    findable across the ``sessions/`` → ``runs/`` rename.
    """
    if not stem or not path.exists():
        return None
    like = "%" + stem.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ".jsonl"
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.* FROM runs r"
             " LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref"
             " WHERE r.file_path LIKE ? ESCAPE '\\'")
        if not include_deleted_worktrees:
            q += " AND " + _NOT_DELETED
        q += " ORDER BY r.created_at DESC, r.id DESC"
        for row in conn.execute(q, (like,)):
            if Path(str(row["file_path"] or "")).stem == stem:
                return _run_dict(row)
    return None


def transcript_root_run(path: Path, file_path: str,
                        include_deleted_worktrees: bool = False) -> dict | None:
    """First (earliest) run carrying *file_path* — the transcript's chain head.

    Fix-continue rows link back here via ``continued_from_run_id``: one
    transcript, one chain head, however many preview/launch/attempt rows
    name it. Empty *file_path* (no transcript) has no chain.
    """
    if not file_path or not path.exists():
        return None
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.* FROM runs r"
             " LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref"
             " WHERE r.file_path = ?")
        if not include_deleted_worktrees:
            q += " AND " + _NOT_DELETED
        q += " ORDER BY r.id ASC LIMIT 1"
        row = conn.execute(q, (file_path,)).fetchone()
    return _run_dict(row) if row is not None else None


def runs_for_worktree(path: Path, worktree_ref: str,
                      include_deleted_worktrees: bool = False) -> list[dict]:
    """Every run recorded for *worktree_ref*, newest first (GC, flow tagging)."""
    if not worktree_ref or not path.exists():
        return []
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.* FROM runs r"
             " LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref"
             " WHERE r.worktree_ref = ?")
        if not include_deleted_worktrees:
            q += " AND " + _NOT_DELETED
        q += " ORDER BY r.id DESC"
        return [_run_dict(r) for r in conn.execute(q, (worktree_ref,)).fetchall()]


#: Runs of soft-deleted worktrees stay in the DB (restorable via undelete)
#: but are hidden from list/detail queries unless *include_deleted_worktrees*;
#: rows with no ref (NULL: repo-scoped attempts, web runs) or a dangling ref
#: (pre-v26 leftovers, purged by store.purge_orphans) stay visible — same
#: pass-through rule as list_flows. Every query using it aliases runs as ``r``.
_NOT_DELETED = ("(r.worktree_ref IS NULL OR w.ref_key IS NULL"
                " OR (w.deleted_at IS NULL OR w.deleted_at = ''))")


def list_runs(path: Path, limit: int = 200,
              include_deleted_worktrees: bool = False) -> list[dict]:
    """Persisted runs, newest first (drives GET /api/runs)."""
    if not path.exists():
        return []
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.id, r.command, r.args, r.exit_code,"
             " r.created_at, r.output, r.truncated, r.validation, r.flow_id,"
             " r.file_path, r.worktree_ref,"
             " r.session_type, r.state, r.harness_name, r.initiator_command,"
             " r.metadata, r.continued_from_run_id,"
             " r.parent_run_id, r.step_index,"
             " r.finished_at, r.duration_ms, r.model_id, r.effort,"
             " r.input_tokens, r.output_tokens, r.cached_tokens, r.cost_usd"
             " FROM runs r LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref")
        if not include_deleted_worktrees:
            q += " WHERE " + _NOT_DELETED
        q += " ORDER BY r.id DESC LIMIT ?"
        return [_run_dict(r) for r in conn.execute(q, (limit,)).fetchall()]


def runs_by_parent(path: Path, parent_run_id: int,
                   include_deleted_worktrees: bool = False) -> list[dict]:
    """Child runs whose parent_run_id matches (targeted WHERE lookup)."""
    if not path.exists():
        return []
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT r.id, r.command, r.args, r.exit_code,"
             " r.created_at, r.output, r.truncated, r.validation, r.flow_id,"
             " r.file_path, r.worktree_ref,"
             " r.session_type, r.state, r.harness_name, r.initiator_command,"
             " r.metadata, r.continued_from_run_id,"
             " r.parent_run_id, r.step_index"
             " FROM runs r LEFT JOIN worktrees w ON w.ref_key = r.worktree_ref")
        if not include_deleted_worktrees:
            q += " WHERE " + _NOT_DELETED + " AND r.parent_run_id = ?"
        else:
            q += " WHERE r.parent_run_id = ?"
        q += " ORDER BY r.id DESC"
        return [_run_dict(r) for r in conn.execute(q, (parent_run_id,)).fetchall()]


def update_run(path: Path, run_id: int, *,
               exit_code: int | None = None,
               output: list[str] | None = None,
               truncated: bool | None = None,
               append: bool = False) -> None:
    """Progressively update a runs row (live log + final state).

    *append* joins *output* onto the stored tail for that run id;
    otherwise *output* replaces the stored blob. Unset fields stay.
    Never raises: callers (tailers/step wrappers) must not fail on DB writes.
    """
    try:
        with connect(path) as conn:
            row = conn.execute("SELECT output FROM runs WHERE id = ?",
                               (run_id,)).fetchone()
            if row is None:
                return
            sets: list[str] = []
            vals: list = []
            if output is not None:
                if append:
                    lines = ((row[0] or "").split("\n") if row[0] else [])
                    lines += list(output)
                    lines = lines[-MAX_RUN_OUTPUT_LINES:]
                else:
                    lines = list(output)[-MAX_RUN_OUTPUT_LINES:]
                sets.append("output = ?")
                vals.append("\n".join(lines))
            if truncated is not None:
                sets.append("truncated = ?")
                vals.append(1 if truncated else 0)
            if exit_code is not None:
                sets.append("exit_code = ?")
                vals.append(exit_code)
            if not sets:
                return
            conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?",
                         (*vals, run_id))
    except Exception:
        pass

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
    # runs: no session_id — legacy DBs still carry it until the v16 fold
    # rebuilds the table, and folded DBs must not be rebuilt for its loss.
    "runs": ("command", "args", "created_at"),
    "issue_cache": ("source", "payload", "fetched_at"),
    "pr_candidates_cache": ("source", "payload", "fetched_at"),
    "release_diff_cache": ("repo_key", "count", "fetched_at"),
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
    runs_seq = _runs_seq(conn)
    try:
        for table, ddl in _canonical_ddl().items():
            info = list(conn.execute(f"PRAGMA table_info({table})"))
            if not info:
                continue
            by_name = {r[1]: r for r in info}
            bad = any(c not in by_name or by_name[c][3] != 1 or by_name[c][4] is not None
                      for c in _REQUIRED_COLS.get(table, ()))
            if table == "runs":
                if list(conn.execute("PRAGMA table_info(sessions)")):
                    # pre-v16: the fold rebuilds runs from the canonical DDL
                    # and still needs the legacy session_id column for its
                    # linkage, so rebuilding here would drop it too early
                    continue
                if "session_id" in by_name:
                    # an earlier fold build still declared session_id; the
                    # dead NOT NULL column must go, insert_run no longer
                    # fills it (every insert would fail otherwise)
                    bad = True
            if not bad:
                continue
            tmp = f"{table}_rebuild"
            conn.execute(f'DROP TABLE IF EXISTS "{tmp}"')
            conn.execute(ddl.replace(f"CREATE TABLE {table}",
                                     f'CREATE TABLE "{tmp}"', 1))
            new_cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{tmp}")')}
            pairs = [(r[1], r[1]) for r in info if r[1] in new_cols]
            cols = ", ".join(f'"{dst}"' for _, dst in pairs)
            sel = ", ".join(f'"{src}"' for src, _ in pairs)
            conn.execute(f'INSERT INTO "{tmp}" ({cols}) SELECT {sel} FROM "{table}"')
            conn.execute(f'DROP TABLE "{table}"')
            conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{table}"')
            rebuilt.append(table)
        if conn.in_transaction:
            conn.commit()
    finally:
        if was_on:
            conn.execute("PRAGMA foreign_keys = ON")
    if "runs" in rebuilt:
        # The rebuild reset the AUTOINCREMENT sequence to the copied
        # max(id); keep the pre-rebuild high-water mark so a previously
        # issued id is never reused.
        _restore_runs_seq(conn, runs_seq)
        conn.commit()
    return rebuilt


_INIT_CACHE: dict[str, bool] = {}


def _schema_current(path: Path) -> bool:
    """Read-only probe: True when all canonical tables exist with the
    post-migration columns (v16 fold included). Read-only connections can
    never take the RESERVED write lock, so this never raises
    ``database is locked`` no matter who holds the DB.
    """
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except Exception:
        return False
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"trackers", "repos", "worktrees", "runs"} <= tables:
            return False
        # v16 folded the sessions entity into runs: the table must be gone
        # and runs must carry the transcript columns.
        if "sessions" in tables:
            return False
        cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        # A DB folded by an intermediate build still carries the dead
        # session_id NOT NULL column, which insert_run no longer fills; only
        # a rebuild can drop it, so it is not current. The pre-fold case
        # returned above, where the column is still needed.
        if "session_id" in cols:
            return False
        if not {"output", "truncated", "validation", "flow_id", "worktree_ref",
                "harness_name", "initiator_command", "prompt", "file_path",
                "session_type", "metadata", "state",
                "continued_from_run_id", "parent_run_id", "step_index",
                "finished_at", "duration_ms", "model_id", "effort",
                "input_tokens", "output_tokens", "cached_tokens", "cost_usd"} <= cols:
            return False
        fks = list(conn.execute("PRAGMA foreign_key_list(runs)"))
        if any(r[2] == "sessions" for r in fks):
            return False
        # v26: worktree_ref is an optional parent (NULL = repo-scoped run)
        # whose deletion cascades the run.
        wfk = next((r for r in fks if r[2] == "worktrees"
                    and (r[3] or "") == "worktree_ref"), None)
        if wfk is None or (wfk[6] or "").upper() != "CASCADE":
            return False
        if any(r[1] == "worktree_ref" and r[3] for r in conn.execute(
                "PRAGMA table_info(runs)")):
            return False
        wcols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
        if "active" not in wcols:
            return False
        if "deleted_at" not in wcols:
            return False
        if "related_repos" not in wcols:
            return False
        if "parent_key_ref" not in wcols:
            return False
        widx = list(conn.execute("PRAGMA index_list(worktrees)"))
        idx_cols = []
        for entry in widx:
            try:
                idx_cols.append({row[2] for row in
                                 conn.execute(f'PRAGMA index_info("{entry[1]}")')})
            except Exception:
                continue
        if not any(c == {"repo_key", "branch"} for c in idx_cols):
            return False
        if any(bool(entry[2]) and c == {"branch"} for entry, c in zip(widx, idx_cols)):
            return False
        for table in ("trackers", "repos"):
            fcols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "fetch_enabled" not in fcols:
                return False
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(trackers)")}
        if "is_enabled" not in tcols:
            return False
        rcols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
        if not {"default_branch", "release_branch", "overlay_buttons", "auto_fix_ci", "auto_fix_reviews"} <= rcols:
            return False
        if "idx_repos_name" not in {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'repos'")}:
            return False
        if "release_diff_cache" not in tables:
            return False
        if "custom_actions" not in tables:
            return False
        if "flow_templates" not in tables or "flows" not in tables:
            return False
        tcols2 = {r[1] for r in conn.execute("PRAGMA table_info(flow_templates)")}
        if "parent_labels" not in tcols2 or "icon" not in tcols2:
            return False
        fcols2 = {r[1] for r in conn.execute("PRAGMA table_info(flows)")}
        if "effective_steps" not in fcols2:
            return False
        if "prodtest_items" not in tables:
            return False
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(prodtest_items)")}
        if not {"worktree_ref", "run_results"} <= pcols:
            return False
        mcols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")} if "messages" in tables else set()
        if mcols and "worktrees" not in mcols:
            return False
        if mcols and "related_repos" not in mcols:
            return False
        if not {"pr_checks", "pr_review_threads", "worktree_events"} <= tables:
            return False
        if "change_log" not in tables:
            return False
        if "schedules" not in tables:
            return False
        triggers = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        if not (_CDC_TRIGGER_NAMES <= triggers):
            return False
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def init_db(path: Path, defer_backfill: bool = False) -> Path:
    """Create/migrate schema synchronously (DB migrations stay on the caller).

    The release-branch backfill shells out to git per repo, so
    run_server passes defer_backfill=True and serves while a background
    thread runs backfill_async(). Every other caller keeps the default:
    backfill inline, same as before.
    """
    if _INIT_CACHE.get(str(path)) and _schema_current(path):
        if not _INIT_CACHE.get("backfill") and not _INIT_CACHE.get("backfill:started"):
            if defer_backfill:
                backfill_async()
            else:
                _run_backfill()
        return path
    import time
    last: Exception | None = None
    for attempt in range(5):
        if attempt and _schema_current(path):
            break
        try:
            with connect(path) as conn:
                conn.executescript(SCHEMA)
                _migrate_v2(conn)
                _migrate_runs_output(conn)
                _migrate_runs_validation(conn)
                _migrate_worktrees_active(conn)
                _migrate_worktrees_branch_scope(conn)
                _migrate_sessions_harness(conn)
                _migrate_sessions_type(conn)
                _migrate_fetch_enabled(conn)
                _migrate_tracker_enabled(conn)
                _migrate_repos_release(conn)
                _migrate_repos_commands(conn)
                _migrate_repos_overlay_buttons(conn)
                _migrate_repos_auto_fix(conn)
                _migrate_repos_unique_name(conn)
                _migrate_worktrees_deleted_at(conn)
                _migrate_worktrees_related(conn)
                _migrate_flow_linkage(conn)
                _migrate_flow_template_lineage(conn)
                _migrate_display_cols(conn)
                _migrate_messages(conn)
                _migrate_messages_worktrees(conn)
                _migrate_messages_related_repos(conn)
                _migrate_category(conn)
                _migrate_run_immediately(conn)
                _migrate_flow_template_icon(conn)
                migrate_sessions_into_runs(conn, path.parent)
                _migrate_prodtest_cols(conn)
                _migrate_runs_worktree_fk(conn)
                _migrate_runs_parent(conn)
                _migrate_schedules(conn)
                _migrate_runs_telemetry(conn)
                _migrate_change_log(conn)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            if _schema_current(path):
                break
            time.sleep(0.05 * (2 ** attempt))
    else:
        raise last  # type: ignore[misc]
    _INIT_CACHE[str(path)] = True
    if defer_backfill:
        backfill_async()
    else:
        _run_backfill()
    return path


def _run_backfill() -> None:
    """Synchronous release-branch backfill (never raises; once per process)."""
    _INIT_CACHE["backfill"] = True
    try:
        from . import repos_release as _rel
        _rel.backfill_release_branches()
    except Exception:
        pass


def backfill_async() -> "threading.Thread | None":
    """Fire-and-forget _run_backfill (daemon; at most once per process)."""
    if _INIT_CACHE.get("backfill:started"):
        return None
    _INIT_CACHE["backfill:started"] = True
    t = threading.Thread(target=_run_backfill,
                         name="workagent-backfill", daemon=True)
    t.start()
    return t


def _norm(p: str) -> str:
    from pathlib import Path as _P
    return str(_P(p).expanduser().resolve()) if p else ""


from .store_trackers import (  # noqa: F401,E402
    _claim_repo_name,
    _insert_path_keyed_repo,
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
                conn.execute("INSERT INTO repos (key_ref, path, name, remote, tool, commands)"
                             " VALUES (?, ?, ?, ?, ?, ?)",
                             (name, norm, name, str((v or {}).get("remote", "") or ""),
                              str((v or {}).get("tool", "") or ""),
                              json.dumps(dict((v or {}).get("commands") or {}))))
                counts["repos"] += 1
                repo_key = name
            else:
                repo_key = row[0]
                counts["repos"] += conn.execute(
                    "UPDATE repos SET name = ?, remote = ?, tool = ?, commands = ? WHERE key_ref = ?",
                    (name, str((v or {}).get("remote", "") or ""),
                     str((v or {}).get("tool", "") or ""),
                     json.dumps(dict((v or {}).get("commands") or {})), repo_key)).rowcount
            counts["links"] += conn.execute(
                "INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key) VALUES (?, ?)",
                (tid, repo_key)).rowcount
        for tid, entry in cfg_trackers.items():
            for r in (entry or {}).get("repos", []):
                norm = _norm(str(r))
                row = conn.execute("SELECT key_ref FROM repos WHERE path = ?", (norm,)).fetchone()
                if row is None:
                    key = norm
                    base = key.rsplit("/", 1)[-1] if "/" in key else key
                    repo_key = _insert_path_keyed_repo(conn, key, base or key)
                else:
                    repo_key = row[0]
                counts["links"] += conn.execute(
                    "INSERT OR IGNORE INTO tracker_repos (tracker_key, repo_key) VALUES (?, ?)",
                    (tid, repo_key)).rowcount
    return counts

from .store_links import (  # noqa: F401,E402
    SOFT_DELETE_RETENTION_DAYS,
    delete_worktree_row, gc_soft_deleted, load_links_rows, purge_orphans,
    save_links_rows, set_worktree_active, soft_delete_worktree,
    undelete_worktree)

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
                    repo_key = _insert_path_keyed_repo(
                        conn, repo, repo.rsplit("/", 1)[-1] or repo)
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


def get_pr_candidates_cache(path: Path, source: str = "prs") -> tuple[list, str | None]:
    """Cached unlinked PR/MR rows + fetched_at ISO, or ([], None)."""
    import json
    init_db(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM pr_candidates_cache WHERE source = ?",
            (source,)).fetchone()
    if row is None:
        return [], None
    return json.loads(row[0]), row[1]


def set_pr_candidates_cache(path: Path, rows: list[dict], source: str = "prs") -> None:
    """Upsert cached unlinked PR/MR rows with a fresh fetched_at."""
    import json
    from datetime import datetime, timezone
    init_db(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO pr_candidates_cache (source, payload, fetched_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,"
            " fetched_at=excluded.fetched_at",
            (source, json.dumps(rows),
             datetime.now(timezone.utc).isoformat()))
def get_release_diff_cache(path: Path, repo_key: str) -> tuple[int | None, str]:
    """Cached release diff (count, fetched_at ISO); (None, '') on miss."""
    init_db(path)
    try:
        with connect(path) as conn:
            row = conn.execute(
                "SELECT count, fetched_at FROM release_diff_cache WHERE repo_key = ?",
                (repo_key,)).fetchone()
    except Exception:
        return None, ""
    if row is None:
        return None, ""
    return int(row[0]), str(row[1] or "")


def set_release_diff_cache(path: Path, repo_key: str, count: int) -> None:
    """Upsert cached release diff count with a fresh fetched_at."""
    from datetime import datetime, timezone
    init_db(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO release_diff_cache (repo_key, count, fetched_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(repo_key) DO UPDATE SET count=excluded.count,"
            " fetched_at=excluded.fetched_at",
            (repo_key, int(count), datetime.now(timezone.utc).isoformat()))
#: Allowed prodtest item statuses — an enum, never free text.
PRODTEST_STATUSES = ("pending", "pending_prodtest", "running",
                     "passed", "failed", "blocked")

_PRODTEST_COLS = ("plan_path", "plan_revision", "plan_risk",
                  "plan_diff_hash", "release_sha", "released_at",
                  "status", "last_run_id", "worktree_ref", "run_results")


def ensure_schema(path: Path) -> Path:
    """Alias for init_db (creates current schema incl. prodtest_items)."""
    return init_db(path)


def upsert_prodtest(path: Path, key: str, **fields) -> dict:
    """Create-or-update a prodtest item row; returns the stored row."""
    from datetime import datetime, timezone
    init_db(path)
    status = str(fields.get("status", "pending"))
    if status not in PRODTEST_STATUSES:
        raise ValueError(
            f"status must be one of {', '.join(PRODTEST_STATUSES)} (got {status!r})")
    extra = [c for c in _PRODTEST_COLS if c in fields]
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        if not extra:
            conn.execute(
                "INSERT INTO prodtest_items (issue_key, updated_at)"
                " VALUES (?, ?)"
                " ON CONFLICT(issue_key) DO UPDATE SET updated_at=excluded.updated_at",
                (key, now))
        else:
            sets = ", ".join(f"{c}=excluded.{c}" for c in extra)
            conn.execute(
                f"INSERT INTO prodtest_items (issue_key, {', '.join(extra)}, updated_at)"
                f" VALUES ({', '.join('?' * (len(extra) + 2))})"
                f" ON CONFLICT(issue_key) DO UPDATE SET {sets}, updated_at=excluded.updated_at",
                (key, *[fields[c] for c in extra], now))
    return get_prodtest(path, key)


def get_prodtest(path: Path, key: str) -> dict | None:
    """One prodtest item row as a dict; None when unknown."""
    import sqlite3 as _s3
    if not Path(path).exists():
        return None
    with connect(path) as conn:
        conn.row_factory = _s3.Row
        try:
            row = conn.execute(
                "SELECT * FROM prodtest_items WHERE issue_key = ?", (key,)).fetchone()
        except _s3.OperationalError:
            return None
    return dict(row) if row is not None else None


def set_prodtest_status(path: Path, key: str, status: str) -> dict:
    """Set a prodtest item's status; ValueError on unknown status/key."""
    if status not in PRODTEST_STATUSES:
        raise ValueError(
            f"status must be one of {', '.join(PRODTEST_STATUSES)} (got {status!r})")
    from datetime import datetime, timezone
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE prodtest_items SET status = ?, updated_at = ? WHERE issue_key = ?",
            (status, datetime.now(timezone.utc).isoformat(), key))
        if cur.rowcount == 0:
            raise ValueError(f"no prodtest item for {key}")
    return get_prodtest(path, key)

def delete_prodtest(path: Path, key: str) -> bool:
    """Delete one prodtest item row; True when a row existed."""
    if not Path(path).exists():
        return False
    with connect(path) as conn:
        cur = conn.execute(
            "DELETE FROM prodtest_items WHERE issue_key = ?", (key,))
        return cur.rowcount > 0


def list_prodtests(path: Path) -> list[dict]:
    """All prodtest item rows ordered by updated_at desc (empty on no DB)."""
    import sqlite3 as _s3
    if not Path(path).exists():
        return []
    with connect(path) as conn:
        conn.row_factory = _s3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM prodtest_items ORDER BY updated_at DESC").fetchall()
        except _s3.OperationalError:
            return []
    return [dict(r) for r in rows]


def gc_blocked_by_prodtest(path: Path, key: str) -> bool:
    """True when GC must skip *key* (prodtest pending_prodtest/running)."""
    if not Path(path).exists():
        return False
    row = get_prodtest(path, key)
    return bool(row) and row.get("status") in ("pending_prodtest", "running")


# ── Schedules (T13) ──────────────────────────────────────────────────

SCHEDULE_TARGET_KINDS: tuple[str, ...] = ("flow", "action", "command")


def _schedule_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    try:
        d["args"] = json.loads(d.get("args_json") or "{}")
    except Exception:
        d["args"] = {}
    return d


def _normalize_schedule_args(args_json: str | dict | list | None) -> str:
    if args_json is None:
        return "{}"
    if isinstance(args_json, str):
        raw = args_json.strip() or "{}"
        json.loads(raw)  # validate JSON
        return raw
    return json.dumps(args_json)


def list_schedules(path: Path) -> list[dict]:
    """Return all schedules ordered by id asc."""
    if not Path(path).exists():
        return []
    init_db(path)
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM schedules ORDER BY id ASC").fetchall()
    return [_schedule_row_to_dict(r) for r in rows]


def get_schedule(path: Path, schedule_id: int | str) -> dict | None:
    """Look up a schedule by integer id or unique name."""
    if not Path(path).exists():
        return None
    init_db(path)
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if isinstance(schedule_id, int) or (isinstance(schedule_id, str) and schedule_id.isdigit()):
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (int(schedule_id),)).fetchone()
            if row is not None:
                return _schedule_row_to_dict(row)
        row = conn.execute("SELECT * FROM schedules WHERE name = ?", (str(schedule_id),)).fetchone()
        return _schedule_row_to_dict(row) if row is not None else None


def add_schedule(
    path: Path,
    *,
    name: str,
    cron_expr: str,
    target_kind: str,
    target_label: str,
    timezone_name: str = "UTC",
    args_json: str | dict | list | None = "{}",
    enabled: bool = True,
    next_run_at: str = "",
) -> dict:
    """Insert a new schedule row and return it."""
    name = (name or "").strip()
    if not name:
        raise ValueError("schedule name is required")
    if target_kind not in SCHEDULE_TARGET_KINDS:
        raise ValueError(f"target_kind must be one of {', '.join(SCHEDULE_TARGET_KINDS)}")
    target_label = (target_label or "").strip()
    if not target_label:
        raise ValueError("target_label is required")
    norm_args = _normalize_schedule_args(args_json)
    now = datetime.now(timezone.utc).isoformat()
    init_db(path)
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "INSERT INTO schedules "
                "(name, cron_expr, timezone, target_kind, target_label, args_json, "
                "enabled, last_run_at, next_run_at, last_run_id, lease_until, created_at, modified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, NULL, '', ?, ?)",
                (
                    name,
                    cron_expr.strip(),
                    (timezone_name or "UTC").strip(),
                    target_kind,
                    target_label,
                    norm_args,
                    1 if enabled else 0,
                    next_run_at or "",
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"schedule {name!r} already exists") from e
        row = conn.execute("SELECT * FROM schedules WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _schedule_row_to_dict(row)


def update_schedule(path: Path, schedule_id: int | str, **fields) -> dict | None:
    """Update mutable fields on a schedule; returns updated dict or None if not found."""
    existing = get_schedule(path, schedule_id)
    if existing is None:
        return None
    allowed = {
        "name",
        "cron_expr",
        "timezone",
        "target_kind",
        "target_label",
        "args_json",
        "enabled",
        "last_run_at",
        "next_run_at",
        "last_run_id",
        "lease_until",
    }
    updates: list[str] = []
    params: list = []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "target_kind" and v not in SCHEDULE_TARGET_KINDS:
            raise ValueError(f"target_kind must be one of {', '.join(SCHEDULE_TARGET_KINDS)}")
        if k == "args_json":
            v = _normalize_schedule_args(v)
        elif k == "enabled":
            v = 1 if bool(v) else 0
        elif isinstance(v, str) and k in ("name", "cron_expr", "timezone", "target_label"):
            v = v.strip()
        updates.append(f"{k} = ?")
        params.append(v)
    if not updates:
        return existing
    updates.append("modified_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(existing["id"])
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"UPDATE schedules SET {', '.join(updates)} WHERE id = ?", params)
        except sqlite3.IntegrityError as e:
            raise ValueError(f"schedule name conflict: {e}") from e
        row = conn.execute("SELECT * FROM schedules WHERE id = ?", (existing["id"],)).fetchone()
    return _schedule_row_to_dict(row) if row is not None else None


def remove_schedule(path: Path, schedule_id: int | str) -> bool:
    """Delete a schedule by id or name; True if a row was deleted."""
    existing = get_schedule(path, schedule_id)
    if existing is None:
        return False
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM schedules WHERE id = ?", (existing["id"],))
        return cur.rowcount > 0


def claim_due_schedules(
    path: Path,
    *,
    now_iso: str,
    lease_until_iso: str,
) -> list[dict]:
    """Atomically claim enabled schedules due at or before *now_iso* whose lease has expired."""
    if not Path(path).exists():
        return []
    init_db(path)
    conn = connect(path)
    conn.isolation_level = None  # explicit transaction for BEGIN IMMEDIATE
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM schedules "
            "WHERE enabled = 1 AND next_run_at != '' AND next_run_at <= ? "
            "AND (lease_until = '' OR lease_until <= ?) "
            "ORDER BY next_run_at ASC, id ASC",
            (now_iso, now_iso),
        ).fetchall()
        claimed: list[dict] = []
        for r in rows:
            conn.execute(
                "UPDATE schedules SET lease_until = ?, modified_at = ? WHERE id = ?",
                (lease_until_iso, now_iso, r["id"]),
            )
            d = _schedule_row_to_dict(r)
            d["lease_until"] = lease_until_iso
            claimed.append(d)
        conn.execute("COMMIT")
        return claimed
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def record_schedule_run(
    path: Path,
    schedule_id: int,
    *,
    last_run_at: str,
    next_run_at: str,
    last_run_id: int | None = None,
) -> dict | None:
    """Record a completed/dispatched schedule run and release its lease."""
    if not Path(path).exists():
        return None
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE schedules SET last_run_at = ?, next_run_at = ?, "
            "last_run_id = COALESCE(?, last_run_id), lease_until = '', modified_at = ? "
            "WHERE id = ?",
            (last_run_at, next_run_at, last_run_id, now, int(schedule_id)),
        )
        row = conn.execute("SELECT * FROM schedules WHERE id = ?", (int(schedule_id),)).fetchone()
    return _schedule_row_to_dict(row) if row is not None else None
