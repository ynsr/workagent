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
