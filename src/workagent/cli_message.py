"""`message` command: registered chat-message CRUD + runs."""
from __future__ import annotations

from typing import Optional

import typer

from . import store
from .cli_core import EXIT_USAGE, _catch_harness_errors, _fail, _print_result, _print_rows, app, eprint

message_app = typer.Typer(help="Manage registered chat messages.", no_args_is_help=True)
app.add_typer(message_app, name="message")


@message_app.command("add")
@_catch_harness_errors
def message_add(
    message_id: str = typer.Option(..., "--message-id", help="App-side id (e.g. Mattermost post id)."),
    thread_id: str = typer.Option("", "--thread-id", help="Thread root id (default: top-level)."),
    app_name: str = typer.Option("mattermost", "--app", help="Source app."),
    sender: str = typer.Option("", "--sender", help="Sender username."),
    content: str = typer.Option("", "--content", help="Message text."),
    ts: str = typer.Option("", "--ts", help="Sent timestamp."),
    repo: list[str] = typer.Option([], "--repo", help="Associated repo (repeatable, >=1)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Register a chat message (repos must reference known repo keys)."""
    row = store.create_message(message_id=message_id, thread_id=thread_id, app_name=app_name,
                               sender_username=sender, content=content, ts=ts, repos=list(repo or []))
    _print_result({"added": row["message_id"], "id": row["id"]}, json_output)


@message_app.command("list")
def message_list(
    thread: str = typer.Option("", "--thread", help="Only this thread_id."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV."),
) -> None:
    """List registered messages."""
    items = [{"id": r["id"], "message_id": r["message_id"], "thread_id": r["thread_id"],
              "app": r["app_name"], "sender": r["sender_username"],
              "repos": ",".join(r.get("repos") or []), "ts": r["ts"]}
             for r in store.list_messages(thread)]
    _print_rows(items, json_output, csv_output,
                ["id", "message_id", "thread_id", "app", "sender", "repos", "ts"],
                "Messages", "(no messages)")


@message_app.command("show")
@_catch_harness_errors
def message_show(
    ref: str = typer.Argument(..., help="Message id/message_id/thread."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show one registered message."""
    _print_result(store.resolve_message(ref), json_output)


@message_app.command("update")
@_catch_harness_errors
def message_update(
    ref: str = typer.Argument(...,
                              help="Message id/message_id/thread."),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    sender: Optional[str] = typer.Option(None, "--sender"),
    content: Optional[str] = typer.Option(None, "--content"),
    ts: Optional[str] = typer.Option(None, "--ts"),
    repo: Optional[list[str]] = typer.Option(None, "--repo"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Update a registered message."""
    fields: dict = {k: v for k, v in {"thread_id": thread_id, "sender_username": sender,
                                      "content": content, "ts": ts}.items() if v is not None}
    if repo is not None:
        fields["repos"] = list(repo)
    _print_result(store.update_message(ref, **fields), json_output)


@message_app.command("remove")
@_catch_harness_errors
def message_remove(
    ref: str = typer.Argument(...,
                              help="Message id/message_id/thread."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Remove a registered message."""
    import sys as _sys
    row = store.resolve_message(ref)
    if not yes and _sys.stdin.isatty():
        eprint(f"remove message {row['message_id']}? [y/N] ", end="")
        if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    store.delete_message(ref)
    _print_result({"removed": row["message_id"]}, json_output)
