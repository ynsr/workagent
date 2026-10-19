"""Parent/child worktree columns: migration defaults + row roundtrip."""
import sqlite3

from workagent import store
from workagent.store_sqlite import _schema_current, init_db


def _raw_cols(path):
    conn = sqlite3.connect(str(path))
    try:
        return {r[1]: r[2] for r in conn.execute("PRAGMA table_info(worktrees)")}
    finally:
        conn.close()


def test_migration_adds_columns_with_defaults(isolated_config, tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE worktrees (ref_key TEXT PRIMARY KEY NOT NULL,"
                 " path TEXT UNIQUE NOT NULL, branch TEXT NOT NULL,"
                 " repo_key TEXT NOT NULL, issue_url TEXT NOT NULL DEFAULT '',"
                 " pr_url TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL,"
                 " payload TEXT NOT NULL DEFAULT '{}',"
                 " active INTEGER NOT NULL DEFAULT 1,"
                 " deleted_at TEXT NOT NULL DEFAULT '',"
                 " UNIQUE (repo_key, branch))")
    conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                 " VALUES ('jira:IPG-1', '/tmp/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    assert _schema_current(db) is False
    init_db(db)
    cols = _raw_cols(db)
    assert cols["related_repos"] == "TEXT"
    assert cols["parent_key_ref"] == "TEXT"
    assert _schema_current(db) is True


def test_roundtrip_parent_and_child(isolated_config):
    store.record_link("jira:IPG-1@main", {"issue": "IPG-1", "worktree": "/tmp/wt1",
                                          "branch": "feat/x", "repo": "/tmp/main",
                                          "related_repos": ["/tmp/other"]})
    store.record_link("jira:IPG-1@other", {"issue": "IPG-1", "worktree": "/tmp/wt2",
                                           "branch": "feat/x", "repo": "/tmp/other",
                                           "parent_key_ref": "jira:IPG-1@main"})
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert links["jira:IPG-1@main"]["related_repos"] == ["/tmp/other"]
    assert links["jira:IPG-1@main"]["parent_key_ref"] == ""
    assert links["jira:IPG-1@other"]["parent_key_ref"] == "jira:IPG-1@main"
    assert links["jira:IPG-1@other"]["related_repos"] == []
