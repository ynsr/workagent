"""Prodtest persistent state + GC exemption + session types (Task 3)."""
from __future__ import annotations


def test_gc_refuses_prodtest_pending(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-1", status="pending_prodtest")
    assert sq.gc_blocked_by_prodtest(db, "IPG-1") is True
    sq.set_prodtest_status(db, "IPG-1", "passed")
    assert sq.gc_blocked_by_prodtest(db, "IPG-1") is False


def test_upsert_get_roundtrip(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    assert sq.get_prodtest(db, "IPG-9") is None
    sq.upsert_prodtest(db, "IPG-9", status="running", plan_path="p.yaml",
                       plan_revision=3)
    row = sq.get_prodtest(db, "IPG-9")
    assert row["status"] == "running"
    assert row["plan_path"] == "p.yaml"
    assert row["plan_revision"] == 3


def test_set_status_rejects_unknown(tmp_path):
    import pytest
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-2", status="pending")
    with pytest.raises(ValueError):
        sq.set_prodtest_status(db, "IPG-2", "nope")


def test_derive_session_type_prodtest():
    from workagent import store_sessions as ss
    assert "prodtest_plan" in ss.SESSION_TYPES
    assert "prodtest_run" in ss.SESSION_TYPES
    assert ss.derive_session_type("prodtest plan --issue IPG-1") == "prodtest_plan"
    assert ss.derive_session_type("prodtest run --issue IPG-1") == "prodtest_run"


def test_gc_preserves_prodtest_blocked_row(tmp_path, monkeypatch):
    """Issue 1 regression: store-level GC must not delete blocked rows."""
    import sqlite3
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
        ("r9", "/tmp/r", "r"))
    conn.execute(
        "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at,"
        " deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("jira:IPG-9", "/tmp/w", "feat/9", "r9", "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00"))
    conn.commit()
    conn.close()
    sq.upsert_prodtest(db, "IPG-9", status="running")
    expired = sq.gc_soft_deleted(db, retention_days=-1)
    assert expired == [], expired  # blocked row never deleted
    with sqlite3.connect(str(db)) as c:
        assert c.execute("SELECT ref_key FROM worktrees").fetchall() == [
            ("jira:IPG-9",)]
    sq.set_prodtest_status(db, "IPG-9", "passed")
    expired = sq.gc_soft_deleted(db, retention_days=-1)
    assert [r["ref_key"] for r in expired] == ["jira:IPG-9"]


def test_upsert_prodtest_no_fields(tmp_path):
    """Issue 2 regression: bare upsert yields a pending row, not SQL error."""
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    row = sq.upsert_prodtest(db, "IPG-10")
    assert row["issue_key"] == "IPG-10"
    assert row["status"] == "pending"


def test_cli_destroy_refuses_prodtest_blocked(tmp_path, monkeypatch, isolated_config):
    import workagent.cli_cleanup as cc
    from workagent import store
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    repo.mkdir()
    wt.mkdir()
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "branch": "feat/9",
                                     "repo": str(repo), "worktree": str(wt)})
    sq.upsert_prodtest(db, "IPG-9", status="pending_prodtest")
    store.soft_delete_worktree("jira:IPG-9")
    monkeypatch.setattr(cc.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"ok": True})
    import workagent.cli as _cli
    monkeypatch.setattr(_cli, "run_cmd", lambda *a, **k: "")
    assert cc.gc_soft_deleted(retention_days=-1, dry_run=True) == [
        {"key": "jira:IPG-9", "branch": "feat/9",
         "status": "skipped:prodtest-pending"}]
    assert "jira:IPG-9" in store.load_links(True, True)
    assert cc.destroy_deleted(["jira:IPG-9"], dry_run=True) == [
        {"key": "jira:IPG-9", "branch": "feat/9",
         "status": "skipped:prodtest-pending"}]
    assert "jira:IPG-9" in store.load_links(True, True)
