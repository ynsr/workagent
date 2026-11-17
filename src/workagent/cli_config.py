from __future__ import annotations

import typer

from . import backend, store
from .cli_core import (
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _print_result,
    app,
    eprint,
)

config_app = typer.Typer(help="Show or update workagent configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("show")
@_catch_harness_errors
def config_show(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Show current configuration."""
    cfg = store.load_config()
    backend._load_harnesses()
    _print_result({
        "default_harness": str(cfg.get("default_harness") or store.DEFAULT_HARNESS),
        "supported_harnesses": sorted(backend.HARNESSES),
    }, json_output)


@config_app.command("set")
@_catch_harness_errors
def config_set(
    key: str = typer.Argument(..., help="Config key (default-harness)."),
    value: str = typer.Argument(..., help="Config value."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Update a configuration value."""
    norm = key.strip().lower().replace("_", "-")
    if norm != "default-harness":
        _fail(f"unknown config key: {key!r} (supported: default-harness)", EXIT_USAGE)
    backend._load_harnesses()
    if value not in backend.HARNESSES:
        _fail(f"unsupported harness: {value!r} (supported: {', '.join(sorted(backend.HARNESSES))})", EXIT_USAGE)
    cfg = store.load_config()
    cfg["default_harness"] = value
    store.save_config(cfg)
    eprint(f"default_harness = {value}")
    _print_result({"default_harness": value}, json_output)
