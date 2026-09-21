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
