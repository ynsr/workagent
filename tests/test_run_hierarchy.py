"""Tests for parent_run_id & step_index run hierarchy (issue #143 / T06)."""
from __future__ import annotations

import os
from pathlib import Path

from workagent import store_sqlite as sq
from workagent import web_runs as wr


def test_runs_parent_migration_and_on_delete_set_null(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    sq.init_db(db)
    parent_id = sq.insert_run(db, "flow", ["run", "ship", "IPG-1"], 0, state="finished")
    child_id = sq.insert_run(
        db, "start", ["IPG-1"], 0, state="finished",
        parent_run_id=parent_id, step_index=0,
    )
    row = sq.get_run(db, child_id)
    assert row is not None
    assert row["parent_run_id"] == parent_id
    assert row["step_index"] == 0

    listed = {r["id"]: r for r in sq.list_runs(db)}
    assert listed[child_id]["parent_run_id"] == parent_id
    assert listed[child_id]["step_index"] == 0

    # ON DELETE SET NULL preserves child when parent is deleted
    with sq.connect(db) as conn:
        conn.execute("DELETE FROM runs WHERE id = ?", (parent_id,))
    after = sq.get_run(db, child_id)
    assert after is not None
    assert after["parent_run_id"] is None


def test_insert_run_reads_parent_env_and_ignores_missing_parent(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "state.db"
    sq.init_db(db)
    parent_id = sq.insert_run(db, "flow", ["run", "ship", "IPG-1"], None, state="running")
    monkeypatch.setenv("WORKAGENT_PARENT_RUN_ID", str(parent_id))
    monkeypatch.setenv("WORKAGENT_STEP_INDEX", "2")
    child_id = sq.insert_run(db, "review", ["IPG-1"], 0, state="finished")
    row = sq.get_run(db, child_id)
    assert row is not None
    assert row["parent_run_id"] == parent_id
    assert row["step_index"] == 2

    # Nonexistent parent in env must not raise IntegrityError
    monkeypatch.setenv("WORKAGENT_PARENT_RUN_ID", "999999")
    orphan_id = sq.insert_run(db, "sync", ["IPG-1"], 0, state="finished")
    orphan = sq.get_run(db, orphan_id)
    assert orphan is not None
    assert orphan["parent_run_id"] is None


def test_persisted_summary_serializes_parent_and_step(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    sq.init_db(db)
    parent_id = sq.insert_run(db, "flow", ["run", "ship", "IPG-1"], 0, state="finished")
    child_id = sq.insert_run(
        db, "start", ["IPG-1"], 0, state="finished",
        parent_run_id=parent_id, step_index=1,
    )
    row = sq.get_run(db, child_id)
    assert row is not None
    summary = wr._persisted_summary(row)
    assert summary["parent_run_id"] == f"db-{parent_id}"
    assert summary["step_index"] == 1
