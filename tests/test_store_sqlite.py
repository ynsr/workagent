"""SQLite repository: schema migrations, trackers, worktrees, runs."""

import re

import pytest

from workagent import store_sqlite as sq


def test_schema_tables_exist(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"trackers", "repos", "worktrees", "pr_cache", "runs"} <= tables
    # v16 folded the sessions table into runs: no session entity survives.
    assert "sessions" not in tables


_LEGACY_RUNS_DDL = ("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,"
                    " command TEXT NOT NULL, args TEXT NOT NULL, exit_code INTEGER,"
                    " created_at TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',"
                    " truncated INTEGER NOT NULL DEFAULT 0,"
                    " validation TEXT NOT NULL DEFAULT '{}', flow_id INTEGER)")


def _seed_legacy_sessions(db, rows: list[tuple], runs: list[tuple]) -> None:
    """Pre-v16 shape: sessions table + runs carrying the sessions FK.

    *rows* are (id, worktree_ref, state, harness_name, initiator_command,
    prompt, file_path, session_type); *runs* are (session_id, command,
    exit_code, output) in insertion order.
    """
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY NOT NULL,"
                     " worktree_ref TEXT, state TEXT NOT NULL,"
                     " harness_name TEXT NOT NULL DEFAULT '',"
                     " initiator_command TEXT NOT NULL DEFAULT '',"
                     " prompt TEXT NOT NULL DEFAULT '',"
                     " file_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,"
                     " session_type TEXT, metadata TEXT NOT NULL DEFAULT '{}',"
                     " flow_id INTEGER)")
        conn.execute(_LEGACY_RUNS_DDL)
        for r in rows:
            conn.execute("INSERT INTO sessions (id, worktree_ref, state, harness_name,"
                         " initiator_command, prompt, file_path, session_type,"
                         " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01')", r)
        for sid, command, exit_code, output in runs:
            conn.execute("INSERT INTO runs (session_id, command, args, exit_code,"
                         " created_at, output) VALUES (?, ?, '[]', ?, '2026-01-01', ?)",
                         (sid, command, exit_code, output))
    sq._INIT_CACHE.pop(str(db), None)


def test_migrate_v16_folds_sessions_into_runs(tmp_path):
    """v16: transcript columns move onto runs, sessions is dropped, chained
    runs link back to the transcript's first run, transcripts move
    sessions/ → runs/ (old stored paths still resolve)."""
    db = tmp_path / "state.db"
    transcript = tmp_path / "sessions" / "omp" / "feat-x" / "review" / "t-1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    _seed_legacy_sessions(db, [("t-1", "", "finished", "omp", "review",
                               "review this PR", str(transcript), "review")],
                          [("t-1", "review", 0, "harness command: omp review"),
                           ("t-1", "review", 0, "review log")])

    sq.init_db(db)

    import sqlite3
    tables = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sessions" not in tables
    with sq.connect(db) as conn:
        rows = [dict(zip(("id", "file_path", "session_type", "harness_name",
                          "initiator_command", "prompt", "state",
                          "continued_from_run_id"), r))
                for r in conn.execute(
                    "SELECT id, file_path, session_type, harness_name,"
                    " initiator_command, prompt, state, continued_from_run_id"
                    " FROM runs ORDER BY id")]
    assert [r["id"] for r in rows] == [1, 2]
    for r in rows:
        assert r["file_path"].endswith("t-1.jsonl")
        assert r["session_type"] == "review"
        assert r["harness_name"] == "omp"
        assert r["initiator_command"] == "review"
        assert r["prompt"] == "review this PR"
    assert rows[0]["state"] == "preview"     # preview rows stay preview
    assert rows[1]["state"] == "finished"    # execution row takes the session state
    assert rows[0]["continued_from_run_id"] is None
    assert rows[1]["continued_from_run_id"] == 1  # later run → transcript's first run

    # The transcript tree moved, the stored path was rewritten to runs/…
    moved = tmp_path / "runs" / "omp" / "feat-x" / "review" / "t-1.jsonl"
    assert moved.is_file() and not transcript.exists()
    assert rows[1]["file_path"] == str(moved)
    # Old-style stored paths (pre-migration rows/backups) still resolve.
    assert sq.resolve_transcript_path(str(transcript), tmp_path) == moved
    legacy_only = tmp_path / "sessions" / "omp" / "old" / "u.jsonl"
    legacy_only.parent.mkdir(parents=True)
    legacy_only.write_text("{}\n")
    assert sq.resolve_transcript_path(
        str(tmp_path / "runs" / "omp" / "old" / "u.jsonl"), tmp_path) == legacy_only
    # An empty stored path (runs.file_path default) never resolves to the CWD.
    assert sq.resolve_transcript_path("", tmp_path) is None

    # list_runs reads transcript columns straight off the run row.
    listed = sq.list_runs(db)
    assert {r["session_type"] for r in listed} == {"review"}
    assert all(r["file_path"].endswith(".jsonl") for r in listed)


def test_migrate_v16_keeps_execution_run_state(tmp_path):
    """Real executions also start with the ``harness command:`` banner but
    carry a second output line — the fold must not stamp them preview."""
    db = tmp_path / "state.db"
    _seed_legacy_sessions(
        db, [("t-1", "", "finished", "omp", "review", "p", "", "review")],
        [("t-1", "review", 0, "harness command: omp review --session-dir x"),
         ("t-1", "review", 1,
          "harness command: omp review --session-dir x\nreview log")])
    sq.init_db(db)
    with sq.connect(db) as conn:
        states = [r[0] for r in conn.execute("SELECT state FROM runs ORDER BY id")]
    assert states == ["preview", "finished"]


def test_init_db_is_idempotent_post_fold(tmp_path):
    """A folded DB probes current and keeps its run transcript columns."""
    db = tmp_path / "state.db"
    sq.init_db(db)
    assert sq._schema_current(db)
    sq._INIT_CACHE.pop(str(db), None)
    sq.init_db(db)
    with sq.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert {"worktree_ref", "harness_name", "initiator_command", "prompt",
            "file_path", "session_type", "metadata", "state",
            "continued_from_run_id"} <= cols


def test_session_id_shape():
    sid = sq.gen_session_id()
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z-\d{4}", sid)


def test_migrate_copies_and_deletes(tmp_path):
    import json
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "links.json").write_text(json.dumps({
        "jira:IPG-1": {"worktree": "/wt", "branch": "feat/1",
                       "repo": "/r", "issue_url": "http://j/1"}}))
    (cfg / "pr_cache.json").write_text(json.dumps({
        "feat/1": {"pr": {"number": 1}, "checked_at": "2026-09-20"}}))
    (cfg / "harnesses.json").write_text(json.dumps({}))
    (cfg / "config.json").write_text(json.dumps(
        {"repos": {"r": {"path": "/r"}}}))
    out = sq.migrate_json(cfg, cfg / "state.db")
    assert out["worktrees"] == 1 and out["pr_cache"] == 1
    assert not (cfg / "links.json").exists()
    assert (cfg / "config.json").exists()


def test_migrate_corrupt_aborts(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "links.json").write_text("{broken")
    import pytest
    from workagent.errors import HarnessError
    with pytest.raises(HarnessError):
        sq.migrate_json(cfg, cfg / "state.db")
    assert (cfg / "links.json").exists()  # left in place


def test_issue_cache_roundtrip(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    assert sq.get_issue_cache(db, "jira") == ([], None)
    rows = [{"key": "IPG-1", "title": "t", "url": "u", "status": "To Do",
             "created": "2026-09-01"}]
    sq.set_issue_cache(db, "jira", rows)
    got, fetched_at = sq.get_issue_cache(db, "jira")
    assert got == rows
    assert fetched_at is not None


def test_backfill_maps_explicit_tracker(tmp_path):
    """backfill_trackers_repos: explicit config.json mapping wins (issue #26)."""
    db = tmp_path / "state.db"
    cfg = {"repos": {"proj": {"path": "/x/proj"}},
           "trackers": {"jira:IPG": {"repos": ["/x/proj"]}}}
    out = sq.backfill_trackers_repos(db, cfg)
    assert out["trackers"] == 1 and out["repos"] == 1
    assert out["links"] == 1  # repo loop writes the join link directly
    with sq.connect(db) as conn:
        row = conn.execute("SELECT repo_key FROM tracker_repos"
                           " WHERE tracker_key = 'jira:IPG'").fetchone()
        assert row[0] == "proj"


def test_backfill_derives_tracker(tmp_path):
    """No explicit mapping → derive_tracker writes the join link (issue #26)."""
    db = tmp_path / "state.db"
    cfg = {"repos": {"r1": {"path": "/x/r1"}}, "trackers": {}}
    out = sq.backfill_trackers_repos(db, cfg, derive_tracker=lambda p: "github:o/r")
    assert out["repos"] == 1
    with sq.connect(db) as conn:
        row = conn.execute("SELECT tracker_key FROM tracker_repos"
                           " WHERE repo_key = 'r1'").fetchone()
        assert row[0] == "github:o/r"


def test_backfill_missing_tracker_raises(tmp_path):
    """No mapping and no derivation → ValueError naming the repo."""
    import pytest
    db = tmp_path / "state.db"
    cfg = {"repos": {"r1": {"path": "/x/r1"}}, "trackers": {}}
    with pytest.raises(ValueError, match="r1"):
        sq.backfill_trackers_repos(db, cfg)


def test_load_links_returns_repo_path(tmp_path):
    """load_links_rows resolves entry['repo'] to the registry path, not key_ref."""
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('jira:IPG', 'jira', 'jira:IPG')")
        conn.execute("INSERT INTO repos (key_ref, path, name)"
                     " VALUES ('proj', '/x/proj', 'proj')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key)"
                     " VALUES ('jira:IPG', 'proj')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('jira:IPG-1', '/wt', 'b', 'proj', '2026-01-01T00:00:00+00:00')")
    assert sq.load_links_rows(db)["jira:IPG-1"]["repo"] == "/x/proj"


def test_backfill_repairs_null_legacy_rows(tmp_path):
    """Legacy repos rows (path-keyed, tracker_key NULL — pre-#26 schema)
    are repaired in place: key_ref preserved, tracker filled, link written."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trackers (key_ref TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE repos (key_ref TEXT PRIMARY KEY,"
                     " path TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',"
                     " tracker_key TEXT, remote TEXT NOT NULL DEFAULT '',"
                     " tool TEXT NOT NULL DEFAULT '')")
        conn.execute("CREATE TABLE tracker_repos (tracker_key TEXT NOT NULL,"
                     " repo_key TEXT NOT NULL,"
                     " PRIMARY KEY (tracker_key, repo_key))")
        conn.execute("INSERT INTO repos (key_ref, path, name, tracker_key)"
                     " VALUES ('/x/proj', '/x/proj', 'proj', NULL)")
    cfg = {"repos": {"proj": {"path": "/x/proj"}},
           "trackers": {"jira:IPG": {"repos": ["/x/proj"]}}}
    sq.backfill_trackers_repos(db, cfg)
    with sq.connect(db) as conn:
        row = conn.execute("SELECT key_ref FROM repos").fetchone()
        assert row[0] == "/x/proj"
        assert conn.execute("SELECT COUNT(*) FROM trackers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tracker_repos").fetchone()[0] == 1


def test_normalize_vendor_enum():
    """Vendor is a closed enum: jira/github accepted (any case), rest rejected."""
    assert sq.normalize_vendor("") == ""
    assert sq.normalize_vendor("JIRA") == "jira"
    assert sq.normalize_vendor(" github ") == "github"
    assert sq.normalize_vendor("gitlab") == "github"  # legacy remap
    with pytest.raises(ValueError, match="must be one of jira, github"):
        sq.normalize_vendor("Custom")


def test_tracker_meta_derivation():
    """_tracker_meta derives mandatory vendor/remote_url from every key shape."""
    assert sq._tracker_meta("github:o/r") == ("github", "https://github.com/o/r")
    assert sq._tracker_meta("gitlab:h/g/r") == ("github", "https://h/g/r")
    # jira remote_url uses jira's configured site when available.
    vendor, url = sq._tracker_meta("jira:IPG")
    assert vendor == "jira" and (url == "jira:IPG" or url.endswith("/browse/IPG"))
    assert sq._tracker_meta("weird") == ("github", "weird")


def test_upsert_tracker_preserves_vendor_on_blank_ensure(tmp_path):
    """Blank ensure-upserts never clobber an explicitly-set enum vendor."""
    db = tmp_path / "state.db"
    sq.upsert_tracker(db, "jira:IPG", vendor="github", remote_url="https://x")
    sq.add_tracker_repo(db, "jira:IPG", "/r")
    row = sq.load_tracker_rows(db)[0]
    assert (row["vendor"], row["remote_url"]) == ("github", "https://x")


def test_upsert_tracker_rejects_non_enum_vendor(tmp_path):
    """upsert_tracker enforces the enum at the store boundary too."""
    import pytest as _pytest
    db = tmp_path / "state.db"
    with _pytest.raises(ValueError, match="must be one of"):
        sq.upsert_tracker(db, "jira:IPG", vendor="Custom")


def test_migrate_v2_repairs_non_enum_vendor(tmp_path):
    """Legacy rows carrying off-enum vendors are remapped into the enum."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trackers (key_ref TEXT PRIMARY KEY,"
                     " vendor TEXT NOT NULL DEFAULT '',"
                     " remote_url TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO trackers VALUES ('jira:X', 'unknown', 'jira:X')")
        conn.execute("INSERT INTO trackers VALUES ('github:o/r', 'gitlab', 'https://h/g/r')")
    sq.init_db(db)
    with sq.connect(db) as conn:
        rows = dict(conn.execute("SELECT key_ref, vendor FROM trackers"))
    assert rows == {"jira:X": "jira", "github:o/r": "github"}


def test_migrate_v2_repairs_legacy_trackers_and_drops_column(tmp_path):
    """Legacy DB: vendor/remote_url backfilled, tracker_key links seeded, column dropped."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trackers (key_ref TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE repos (key_ref TEXT PRIMARY KEY,"
                     " path TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',"
                     " tracker_key TEXT, remote TEXT NOT NULL DEFAULT '',"
                     " tool TEXT NOT NULL DEFAULT '')")
        conn.execute("CREATE TABLE tracker_repos (tracker_key TEXT NOT NULL,"
                     " repo_key TEXT NOT NULL,"
                     " PRIMARY KEY (tracker_key, repo_key))")
        conn.execute("INSERT INTO trackers (key_ref) VALUES ('github:o/r')")
        conn.execute("INSERT INTO repos (key_ref, path, name, tracker_key)"
                     " VALUES ('r', '/r', 'r', 'github:o/r')")
    sq.init_db(db)
    with sq.connect(db) as conn:
        t = conn.execute("SELECT vendor, remote_url FROM trackers").fetchone()
        assert t[0] == "github" and t[1] == "https://github.com/o/r"
        assert conn.execute("SELECT COUNT(*) FROM tracker_repos").fetchone()[0] == 1
        cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
        assert "tracker_key" not in cols

def test_remove_repo_row_cascades_and_unlinked_register_keeps_row(tmp_path):
    """remove_repo_row cascades joins/worktrees; register_repo_row_unlinked
    must only refresh remote/tool — never delete the repo row."""
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "proj", "/proj", "jira:IPG")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'proj', '2026-01-01T00:00:00+00:00')")
    assert sq.remove_repo_row(db, "proj") is True
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tracker_repos").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 0
    # re-register, then the unlinked refresh keeps the row
    sq.register_repo_row(db, "proj", "/proj", "jira:IPG")
    sq.register_repo_row_unlinked(db, "proj", "/proj", remote="https://x/y", tool="gh")
    with sq.connect(db) as conn:
        row = conn.execute("SELECT remote, tool FROM repos WHERE key_ref = 'proj'").fetchone()
        assert row == ("https://x/y", "gh")
    assert sq.remove_repo_row(db, "missing") is False

def test_migrate_worktrees_branch_scope_allows_same_branch_per_repo(tmp_path):
    """Legacy UNIQUE(branch) → UNIQUE(repo_key, branch): same branch in two repos."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE repos (key_ref TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,"
                     " name TEXT NOT NULL DEFAULT '', remote TEXT NOT NULL DEFAULT '',"
                     " tool TEXT NOT NULL DEFAULT '')")
        conn.execute("CREATE TABLE worktrees (ref_key TEXT PRIMARY KEY NOT NULL,"
                     " path TEXT UNIQUE NOT NULL, branch TEXT UNIQUE NOT NULL,"
                     " repo_key TEXT, issue_url TEXT NOT NULL DEFAULT '',"
                     " pr_url TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL,"
                     " payload TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1)")
        conn.execute("INSERT INTO repos VALUES ('a', '/r/a', 'a', '', '')")
        conn.execute("INSERT INTO repos VALUES ('b', '/r/b', 'b', '', '')")
        conn.execute("INSERT INTO worktrees VALUES ('k1', '/wt/a', 'feat/X', 'a', '', '', '2026-01-01T00:00:00+00:00', '{}', 1)")
    sq.init_db(db)
    with sq.connect(db) as conn:
        idx_cols = [{row[2] for row in conn.execute(f'PRAGMA index_info("{r[1]}")')}
                    for r in conn.execute("PRAGMA index_list(worktrees)")]
        assert {"repo_key", "branch"} in idx_cols
        assert {"branch"} not in [c for c, r in
                                  zip(idx_cols, conn.execute("PRAGMA index_list(worktrees)")) if r[2]]
        # second repo may now track the same branch name
        conn.execute("INSERT INTO worktrees VALUES ('k2', '/wt/b', 'feat/X', 'b', '', '', '2026-01-01T00:00:00+00:00', '{}', 1, '', '[]', '')")
        assert conn.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 2


def test_migrate_worktrees_branch_scope_idempotent(tmp_path):
    """Second init_db is a no-op (rows + composite index survive)."""
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.init_db(db)
    with sq.connect(db) as conn:
        idx_cols = [{row[2] for row in conn.execute(f'PRAGMA index_info("{r[1]}")')}
                    for r in conn.execute("PRAGMA index_list(worktrees)")]
        assert {"repo_key", "branch"} in idx_cols


def test_register_repo_row_same_path_rename_rekeys_children(tmp_path):
    """Issue #30: same-path re-register under a new name re-keys joins +
    worktrees instead of dying on the FK constraint."""
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "proj", "/proj", "jira:IPG")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'proj', '2026-01-01T00:00:00+00:00')")
    sq.register_repo_row(db, "proj-renamed", "/proj", "jira:IPG")
    with sq.connect(db) as conn:
        assert conn.execute("SELECT key_ref FROM repos").fetchall() == [("proj-renamed",)]
        assert conn.execute("SELECT repo_key FROM tracker_repos").fetchall() == [("proj-renamed",)]
        assert conn.execute("SELECT repo_key FROM worktrees").fetchall() == [("proj-renamed",)]

def test_cache_review_stats_roundtrip(tmp_path, monkeypatch, isolated_config):
    """Issue #28: cache_review_stats persists reviews/unresolved/resolved."""
    from workagent import store
    monkeypatch.setattr(store, "_sqlite_path", lambda: tmp_path / "state.db")
    store.cache_review_stats("feat/x", {"reviews": 2, "unresolved": 1, "resolved": 3},
                             sha="abc")
    cached = store.load_pr_cache()["feat/x"]
    assert (cached["reviews"], cached["unresolved"], cached["resolved"]) == (2, 1, 3)
    assert cached["reviews_sha"] == "abc"
    assert "reviews_checked_at" in cached
    store.cache_review_stats("feat/x", None)
    assert store.load_pr_cache()["feat/x"]["reviews"] == 2


def test_worktree_count_for_repo(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "proj", "/proj", "jira:IPG")
    assert sq.worktree_count_for_repo(db, "proj") == 0
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'proj', '2026-01-01T00:00:00+00:00')")
    assert sq.worktree_count_for_repo(db, "proj") == 1


def test_migrate_worktrees_active_defaults_to_active(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('k1', '/tmp/w1', 'b1', NULL, '2026-01-01', '{}')")
    sq._migrate_worktrees_active(sq.connect(db))
    with sq.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
        assert "active" in cols
        assert conn.execute("SELECT active FROM worktrees WHERE ref_key='k1'").fetchone()[0] == 1


def test_migrate_worktrees_active_idempotent(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        sq._migrate_worktrees_active(conn)
        sq._migrate_worktrees_active(conn)
        with conn:
            conn.execute("UPDATE worktrees SET active=1 WHERE 0")



def test_migrate_v16_folds_pre_rename_runtime_column(tmp_path):
    """Pre-rename DBs carry sessions.runtime_name: the fold must land it on
    runs.harness_name instead of dropping the harness name."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY NOT NULL,"
                     " worktree_ref TEXT, state TEXT NOT NULL,"
                     " runtime_name TEXT NOT NULL DEFAULT '',"
                     " initiator_command TEXT NOT NULL DEFAULT '',"
                     " prompt TEXT NOT NULL DEFAULT '',"
                     " file_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,"
                     " session_type TEXT, metadata TEXT NOT NULL DEFAULT '{}')")
        conn.execute(_LEGACY_RUNS_DDL)
        conn.execute("INSERT INTO sessions (id, state, runtime_name, initiator_command,"
                     " prompt, file_path, created_at)"
                     " VALUES ('s1', 'running', 'omp', 'start', 'p', '/tmp/s.jsonl', '2026-01-01')")
        conn.execute("INSERT INTO runs (session_id, command, args, created_at)"
                     " VALUES ('s1', 'start', '[]', '2026-01-01')")
    sq._INIT_CACHE.pop(str(db), None)
    sq.init_db(db)
    with sq.connect(db) as conn:
        assert conn.execute("SELECT harness_name FROM runs").fetchone()[0] == "omp"


def test_init_db_folds_legacy_schema(tmp_path):
    """init_db on a DB that still has sessions ends folded and current."""
    db = tmp_path / "state.db"
    _seed_legacy_sessions(db, [("s1", "", "finished", "omp", "start", "p", "", "start")],
                          [("s1", "start", 0, "log")])
    assert not sq._schema_current(db)
    sq.init_db(db)
    assert sq._schema_current(db)

def test_load_links_rows_filters_inactive(tmp_path):
    from workagent import store_sqlite as sq, store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload, active)"
            " VALUES ('a', '/tmp/wa', 'ba', NULL, '2026-01-01', '{}', 1),"
            " ('d', '/tmp/wd', 'bd', NULL, '2026-01-01', '{}', 0)")
    assert set(sl.load_links_rows(db).keys()) == {"a"}
    all_rows = sl.load_links_rows(db, include_inactive=True)
    assert set(all_rows.keys()) == {"a", "d"}
    assert all_rows["d"]["active"] == 0


def test_set_worktree_active_round_trip(tmp_path):
    from workagent import store_sqlite as sq, store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('k1', '/tmp/w1', 'b1', NULL, '2026-01-01', '{}')")
    sl.set_worktree_active(db, "k1", False)
    assert "k1" not in sl.load_links_rows(db)
    assert sl.load_links_rows(db, include_inactive=True)["k1"]["active"] == 0
    sl.set_worktree_active(db, "k1", True)
    assert sl.load_links_rows(db)["k1"]["active"] == 1


def test_delete_worktree_row_leaves_disk(tmp_path):
    from workagent import store_sqlite as sq, store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    wt = tmp_path / "wt1"
    wt.mkdir()
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('k1', ?, 'b1', NULL, '2026-01-01', '{}')", (str(wt),))
    sl.delete_worktree_row(db, "k1")
    assert wt.exists()
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM worktrees WHERE ref_key='k1'").fetchone()[0] == 0
    import pytest
    from workagent.errors import HarnessError
    with pytest.raises(HarnessError):
        sl.delete_worktree_row(db, "k1")


def test_save_links_rows_never_resets_active(tmp_path):
    from workagent import store_sqlite as sq, store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('k1', '/tmp/w1', 'b1', NULL, '2026-01-01', '{}')")
    sl.set_worktree_active(db, "k1", False)
    rows = sl.load_links_rows(db, include_inactive=True)
    assert rows["k1"]["active"] == 0
    sl.save_links_rows(db, rows)  # re-save must not resurrect
    assert "k1" not in sl.load_links_rows(db)
    assert sl.load_links_rows(db, include_inactive=True)["k1"]["active"] == 0

def test_migrate_v16_backfills_session_type_from_prompt(tmp_path):
    """Pre-v6 rows carry no session_type; the fix-comments prompt header is
    the only surviving discriminator, so the fold lands fix_comments."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY NOT NULL,"
                     " worktree_ref TEXT, state TEXT NOT NULL,"
                     " harness_name TEXT NOT NULL DEFAULT '',"
                     " initiator_command TEXT NOT NULL DEFAULT '',"
                     " prompt TEXT NOT NULL DEFAULT '',"
                     " file_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
        conn.execute(_LEGACY_RUNS_DDL)
        conn.execute("INSERT INTO sessions (id, state, harness_name, initiator_command,"
                     " prompt, file_path, created_at)"
                     " VALUES ('s1', 'finished', 'omp', 'omp', ?, '/f', '2026-01-01')",
                     ("Fix all open (not-resolved) review comments on this PR/MR: https://x/1",))
        conn.execute("INSERT INTO runs (session_id, command, args, created_at)"
                     " VALUES ('s1', 'omp', '[]', '2026-01-01')")
    sq._INIT_CACHE.pop(str(db), None)
    sq.init_db(db)
    with sq.connect(db) as conn:
        row = conn.execute("SELECT session_type, metadata FROM runs").fetchone()
        assert row[0] == "fix_comments"
        assert row[1] == "{}"


def test_derive_session_type_matrix():
    assert sq.derive_session_type("start", False) == "start"
    assert sq.derive_session_type("sync", False) == "sync"
    assert sq.derive_session_type("review", False) == "review"
    assert sq.derive_session_type("review", True) == "fix_comments"
    assert sq.derive_session_type("cleanup", False) is None
    assert sq.derive_session_type("", False) is None


def test_derive_session_type_adhoc_is_custom_action():
    assert sq.derive_session_type("adhoc", False) == "custom_action"


def _seed_link(db, key: str) -> None:
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES (?, ?, 'b', NULL, '2026-01-01', '{}')",
            (key, f"/tmp/{key}"))


def _run_row(db, ref: str = "") -> int:
    return sq.insert_run(db, "start", [], 0, worktree_ref=ref,
                         file_path=f"/f/{ref or 'repo'}.jsonl")


def test_latest_review_run_resolves_from_runs(tmp_path):
    """The ref's latest non-running review-kind run is the one to continue."""
    db = tmp_path / "state.db"
    sq.init_db(db)
    rid = sq.insert_run(db, "review", ["k"], 0, worktree_ref="k",
                        session_type="review", file_path="/tmp/x.jsonl")
    assert sq.latest_review_run(db, "k")["id"] == rid


def test_latest_review_run_skips_running_and_other_kinds(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    done = sq.insert_run(db, "review", ["k"], 0, worktree_ref="k",
                         session_type="review", file_path="/old.jsonl")
    sq.insert_run(db, "review", ["k"], None, worktree_ref="k",
                  session_type="review", file_path="/running.jsonl",
                  state="running")
    sq.insert_run(db, "start", ["k"], 0, worktree_ref="k",
                  session_type="start", file_path="/start.jsonl")
    sq.insert_run(db, "review", ["other"], 0, worktree_ref="other",
                  session_type="review", file_path="/other.jsonl")
    assert sq.latest_review_run(db, "k")["id"] == done
    assert sq.latest_review_run(db, "nope") is None


def test_run_metadata_merges(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    rid = sq.insert_run(db, "review", ["k"], 0, worktree_ref="k",
                        metadata={"flow_id": 7})
    out = sq.set_run_metadata(db, rid,
                              {"review_comments_fixed_at": "2026-09-26T00:00:00+00:00"})
    assert out == {"flow_id": 7,
                   "review_comments_fixed_at": "2026-09-26T00:00:00+00:00"}
    assert sq.get_run(db, rid)["metadata"]["review_comments_fixed_at"].startswith(
        "2026-09-26")
    with pytest.raises(KeyError):
        sq.set_run_metadata(db, 999, {"x": 1})


def test_insert_run_rejects_bad_type_and_state(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    with pytest.raises(ValueError):
        sq.insert_run(db, "x", [], 0, session_type="nonsense")
    with pytest.raises(ValueError):
        sq.insert_run(db, "x", [], 0, state="nonsense")
    assert sq.list_runs(db) == []


def test_finish_run_states(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    rid = sq.insert_run(db, "start", ["k"], None, state="running")
    assert sq.get_run(db, rid)["state"] == "running"
    sq.finish_run(db, rid, "finished")
    assert sq.get_run(db, rid)["state"] == "finished"
    sq.finish_run(db, rid, "failed")
    assert sq.get_run(db, rid)["state"] == "failed"
    with pytest.raises(AssertionError):
        sq.finish_run(db, rid, "nonsense")


def test_finish_run_preview_state(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    rid = sq.insert_run(db, "sync", ["k"], None, state="running")
    sq.finish_run(db, rid, "preview")
    assert sq.get_run(db, rid)["state"] == "preview"


def test_get_run_carries_transcript_identity(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    rid = sq.insert_run(db, "review", ["k"], 0, worktree_ref="k",
                        harness_name="omp", initiator_command="review",
                        prompt="p", file_path="/f/x.jsonl",
                        session_type="review", metadata={"a": 1},
                        validation={"attempt": 2})
    row = sq.get_run(db, rid)
    assert row["args"] == ["k"]
    assert row["validation"] == {"attempt": 2}
    assert row["metadata"] == {"a": 1}
    assert (row["harness_name"], row["initiator_command"], row["prompt"],
            row["file_path"], row["session_type"]) == (
        "omp", "review", "p", "/f/x.jsonl", "review")
    assert sq.get_run(db, 999) is None


def test_run_by_file_path_resolves_newest_and_rekeys(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.insert_run(db, "review", ["k"], 0, worktree_ref="k",
                  file_path="/f/t.jsonl")
    new = sq.insert_run(db, "review", ["k"], 0, worktree_ref="k",
                        file_path="/f/t.jsonl")
    assert sq.run_by_file_path(db, "/f/t.jsonl")["id"] == new
    sq.set_run_file_path(db, new, "/f/minted-9.jsonl")
    assert sq.run_by_file_path(db, "/f/minted-9.jsonl")["id"] == new
    assert sq.run_by_file_path(db, "") is None
    assert sq.run_by_file_path(db, "/f/nope.jsonl") is None


def test_list_runs_carries_transcript_columns(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    rid = sq.insert_run(db, "start", ["k"], 3, worktree_ref="k",
                        harness_name="omp", initiator_command="omp start",
                        prompt="do it", file_path="/f/s.jsonl",
                        session_type="start",
                        metadata={"branch_path": "feat-x"},
                        state="failed", output=["one", "two"])
    row = sq.list_runs(db)[0]
    assert row["id"] == rid
    assert row["exit_code"] == 3
    assert row["metadata"] == {"branch_path": "feat-x"}
    assert row["state"] == "failed"
    assert row["harness_name"] == "omp"
    assert row["initiator_command"] == "omp start"
    assert row["prompt"] == "do it"
    assert row["file_path"] == "/f/s.jsonl"
    assert row["session_type"] == "start"
    assert row["output"] == "one\ntwo"


def test_worktree_hard_delete_purges_its_runs(tmp_path):
    """runs.worktree_ref has no FK: the hard delete purges this ref's runs."""
    from workagent import store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    _seed_link(db, "k")
    _seed_link(db, "k2")
    _run_row(db, "k")
    other = _run_row(db, "k2")
    repo = _run_row(db, "")
    sl.delete_worktree_row(db, "k")
    ids = {r["id"] for r in sq.list_runs(db, include_deleted_worktrees=True)}
    assert ids == {other, repo}


def test_save_links_prune_purges_orphan_runs(tmp_path):
    """The links sweep drops runs of pruned worktrees, never repo-scoped rows."""
    from workagent import store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    _seed_link(db, "keep")
    _seed_link(db, "gone")
    kept = _run_row(db, "keep")
    _run_row(db, "gone")
    repo = _run_row(db, "")
    sl.save_links_rows(db, {"keep": {"worktree": "/tmp/keep", "branch": "b",
                                     "repo": "/tmp/rk"}})
    ids = {r["id"] for r in sq.list_runs(db, include_deleted_worktrees=True)}
    assert ids == {kept, repo}
    sl.save_links_rows(db, {})
    assert [r["id"] for r in sq.list_runs(db, include_deleted_worktrees=True)] == [repo]


def test_gc_soft_deleted_purges_runs(tmp_path):
    from workagent import store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    _seed_link(db, "k")
    _run_row(db, "k")
    repo = _run_row(db, "")
    with sq.connect(db) as conn:
        conn.execute("UPDATE worktrees SET deleted_at = '2020-01-01T00:00:00+00:00'"
                     " WHERE ref_key = 'k'")
    assert [r["ref_key"] for r in sl.gc_soft_deleted(db)] == ["k"]
    ids = {r["id"] for r in sq.list_runs(db, include_deleted_worktrees=True)}
    assert ids == {repo}


def test_repos_name_unique_fresh_db(tmp_path):
    """Issue #54: fresh DBs enforce a UNIQUE name on repos (second name dies)."""
    import sqlite3
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('a', '/r/a', 'dup')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('b', '/r/b', 'dup')")


def test_migrate_repos_name_unique_dedupes_legacy_rows(tmp_path):
    """Issue #54: legacy DBs with duplicate repo names migrate (first wins,
    later rows renamed name-2/name-3) and gain the UNIQUE index."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE repos (key_ref TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,"
                     " name TEXT NOT NULL DEFAULT '', remote TEXT NOT NULL DEFAULT '',"
                     " tool TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO repos VALUES ('a', '/r/a', 'dup', '', '')")
        conn.execute("INSERT INTO repos VALUES ('b', '/r/b', 'dup', '', '')")
    sq.init_db(db)
    with sq.connect(db) as conn:
        names = sorted(r[0] for r in conn.execute("SELECT name FROM repos"))
        assert names == ["dup", "dup-2"]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('c', '/r/c', 'dup')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('d', '/r/d', 'dup-2')")

def test_register_repo_row_rejects_name_collision(tmp_path):
    """Issue #54: registering a taken name for another path fails fast."""
    from workagent.errors import HarnessError
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "proj", "/proj", "jira:IPG")
    with pytest.raises(HarnessError, match="already registered"):
        sq.register_repo_row(db, "proj", "/other", "jira:IPG")


def test_register_repo_row_unlinked_rejects_taken_name(tmp_path):
    """Issue #54/PR #56: unlinked refresh raises on a taken name (no silent re-key)."""
    from workagent.errors import HarnessError
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "proj", "/proj", "jira:IPG")
    sq.register_repo_row(db, "other", "/other", "jira:IPG")
    with pytest.raises(HarnessError, match="already registered"):
        sq.register_repo_row_unlinked(db, "proj", "/other")
    with sq.connect(db) as conn:
        rows = {r[0]: r[1] for r in conn.execute("SELECT key_ref, name FROM repos")}
        assert rows == {"proj": "proj", "other": "other"}


def test_migrate_repos_unique_name_keeps_unique_padded_name(tmp_path):
    """Issue #54/PR #56: migration rewrites only colliding/empty names."""
    import sqlite3
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE repos (key_ref TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,"
                     " name TEXT NOT NULL, remote TEXT, tool TEXT)")
        conn.execute("INSERT INTO repos VALUES ('a', '/r/a', '  spaced  ', '', '')")
        conn.execute("INSERT INTO repos VALUES ('b', '/r/b', 'dup', '', '')")
        conn.execute("INSERT INTO repos VALUES ('c', '/r/c', 'dup', '', '')")
    sq.init_db(db)
    with sq.connect(db) as conn:
        names = {r[0]: r[1] for r in conn.execute("SELECT key_ref, name FROM repos")}
        assert names["a"] == "  spaced  "
        assert sorted([names["b"], names["c"]]) == ["dup", "dup-2"]
