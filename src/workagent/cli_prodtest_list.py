"""`prodtest list` / `prodtest status`: read-only views over prodtest_items."""
from __future__ import annotations

import json

import typer

from . import store
from .cli_core import _catch_harness_errors, _fail, _print_rows
from .cli_prodtest import _prodtest_key_for_status, prodtest_app

_COLUMNS = ["issue", "status", "plan", "revision", "risk", "release", "last_run"]


def _row_for_list(item: dict) -> dict:
    return {
        "issue": str(item.get("issue_key", "") or ""),
        "status": str(item.get("status", "") or ""),
        "plan": str(item.get("plan_path", "") or ""),
        "revision": item.get("plan_revision", "") or "",
        "risk": str(item.get("plan_risk", "") or ""),
        "release": str(item.get("release_sha", "") or ""),
        "last_run": str(item.get("last_run_id", "") or ""),
    }


@prodtest_app.command("list")
@_catch_harness_errors
def prodtest_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """List every prodtest item with status/plan/rev/release/last run."""
    try:
        items = store.list_prodtests()
    except Exception as e:
        _fail(f"prodtest list failed: {e}", 1)
    rows = [_row_for_list(i) for i in (items or []) if isinstance(i, dict)]
    if json_output:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    _print_rows(rows, json_output=False, csv_output=False, columns=_COLUMNS,
                title="Prodtest items", empty="no prodtest items")


@prodtest_app.command("status")
@_catch_harness_errors
def prodtest_status(
    ref: str = typer.Argument(..., help="Issue ref or worktree ref (key/branch/path)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Print one prodtest item (status/plan/rev/release/last run)."""
    store_key = _prodtest_key_for_status(ref)
    try:
        item = store.get_prodtest(store_key)
    except Exception as e:
        _fail(f"prodtest status failed: {e}", 1)
    if not item:
        _fail(f"no prodtest item for {store_key}", 2)
    assert item is not None
    row = _row_for_list(dict(item))
    # Enrich the single-item view with the release timestamp + run payload.
    try:
        row["released_at"] = str(item.get("released_at", "") or "")
        row["run_results"] = str(item.get("run_results", "") or "")
        row["worktree_ref"] = str(item.get("worktree_ref", "") or "")
    except Exception:
        pass
    if json_output:
        print(json.dumps(row, indent=2, ensure_ascii=False))
        return
    from .cli_core import _print_result
    _print_result(row, False)

@prodtest_app.command("delete")
@_catch_harness_errors
def prodtest_delete(
    ref: str = typer.Argument(..., help="Issue ref or worktree ref (key/branch/path)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Delete one prodtest item row."""
    from .cli_prodtest import _prodtest_key_for_status as _key_for
    store_key = _key_for(ref)
    try:
        deleted = store.delete_prodtest(store_key)
    except Exception as e:
        _fail(f"prodtest delete failed: {e}", 1)
    if not deleted:
        _fail(f"no prodtest item for {store_key}", 2)
    from .cli_core import _print_result
    _print_result({"deleted": store_key}, json_output)


@prodtest_app.command("cleanup")
@_catch_harness_errors
def prodtest_cleanup(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    yes: bool = typer.Option(False, "--yes", help="Delete without prompting (required non-TTY)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List rows that would be deleted without acting."),
) -> None:
    """Delete every prodtest item whose status is `passed`."""
    import sys as _sys
    try:
        items = store.list_prodtests()
    except Exception as e:
        _fail(f"prodtest cleanup failed: {e}", 1)
    targets = [str(i.get("issue_key", "") or "") for i in (items or [])
               if isinstance(i, dict) and i.get("status") == "passed"
               and str(i.get("issue_key", "") or "")]
    if dry_run:
        rows = [{"issue": k, "status": "cleanup-dry-run"} for k in sorted(targets)]
        if json_output:
            print(json.dumps({"results": rows}, indent=2, ensure_ascii=False))
            return
        _print_rows(rows, json_output=False, csv_output=False,
                    columns=["issue", "status"],
                    title="Prodtest cleanup", empty="no passed prodtest items")
        return
    if not targets:
        _print_rows([], json_output=False, csv_output=False,
                    columns=["issue", "status"],
                    title="Prodtest cleanup", empty="no passed prodtest items")
        return
    if not yes and not _sys.stdin.isatty():
        _fail("prodtest cleanup requires --yes (non-interactive); add --dry-run to preview.", 2)
    rows: list[dict] = []
    for key in sorted(targets):
        try:
            store.delete_prodtest(key)
        except Exception as e:
            rows.append({"issue": key, "status": f"failed: {e}"})
            continue
        rows.append({"issue": key, "status": "deleted"})
    if json_output:
        print(json.dumps({"results": rows}, indent=2, ensure_ascii=False))
        return
    _print_rows(rows, json_output=False, csv_output=False,
                columns=["issue", "status"],
                title="Prodtest cleanup", empty="no passed prodtest items")
