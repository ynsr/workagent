"""SQLite repository: schema v1, sessions, runs."""

import re

import pytest

from workagent import store_sqlite as sq


def test_schema_tables_exist(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"trackers", "repos", "worktrees", "pr_cache",
            "sessions", "runs"} <= tables
def test_worktree_delete_cascades_sessions(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    sq.insert_session(db, worktree_ref="k", harness_name="omp",
                      initiator_command="start", prompt="p",
                      file_path="/tmp/x.jsonl")
    with sq.connect(db) as conn:
        conn.execute("DELETE FROM worktrees WHERE ref_key = 'k'")
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_finish_session_states(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp",
                            initiator_command="start", prompt="p",
                            file_path="/tmp/x.jsonl")
    sq.finish_session(db, sid, "finished")
    assert sq.get_session(db, sid)["state"] == "finished"


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
    # jira remote_url uses jira-cli's configured site when available.
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


def test_migrate_sessions_harness_renames_runtime_column(tmp_path):
    """Pre-rename DBs carry sessions.runtime_name; the harness rename must
    migrate it in place so insert_session (harness_name) keeps working."""
    import sqlite3
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("ALTER TABLE sessions RENAME COLUMN harness_name TO runtime_name")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k', '/tmp/w', 'b', NULL, '2026-01-01', '{}')")
        conn.execute("INSERT INTO sessions (id, worktree_ref, state, runtime_name,"
                     " initiator_command, prompt, file_path, created_at)"
                     " VALUES ('s1', 'k', 'running', 'omp', 'start', 'p', '/tmp/s.jsonl', '2026-01-01')")
    sq._INIT_CACHE.pop(str(db), None)
    sq.init_db(db)
    with sq.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert "harness_name" in cols
        assert "runtime_name" not in cols
        assert conn.execute("SELECT harness_name FROM sessions WHERE id='s1'").fetchone()[0] == "omp"
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp",
                            initiator_command="start", prompt="p",
                            file_path="/tmp/new.jsonl")
    assert sq.get_session(db, sid)["harness_name"] == "omp"


def test_init_db_repairs_legacy_runtime_column(tmp_path):
    """init_db on a DB whose sessions table still has runtime_name must end
    with a harness_name column and a passing _schema_current probe."""
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("ALTER TABLE sessions RENAME COLUMN harness_name TO runtime_name")
    sq._INIT_CACHE.pop(str(db), None)
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
