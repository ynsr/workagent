"""Tests for run telemetry persistence and API exposure (issue #155 / T12)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from workagent import store_sqlite as sq
from workagent.webapp import create_app


def test_runs_telemetry_migration_and_finish_run(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    # Re-running init_db is idempotent.
    sq.init_db(db)

    rid = sq.insert_run(
        db, "start", ["jira:IPG-1"], None,
        harness_name="agy", state="running",
        model_id="claude-sonnet-4", effort="high",
    )
    row_running = sq.get_run(db, rid)
    assert row_running is not None
    assert row_running["model_id"] == "claude-sonnet-4"
    assert row_running["effort"] == "high"
    assert row_running["finished_at"] == ""
    assert row_running["duration_ms"] is None

    sq.finish_run(
        db, rid, "finished",
        duration_ms=12345,
        input_tokens=1200,
        output_tokens=450,
        cached_tokens=300,
        cost_usd=0.0145,
    )
    row = sq.get_run(db, rid)
    assert row is not None
    assert row["state"] == "finished"
    assert row["finished_at"]
    assert row["duration_ms"] == 12345
    assert row["model_id"] == "claude-sonnet-4"
    assert row["effort"] == "high"
    assert row["input_tokens"] == 1200
    assert row["output_tokens"] == 450
    assert row["cached_tokens"] == 300
    assert row["cost_usd"] == 0.0145

    listed = sq.list_runs(db)
    assert len(listed) == 1
    assert listed[0]["duration_ms"] == 12345
    assert listed[0]["cost_usd"] == 0.0145


def test_api_runs_exposes_telemetry_and_finished(isolated_config, tmp_path):
    db = sq.db_path()
    sq.init_db(db)
    rid = sq.insert_run(
        db, "review", ["org/repo#1", "--model", "claude-opus-4", "--effort", "max"],
        0, harness_name="omp", state="running",
        model_id="claude-opus-4", effort="max",
    )
    sq.finish_run(
        db, rid, "finished",
        duration_ms=4200,
        input_tokens=800,
        output_tokens=200,
        cached_tokens=100,
        cost_usd=0.025,
    )

    app = create_app(tmp_path, "127.0.0.1", 8765, {"127.0.0.1", "localhost", "testserver"})
    client = TestClient(app)
    r_list = client.get("/api/runs")
    assert r_list.status_code == 200
    items = r_list.json()
    match = next(x for x in items if x["id"] == f"db-{rid}")
    assert match["finished"] is not None
    assert match["duration_ms"] == 4200
    assert match["model_id"] == "claude-opus-4"
    assert match["effort"] == "max"
    assert match["input_tokens"] == 800
    assert match["output_tokens"] == 200
    assert match["cached_tokens"] == 100
    assert match["cost_usd"] == 0.025

    r_detail = client.get(f"/api/runs/db-{rid}")
    assert r_detail.status_code == 200
    detail = r_detail.json()
    assert detail["finished"] == match["finished"]
    assert detail["duration_ms"] == 4200
    assert detail["model_id"] == "claude-opus-4"
    assert detail["effort"] == "max"
    assert detail["input_tokens"] == 800
    assert detail["output_tokens"] == 200
    assert detail["cached_tokens"] == 100
    assert detail["cost_usd"] == 0.025
