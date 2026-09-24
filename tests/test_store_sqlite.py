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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('k', '/wt', 'b', 'r')")
    sq.insert_session(db, worktree_ref="k", runtime_name="omp",
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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('k', '/wt', 'b', 'r')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('jira:IPG-1', '/wt', 'b', 'proj')")
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
