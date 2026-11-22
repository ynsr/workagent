"""`schedule` command: recurring Cron/RRULE schedule CRUD + due-run dispatcher (T13).

Registered on the shared `cli_core.app`; cli.py imports this module
for its side effect.
"""
from __future__ import annotations

import json
from typing import Optional

import typer

from . import scheduler as _sched
from . import store_sqlite as _sq
from .cli_core import (
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _print_result,
    _print_rows,
    app,
)

schedule_app = typer.Typer(help="Manage recurring Cron/RRULE schedules.", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")


def _db():
    return _sq.db_path()


@schedule_app.command("list")
@_catch_harness_errors
def schedule_list(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    csv_out: bool = typer.Option(False, "--csv", help="Emit CSV on stdout."),
) -> None:
    """List all configured schedules."""
    rows = _sq.list_schedules(_db())
    if json_out:
        print(json.dumps({"schedules": rows}, indent=2))
        return
    cols = ["id", "name", "cron_expr", "timezone", "target_kind", "target_label", "enabled", "next_run_at", "last_run_at"]
    table_rows = [
        [
            str(r["id"]),
            r["name"],
            r["cron_expr"],
            r["timezone"],
            r["target_kind"],
            r["target_label"],
            "yes" if r["enabled"] else "no",
            r["next_run_at"] or "-",
            r["last_run_at"] or "-",
        ]
        for r in rows
    ]
    _print_rows(cols, table_rows, title="Schedules", as_csv=csv_out)


@schedule_app.command("add")
@_catch_harness_errors
def schedule_add(
    name: str = typer.Option(..., "--name", help="Unique schedule name."),
    cron: str = typer.Option(..., "--cron", help="5-field cron expression, macro (@daily), or RRULE."),
    target_kind: str = typer.Option(..., "--target-kind", help="Target kind: flow, action, or command."),
    target_label: str = typer.Option(..., "--target-label", help="Flow template label, action label, or CLI command."),
    timezone_name: str = typer.Option("UTC", "--timezone", help="IANA timezone name (default UTC)."),
    args_json: str = typer.Option("{}", "--args", help="JSON object/array of extra arguments."),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Whether the schedule is active."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Create a new recurring schedule."""
    try:
        nxt = _sched.next_run_after(cron, timezone_name).isoformat()
        row = _sq.add_schedule(
            _db(),
            name=name,
            cron_expr=cron,
            timezone_name=timezone_name,
            target_kind=target_kind,
            target_label=target_label,
            args_json=args_json,
            enabled=enabled,
            next_run_at=nxt,
        )
    except (ValueError, json.JSONDecodeError) as e:
        _fail(str(e), EXIT_USAGE)
    _print_result(row, json_out)


@schedule_app.command("update")
@_catch_harness_errors
def schedule_update(
    schedule_id: str = typer.Argument(..., help="Schedule ID or name."),
    name: Optional[str] = typer.Option(None, "--name", help="New schedule name."),
    cron: Optional[str] = typer.Option(None, "--cron", help="New cron/RRULE expression."),
    target_kind: Optional[str] = typer.Option(None, "--target-kind", help="New target kind: flow, action, or command."),
    target_label: Optional[str] = typer.Option(None, "--target-label", help="New target label or command."),
    timezone_name: Optional[str] = typer.Option(None, "--timezone", help="New IANA timezone."),
    args_json: Optional[str] = typer.Option(None, "--args", help="New JSON arguments."),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled", help="Enable or disable the schedule."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Update an existing schedule."""
    existing = _sq.get_schedule(_db(), schedule_id)
    if existing is None:
        _fail(f"unknown schedule: {schedule_id!r}", EXIT_USAGE)
    eff_cron = cron if cron is not None else existing["cron_expr"]
    eff_tz = timezone_name if timezone_name is not None else existing["timezone"]
    next_run_at: str | None = None
    if cron is not None or timezone_name is not None or (enabled is True and not existing.get("next_run_at")):
        try:
            next_run_at = _sched.next_run_after(eff_cron, eff_tz).isoformat()
        except ValueError as e:
            _fail(str(e), EXIT_USAGE)
    try:
        row = _sq.update_schedule(
            _db(),
            existing["id"],
            name=name,
            cron_expr=cron,
            timezone=timezone_name,
            target_kind=target_kind,
            target_label=target_label,
            args_json=args_json,
            enabled=enabled,
            next_run_at=next_run_at,
        )
    except (ValueError, json.JSONDecodeError) as e:
        _fail(str(e), EXIT_USAGE)
    _print_result(row, json_out)


@schedule_app.command("remove")
@_catch_harness_errors
def schedule_remove(
    schedule_id: str = typer.Argument(..., help="Schedule ID or name."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Delete a schedule."""
    ok = _sq.remove_schedule(_db(), schedule_id)
    if not ok:
        _fail(f"unknown schedule: {schedule_id!r}", EXIT_USAGE)
    _print_result({"removed": True, "id": schedule_id}, json_out)


@schedule_app.command("run-due")
@_catch_harness_errors
def schedule_run_due(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview due schedules without dispatching."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Dispatch all enabled schedules currently due."""
    results = _sched.run_due_schedules(_db(), dry_run=dry_run)
    if json_out:
        print(json.dumps({"results": results}, indent=2))
        return
    if not results:
        print("No due schedules.")
        return
    cols = ["id", "name", "status", "next_run_at", "error"]
    table_rows = [
        [str(r["id"]), r["name"], r["status"], r["next_run_at"] or "-", r["error"] or "-"]
        for r in results
    ]
    _print_rows(cols, table_rows, title="Due schedules")
