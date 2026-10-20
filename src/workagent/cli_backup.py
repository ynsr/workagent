"""`backup` command: export/import custom actions + flow templates (JSON).

DB stays authoritative; files are the backup story. Import replaces
both tables wholesale (confirm-gated) and validates every row through
the existing create/update validators.
"""
from __future__ import annotations

from pathlib import Path

import typer

from .cli_core import (
    _catch_harness_errors,
    _fail,
    _print_result,
    app,
    eprint,
)

backup_app = typer.Typer(help="Backup actions + flow templates to JSON.",
                         no_args_is_help=True)
app.add_typer(backup_app, name="backup")


def _db():
    from . import store_sqlite as _sq
    return _sq.db_path()


def _resolve_path(raw: str | None) -> Path:
    from . import store_backup as _b
    return Path(raw).expanduser() if raw else _b.default_backup_path()


@backup_app.command("export")
@_catch_harness_errors
def backup_export(
    path: str | None = typer.Option(None, "--path", help="Output file (default: config dir)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Export actions + flow templates to a JSON backup file."""
    import json

    from . import store_backup as _b
    dest = _resolve_path(path)
    snapshot = _b.export_backup(_db())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False)
                    + "\n", encoding="utf-8")
    _print_result({"exported": str(dest),
                   "actions": len(snapshot["actions"]),
                   "flow_templates": len(snapshot["flow_templates"])},
                  json_output)


@backup_app.command("import")
@_catch_harness_errors
def backup_import(
    path: str | None = typer.Argument(None, help="Backup file (default: config dir)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation (replaces tables)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Import a JSON backup, REPLACING actions + flow templates."""
    import json as _json
    import sys as _sys

    from . import store_backup as _b
    src = _resolve_path(path)
    if not src.exists():
        _fail(f"backup file not found: {src}", 2)
    if not yes and _sys.stdin.isatty():
        eprint(f"import {src}? this REPLACES all actions + flow templates [y/N] ",
               end="")
        if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", 2)
    elif not yes:
        _fail("refusing to replace tables without --yes (non-TTY)", 2)
    try:
        snapshot = _json.loads(src.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as e:
        _fail(f"cannot read backup {src}: {e}", 2)
    out = _b.import_backup(_db(), snapshot)
    _print_result({"imported": str(src), **out}, json_output)
