"""`workagent service` — user-scoped systemd lifecycle for `serve`.

Wraps the mechanics in `.service` (unit render, linger, systemctl):
install (write unit → daemon-reload → enable --now → linger),
uninstall (disable --now → remove unit → daemon-reload),
start/stop/restart/status/logs thin systemctl/journalctl wrappers.
"""
from __future__ import annotations

import json
import sys

import typer

from . import service as _svc
from .cli_core import EXIT_GENERAL, EXIT_USAGE, _catch_harness_errors, _fail, app, eprint

service_app = typer.Typer(help="Run the web UI as a user systemd service (auto-start on login).",
                          no_args_is_help=True)
app.add_typer(service_app, name="service")


def _resolve_port(port: int | None) -> int:
    import os
    try:
        return port or int(os.environ.get("PORT", str(_svc.DEFAULT_PORT)))
    except ValueError:
        _fail(f"invalid PORT: {os.environ.get('PORT')!r}", EXIT_GENERAL)
    raise AssertionError("unreachable")


def _confirm_or_abort(prompt: str, yes: bool, dry_run: bool = False) -> None:
    if yes or dry_run:
        return
    if sys.stdin.isatty():
        if typer.confirm(prompt):
            return
        _fail("aborted", EXIT_USAGE)
    _fail("refusing to act non-interactively without --yes "
          "(or preview with --dry-run)", EXIT_USAGE)


@service_app.command("install")
@_catch_harness_errors
def service_install(
    host: str = typer.Option(_svc.DEFAULT_HOST, "--host", help="Bind address for serve (default: loopback only)."),
    port: int = typer.Option(None, "--port", help="TCP port (default: $PORT, else 3344)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Install + enable the user service (auto-start on login, linger on)."""
    resolved = _resolve_port(port)
    _confirm_or_abort(f"Install user service workagent.service (serve --host {host} --port {resolved})?", yes, dry_run)
    from .webapp import _is_loopback
    if not _is_loopback(host):
        eprint(f"warning: non-loopback bind {host} is unauthenticated — see the README security note.")
    result = _svc.install(host, resolved, yes=yes, dry_run=dry_run)
    if json_output:
        print(json.dumps(result))
        return
    if dry_run:
        for a in result["actions"]:
            eprint(f"would: {a}")
        return
    for a in result["actions"]:
        eprint(a)
    if result.get("linger_warning"):
        eprint(f"warning: {result['linger_warning']} — service stops at logout without linger.")
    if result.get("error"):
        _fail(result["error"], EXIT_GENERAL)
    eprint(f"service installed: {result['unit']}")


@service_app.command("uninstall")
@_catch_harness_errors
def service_uninstall(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Disable + remove the user service (leaves linger untouched)."""
    _confirm_or_abort("Disable and remove user service workagent.service?", yes)
    result = _svc.uninstall()
    if json_output:
        print(json.dumps(result))
        return
    for a in result["actions"]:
        eprint(a)


def _ctl(action: str, json_output: bool) -> None:
    cp = _svc._run_systemctl(action, "workagent")
    out = (cp.stdout or "").strip()
    if json_output:
        print(json.dumps({"action": action, "exit": cp.returncode, "output": out,
                          "error": (cp.stderr or '').strip()}))
    elif out:
        print(out)
    elif cp.stderr:
        eprint(cp.stderr.strip())
    if cp.returncode != 0:
        raise typer.Exit(cp.returncode)


@service_app.command("start")
@_catch_harness_errors
def service_start(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Start the user service now."""
    _ctl("start", json_output)


@service_app.command("stop")
@_catch_harness_errors
def service_stop(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Stop the user service now."""
    _ctl("stop", json_output)


@service_app.command("restart")
@_catch_harness_errors
def service_restart(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Restart the user service now."""
    _ctl("restart", json_output)


@service_app.command("status")
@_catch_harness_errors
def service_status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Report whether the user service is active (exit code preserved)."""
    cp = _svc._run_systemctl("is-active", "workagent")
    state = (cp.stdout or "").strip()
    if json_output:
        print(json.dumps({"status": state or "unknown",
                          "active": cp.returncode == 0}))
    else:
        print(state or "unknown")
    if cp.returncode != 0:
        raise typer.Exit(cp.returncode)


@service_app.command("logs")
@_catch_harness_errors
def service_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of journal lines to show."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow new journal entries."),
) -> None:
    """Show journal logs for the user service (pages only on a TTY)."""
    args = [f"-n{lines}"]
    if follow:
        args.append("-f")
    elif not sys.stdout.isatty():
        args.append("--no-pager")
    raise typer.Exit(_svc._run_journal(*args))
