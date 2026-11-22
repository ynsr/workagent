"""Tests for T13 built-in Cron/RRULE scheduler, CLI, and REST API."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from workagent import cli, scheduler
from workagent import store_sqlite as sq
from workagent.webapp import create_app


def test_parse_and_next_run_cron_and_rrule():
    base = datetime(2026, 6, 1, 8, 30, 0, tzinfo=timezone.utc)  # Monday

    # Standard 5-field cron
    nxt = scheduler.next_run_after("0 9 * * 1-5", "UTC", after=base)
    assert nxt == datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)

    # Macro @daily
    nxt_daily = scheduler.next_run_after("@daily", "UTC", after=base)
    assert nxt_daily == datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc)

    # Step expression
    nxt_step = scheduler.next_run_after("*/15 * * * *", "UTC", after=base)
    assert nxt_step == datetime(2026, 6, 1, 8, 45, 0, tzinfo=timezone.utc)

    # RRULE weekly
    nxt_rrule = scheduler.next_run_after(
        "RRULE:FREQ=WEEKLY;BYDAY=TU;BYHOUR=10;BYMINUTE=15", "UTC", after=base
    )
    assert nxt_rrule == datetime(2026, 6, 2, 10, 15, 0, tzinfo=timezone.utc)

    # Preview returns ascending timestamps
    preview = scheduler.preview_next_runs("0 */6 * * *", "UTC", count=3, after=base)
    assert len(preview) == 3
    assert preview == sorted(preview)

    # Invalid expressions and timezones raise ValueError
    with pytest.raises(ValueError):
        scheduler.next_run_after("bad cron", "UTC", after=base)
    with pytest.raises(ValueError):
        scheduler.next_run_after("0 9 * * *", "Not/A_Zone", after=base)


def test_build_schedule_argv():
    assert scheduler.build_schedule_argv({
        "target_kind": "flow",
        "target_label": "nightly",
        "args": {"ref": "jira:IPG-1", "yes": True},
    }) == ["flow", "run", "nightly", "jira:IPG-1", "--yes"]

    assert scheduler.build_schedule_argv({
        "target_kind": "action",
        "target_label": "lint",
        "args": {"ref": "jira:IPG-2", "acp": True},
    }) == ["action", "run", "lint", "jira:IPG-2", "--acp"]

    assert scheduler.build_schedule_argv({
        "target_kind": "command",
        "target_label": "cleanup --merged --yes",
        "args": {},
    }) == ["cleanup", "--merged", "--yes"]


def test_run_due_schedules_lease_safe(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    now = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)

    due = sq.add_schedule(
        db,
        name="due-job",
        cron_expr="0 9 * * *",
        timezone_name="UTC",
        target_kind="command",
        target_label="review --all --yes",
        next_run_at="2026-06-01T09:00:00+00:00",
    )
    sq.add_schedule(
        db,
        name="future-job",
        cron_expr="0 20 * * *",
        timezone_name="UTC",
        target_kind="command",
        target_label="cleanup --merged --yes",
        next_run_at="2026-06-01T20:00:00+00:00",
    )

    # Dry run does not mutate
    dry = scheduler.run_due_schedules(db, now=now, dry_run=True)
    assert len(dry) == 1
    assert dry[0]["name"] == "due-job"
    assert dry[0]["status"] == "dry_run"
    assert sq.get_schedule(db, due["id"])["last_run_at"] == ""

    # Real dispatch records last_run_at, next_run_at, and last_run_id
    dispatched: list[list[str]] = []

    def fake_dispatch(sched, argv):
        dispatched.append(argv)
        return None

    res = scheduler.run_due_schedules(db, now=now, dispatcher=fake_dispatch)
    assert len(res) == 1
    assert res[0]["status"] == "dispatched"
    assert dispatched == [["review", "--all", "--yes"]]

    updated = sq.get_schedule(db, due["id"])
    assert updated["last_run_at"] == now.isoformat()
    assert updated["next_run_at"] == "2026-06-02T09:00:00+00:00"
    assert updated["lease_until"] == ""

    # Running again at same timestamp dispatches nothing
    assert scheduler.run_due_schedules(db, now=now, dispatcher=fake_dispatch) == []


def test_cli_schedule_crud_and_run_due(isolated_config):
    runner = CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "schedule", "add",
            "--name", "daily-cleanup",
            "--cron", "0 20 * * *",
            "--target-kind", "command",
            "--target-label", "cleanup --merged --yes",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    created = json.loads(res.stdout)
    assert created["name"] == "daily-cleanup"

    res_list = runner.invoke(cli.app, ["schedule", "list", "--json"])
    assert res_list.exit_code == 0
    assert len(json.loads(res_list.stdout)["schedules"]) == 1

    res_upd = runner.invoke(
        cli.app,
        ["schedule", "update", str(created["id"]), "--disabled", "--json"],
    )
    assert res_upd.exit_code == 0
    assert json.loads(res_upd.stdout)["enabled"] is False

    res_due = runner.invoke(cli.app, ["schedule", "run-due", "--dry-run", "--json"])
    assert res_due.exit_code == 0
    assert json.loads(res_due.stdout)["results"] == []

    res_rm = runner.invoke(cli.app, ["schedule", "remove", str(created["id"]), "--json"])
    assert res_rm.exit_code == 0
    assert json.loads(res_rm.stdout)["removed"] is True


def test_webapp_schedule_endpoints(isolated_config, tmp_path):
    app = create_app(tmp_path / "static", "127.0.0.1", 8765, [])
    client = TestClient(app, base_url="http://localhost")
    headers = {"Origin": "http://localhost"}

    # Preview
    r = client.post("/api/schedules/preview", json={"cron_expr": "0 9 * * 1-5", "timezone": "UTC", "count": 3}, headers=headers)
    assert r.status_code == 200
    assert len(r.json()["next_runs"]) == 3

    # Create
    r = client.post(
        "/api/schedules",
        json={
            "name": "Daily PR review",
            "cron_expr": "0 9 * * 1-5",
            "timezone": "UTC",
            "target_kind": "command",
            "target_label": "review --all --yes",
        },
        headers=headers,
    )
    assert r.status_code == 200
    sid = r.json()["id"]

    # List includes starters + created schedule
    r = client.get("/api/schedules")
    assert r.status_code == 200
    body = r.json()
    assert len(body["schedules"]) == 1
    assert len(body["starters"]) == 3

    # Patch
    r = client.patch(f"/api/schedules/{sid}", json={"enabled": False}, headers=headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # Delete
    r = client.delete(f"/api/schedules/{sid}", headers=headers)
    assert r.status_code == 200
    assert r.json()["removed"] is True


def test_rrule_rejects_interval_gt1_non_hourly_and_unknown_keys():
    for expr in (
        "RRULE:FREQ=DAILY;INTERVAL=2;BYHOUR=10;BYMINUTE=0",
        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;BYHOUR=10;BYMINUTE=0",
        "RRULE:FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=1;BYHOUR=10;BYMINUTE=0",
    ):
        with pytest.raises(ValueError, match="INTERVAL"):
            scheduler.next_run_after(expr, "UTC")
    # Unknown keys (COUNT/UNTIL/DTSTART/...) must not silently run forever.
    for expr in (
        "RRULE:FREQ=DAILY;COUNT=5;BYHOUR=10;BYMINUTE=0",
        "RRULE:FREQ=DAILY;UNTIL=20270101T000000Z;BYHOUR=10;BYMINUTE=0",
        "RRULE:FREQ=DAILY;DTSTART=20260601T000000Z;BYHOUR=10;BYMINUTE=0",
    ):
        with pytest.raises(ValueError, match="unsupported RRULE key"):
            scheduler.next_run_after(expr, "UTC")
    # HOURLY INTERVAL>1 stays valid; plain INTERVAL=1 RRULEs still convert.
    assert scheduler._rrule_to_cron("RRULE:FREQ=HOURLY;INTERVAL=2;BYMINUTE=15") == "15 */2 * * *"
    assert scheduler._rrule_to_cron("RRULE:FREQ=DAILY;BYHOUR=10;BYMINUTE=15") == "15 10 * * *"


def test_run_due_keeps_next_run_on_next_run_failure(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    now = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    row = sq.add_schedule(
        db,
        name="bad-cron",
        cron_expr="0 9 * * *",
        timezone_name="UTC",
        target_kind="command",
        target_label="cleanup --merged --yes",
        next_run_at="2026-06-01T09:00:00+00:00",
    )
    # Corrupt the stored expr after the fact (writes bypass validation).
    from workagent.store_sqlite import connect
    conn = connect(db)
    conn.execute("UPDATE schedules SET cron_expr = ? WHERE id = ?", ("not a cron", row["id"]))
    conn.commit()
    conn.close()
    res = scheduler.run_due_schedules(db, now=now, dispatcher=lambda s, a: None)
    assert len(res) == 1
    assert res[0]["status"] == "error"
    assert "next-run" in (res[0]["error"] or "")
    kept = sq.get_schedule(db, row["id"])
    assert kept["next_run_at"] == "2026-06-01T09:00:00+00:00"


def test_webapp_schedule_patch_recompute_and_invalid_cron(isolated_config, tmp_path):
    app = create_app(tmp_path / "static", "127.0.0.1", 8765, [])
    client = TestClient(app, base_url="http://localhost")
    headers = {"Origin": "http://localhost"}
    r = client.post(
        "/api/schedules",
        json={
            "name": "Morning review",
            "cron_expr": "0 9 * * *",
            "timezone": "UTC",
            "target_kind": "command",
            "target_label": "review --all",
        },
        headers=headers,
    )
    assert r.status_code == 200
    sid = r.json()["id"]
    before = r.json()["next_run_at"]

    # Patching cron_expr recomputes next_run_at.
    r = client.patch(f"/api/schedules/{sid}", json={"cron_expr": "0 10 * * *"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["next_run_at"] != before

    # Patching an unrelated field preserves next_run_at.
    nxt = r.json()["next_run_at"]
    r = client.patch(f"/api/schedules/{sid}", json={"name": "Renamed"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert r.json()["next_run_at"] == nxt

    # Invalid cron_expr PATCH is a 400, not a 500/store of garbage.
    r = client.patch(f"/api/schedules/{sid}", json={"cron_expr": "not a cron"}, headers=headers)
    assert r.status_code == 400

    # run-now on a destructive target requires confirm:true.
    r = client.post(f"/api/schedules/{sid}", headers=headers)
    assert r.status_code in (404, 405)  # routes are /api/schedules/{id}/run only
    r = client.post(f"/api/schedules/{sid}/run", headers=headers)
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def test_webapp_schedule_rrule_validation(isolated_config, tmp_path):
    app = create_app(tmp_path / "static", "127.0.0.1", 8765, [])
    client = TestClient(app, base_url="http://localhost")
    headers = {"Origin": "http://localhost"}
    for expr in (
        "RRULE:FREQ=DAILY;INTERVAL=2;BYHOUR=10;BYMINUTE=0",
        "RRULE:FREQ=DAILY;COUNT=5;BYHOUR=10;BYMINUTE=0",
    ):
        r = client.post(
            "/api/schedules",
            json={
                "name": "bad rrule",
                "cron_expr": expr,
                "timezone": "UTC",
                "target_kind": "command",
                "target_label": "cleanup --merged --yes",
            },
            headers=headers,
        )
        assert r.status_code == 400
