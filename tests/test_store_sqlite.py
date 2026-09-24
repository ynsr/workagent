"""SQLite repository: schema v1, sessions, runs."""

import re

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
        conn.execute("INSERT INTO trackers (key_ref) VALUES ('t')")
        conn.execute("INSERT INTO repos (key_ref, path, name, tracker_key) VALUES ('r', '/r', 'r', 't')")
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
        conn.execute("INSERT INTO trackers (key_ref) VALUES ('t')")
        conn.execute("INSERT INTO repos (key_ref, path, name, tracker_key) VALUES ('r', '/r', 'r', 't')")
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
    assert out["links"] == 0  # link pass only fires for unregistered tracker paths
    with sq.connect(db) as conn:
        row = conn.execute("SELECT tracker_key FROM repos WHERE key_ref = 'proj'").fetchone()
        assert row[0] == "jira:IPG"


def test_backfill_derives_tracker(tmp_path):
    """No explicit mapping → derive_tracker fills tracker_key (issue #26)."""
    db = tmp_path / "state.db"
    cfg = {"repos": {"r1": {"path": "/x/r1"}}, "trackers": {}}
    out = sq.backfill_trackers_repos(db, cfg, derive_tracker=lambda p: "github:o/r")
    assert out["repos"] == 1
    with sq.connect(db) as conn:
        row = conn.execute("SELECT tracker_key FROM repos WHERE key_ref = 'r1'").fetchone()
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
        conn.execute("INSERT INTO trackers (key_ref) VALUES ('jira:IPG')")
        conn.execute("INSERT INTO repos (key_ref, path, name, tracker_key)"
                     " VALUES ('proj', '/x/proj', 'proj', 'jira:IPG')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('jira:IPG-1', '/wt', 'b', 'proj')")
    assert sq.load_links_rows(db)["jira:IPG-1"]["repo"] == "/x/proj"
