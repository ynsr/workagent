"""SQLite repository: schema v1, sessions, runs."""

import re

from harness import store_sqlite as sq


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
        conn.execute("INSERT INTO repos (key_ref, path) VALUES ('r', '/r')")
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
        conn.execute("INSERT INTO repos (key_ref, path) VALUES ('r', '/r')")
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
    from harness.errors import HarnessError
    with pytest.raises(HarnessError):
        sq.migrate_json(cfg, cfg / "state.db")
    assert (cfg / "links.json").exists()  # left in place
