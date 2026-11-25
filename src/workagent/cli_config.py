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
        "default_model": str(cfg.get("default_model") or store.DEFAULT_MODEL),
        "default_effort": str(cfg.get("default_effort") or store.DEFAULT_EFFORT),
        "supported_harnesses": sorted(backend.HARNESSES),
    }, json_output)


@config_app.command("set")
@_catch_harness_errors
def config_set(
    key: str = typer.Argument(..., help="Config key (default-harness, default-model, default-effort)."),
    value: str = typer.Argument(..., help="Config value."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Update a configuration value."""
    norm = key.strip().lower().replace("_", "-")
    if norm not in ("default-harness", "default-model", "default-effort"):
        _fail(
            f"unknown config key: {key!r} (supported: default-harness, default-model, default-effort)",
            EXIT_USAGE,
        )
    cfg = store.load_config()
    if norm == "default-harness":
        backend._load_harnesses()
        if value not in backend.HARNESSES:
            _fail(f"unsupported harness: {value!r} (supported: {', '.join(sorted(backend.HARNESSES))})", EXIT_USAGE)
        if cfg.get("default_harness") != value:
            cfg["default_model"] = ""
            cfg["default_effort"] = ""
        cfg["default_harness"] = value
        store.save_config(cfg)
        eprint(f"default_harness = {value}")
        _print_result({"default_harness": value}, json_output)
        return
    field = norm.replace("-", "_")
    cleaned = value.strip()
    cfg[field] = cleaned
    store.save_config(cfg)
    eprint(f"{field} = {cleaned}")
    _print_result({field: cleaned}, json_output)
