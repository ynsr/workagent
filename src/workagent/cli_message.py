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

def _mm(*argv: str) -> dict:
    """Run `mm` and JSON-parse stdout; HarnessError surfaces verbatim."""
    from .errors import run_cmd
    import json as _json
    out = run_cmd("mm", *argv)
    try:
        return _json.loads(out or "{}")
    except Exception:
        return {"raw": out or ""}

def _message_header(msg: dict) -> str:
    repos = ", ".join(msg.get("repos") or [])
    return (f"Message {msg.get('message_id','')} ({msg.get('app_name','')},"
            f" from {msg.get('sender_username','')} at {msg.get('ts','')}):\n"
            f"{msg.get('content','')}\nThread: {msg.get('thread_id','')}\n"
            f"Repos: {repos}")


def _resolve_exec(msg: dict, ref_opt: str = "", repo_opt: str = "") -> tuple[str, str]:
    """Hybrid resolve → (workdir, worktree_key | "").

    Explicit `--ref` wins; `--repo` narrows multi-repo messages; a lone
    `repos[]` entry resolves via the repo registry; several repos without
    `--repo`/`--ref` → exit 2 naming candidates.
    """
    from . import worktrees
    if (ref_opt or "").strip():
        links = store.load_links()
        _key, entry, _ = worktrees.resolve_any(ref_opt.strip(), links)
        return str(entry.get("worktree", "")), _key
    repos = [r for r in (msg.get("repos") or []) if r]
    if repo_opt:
        if repo_opt not in repos:
            _fail(f"--repo {repo_opt} is not linked to message"
                  f" {msg.get('message_id','')} (repos: {', '.join(repos)})",
                  EXIT_USAGE)
        repos = [repo_opt]
    if not repos:
        _fail(f"message {msg.get('message_id','')} has no linked repos", EXIT_USAGE)
    if len(repos) == 1:
        reg = store.load_repos()
        hit = reg.get(repos[0]) or {}
        return str(hit.get("path", "")), ""
    import sys as _sys
    if not _sys.stdin.isatty():
        _fail(f"message {msg.get('message_id','')} is linked to multiple repos:"
              f" {', '.join(repos)} — re-run with --repo <name> or --ref <worktree>",
              EXIT_USAGE)
    from . import pick as _pick
    choice = _pick.pick(f"Pick repo for {msg.get('message_id','')}",
                        [(r, "") for r in repos])
    if not choice:
        _fail("no repo selected", EXIT_USAGE)
    return str(store.load_repos().get(choice, {}).get("path", "")), ""

def _run_prompt(msg: dict, prompt: str, ref_opt: str, repo_opt: str, launch: bool,
                terminal: bool, harness: Optional[str], extra_prompt: Optional[str],
                env_file: Optional[str], session_file: Optional[str],
                dry_run: bool, yes: bool, json_output: bool) -> None:
    """Shared adhoc/validate runner: message-header + prompt → harness."""
    from .cli_harness import _guard_harness, _run_harness, append_extra_prompt
    if launch and terminal:
        _fail("--terminal cannot be used with --launch (pick one run mode)", EXIT_USAGE)
    if not (prompt or "").strip() and not extra_prompt:
        _fail("--prompt is required and must not be blank", EXIT_USAGE)
    workdir, key = _resolve_exec(msg, ref_opt, repo_opt or "")
    if not workdir:
        _fail(f"could not resolve a workdir for message {msg.get('message_id','')}",
              EXIT_USAGE)
    body = f"{_message_header(msg)}\n\n{(prompt or '').strip()}"
    if (extra_prompt or "").strip():
        body = append_extra_prompt(body, extra_prompt.strip())
    mode = "terminal" if terminal else ("headless" if launch else "preview")
    if dry_run:
        _print_result({"message": msg.get("message_id"), "workdir": workdir,
                       "key": key, "mode": mode, "dry_run": True,
                       "prompt_chars": len(body)}, json_output)
        return
    import sys as _sys
    if not yes and _sys.stdin.isatty() and mode == "preview":
        eprint(f"run prompt on message {msg.get('message_id')} ({workdir})? [y/N] ", end="")
        if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    if key:
        _guard_harness(key, workdir)
    _run_harness(harness or store.load_config().get("default_harness", "omp"),
                 body, workdir, workdir, mode == "preview", mode != "terminal",
                 {"command": "message", "session_metadata": {"action": "message",
                  "mode": mode, "message_id": msg.get("message_id")}},
                 env_file=env_file, session_file=session_file)
    _print_result({"message": msg.get("message_id"), "workdir": workdir,
                   "key": key, "mode": mode}, json_output)

@message_app.command("adhoc")
@_catch_harness_errors
def message_adhoc(
    msg: str = typer.Argument(..., help="Message id/message_id/thread."),
    prompt: str = typer.Option("", "--prompt", help="Prompt body (required)."),
    ref: str = typer.Option("", "--ref", help="Worktree ref (wins over repos[])."),
    repo: str = typer.Option("", "--repo", help="Pin one repo for multi-repo messages."),
    launch: bool = typer.Option(False, "--launch", help="Run headless."),
    terminal: bool = typer.Option(False, "--terminal", help="Run in OS terminal."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before commands."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Run an adhoc prompt grounded in one registered message."""
    row = store.resolve_message(msg)
    _run_prompt(row, prompt, ref, repo, launch, terminal, harness, extra_prompt,
                env_file, session_file, dry_run, yes, json_output)


@message_app.command("validate")
@_catch_harness_errors
def message_validate(
    msg: str = typer.Argument(..., help="Message id/message_id/thread."),
    prompt: str = typer.Option("", "--prompt", help="Extra validation instructions."),
    ref: str = typer.Option("", "--ref", help="Worktree ref (wins over repos[])."),
    repo: str = typer.Option("", "--repo", help="Pin one repo for multi-repo messages."),
    launch: bool = typer.Option(False, "--launch", help="Run headless."),
    terminal: bool = typer.Option(False, "--terminal", help="Run in OS terminal."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Validate a message's claims against its linked repos."""
    row = store.resolve_message(msg)
    fixed = ("Validate the claims in this message against the linked repos."
             " For each claim report CONFIRMED/REFUTED with file:line evidence.")
    extra = f"{fixed} {(prompt or '').strip()}".strip()
    _run_prompt(row, extra, ref, repo, launch, terminal, harness, None,
                None, None, dry_run, yes, json_output)

@message_app.command("draft-reply")
@_catch_harness_errors
def message_draft_reply(
    msg: str = typer.Argument(..., help="Message id/message_id/thread."),
    text: str = typer.Option("", "--text", help="Draft body (required)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print argv without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Save a draft reply via `mm` (never direct-send)."""
    if not (text or "").strip():
        _fail("--text is required and must not be blank", EXIT_USAGE)
    row = store.resolve_message(msg)
    info = _mm("messages", "get-msg", row["message_id"])
    channel = str(info.get("channel_id") or info.get("channel") or "")
    if not channel:
        _fail(f"mm has no channel for message {row['message_id']}", EXIT_USAGE)
    thread_root = row.get("thread_id") or row["message_id"]
    if dry_run:
        _print_result({"message": row["message_id"], "channel_id": channel,
                       "reply_to": thread_root, "dry_run": True,
                       "argv": ["mm", "messages", "draft", "save", "--channel",
                                channel, "--reply-to", thread_root,
                                "--text", text.strip()]}, json_output)
        return
    saved = _mm("messages", "draft", "save", "--channel", channel,
                "--reply-to", thread_root, "--text", text.strip())
    _print_result({"message": row["message_id"], "channel_id": channel,
                   "reply_to": thread_root, "draft": saved}, json_output)

action_app = typer.Typer(help="Run message-scoped custom actions.", no_args_is_help=True)
message_app.add_typer(action_app, name="action")


@action_app.command("run")
@_catch_harness_errors
def message_action_run(
    label: str = typer.Argument(..., help="Action label (category must be message)."),
    msg: str = typer.Argument(..., help="Message id/message_id/thread."),
    ref: str = typer.Option("", "--ref", help="Worktree ref (wins over repos[])."),
    repo: str = typer.Option("", "--repo", help="Pin one repo for multi-repo messages."),
    launch: bool = typer.Option(False, "--launch", help="Run headless."),
    terminal: bool = typer.Option(False, "--terminal", help="Run in OS terminal."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before commands."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Run a message-category custom action grounded in one message."""
    action = store.get_action(label)
    if action is None:
        _fail(f"unknown action label: {label!r}", EXIT_USAGE)
    if (action.get("category") or "worktree") != "message":
        _fail(f"action {label!r} has category"
              f" {(action.get('category') or 'worktree')!r} — message action run"
              " requires a message-category action", EXIT_USAGE)
    row = store.resolve_message(msg)
    workdir, _key = _resolve_exec(row, ref, repo)
    if not workdir:
        _fail(f"could not resolve a workdir for message {row.get('message_id','')}",
              EXIT_USAGE)
    from .cli_action import action_run as _action_run
    from . import worktrees as _wt
    links = store.load_links()
    found = ""
    for _k, _e in links.items():
        if str((_e or {}).get("worktree", "")) == workdir and _wt.is_valid_worktree(workdir):
            found = _k
            break
    if not found:
        _fail(f"message {row.get('message_id', '')} resolves to repo path"
              f" {workdir} with no linked worktree — re-run with"
              " --ref <worktree>", EXIT_USAGE)
    header_extra = f"{_message_header(row)}\n\n{(extra_prompt or '').strip()}".strip()
    _action_run(label, found, launch=launch, terminal=terminal, harness=harness,
                extra_prompt=header_extra or None,
                env_file=env_file, session_file=session_file, dry_run=dry_run,
                yes=yes, json_output=json_output)

flow_app = typer.Typer(help="Run message-scoped flow templates.", no_args_is_help=True)
message_app.add_typer(flow_app, name="flow")


@flow_app.command("run")
@_catch_harness_errors
def message_flow_run(
    label: str = typer.Argument(..., help="Flow template label (category must be message)."),
    msg: str = typer.Argument(..., help="Message id/message_id/thread."),
    ref: str = typer.Option("", "--ref", help="Worktree ref (required for worktree-kind steps)."),
    repo: str = typer.Option("", "--repo", help="Pin one repo for multi-repo messages."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Run a message-category flow template grounded in one message."""
    from .store_flows import validate_template
    tmpl = store.get_template(label)
    if tmpl is None:
        _fail(f"unknown flow template: {label!r}", EXIT_USAGE)
    if (tmpl.get("category") or "worktree") != "message":
        _fail(f"flow template {label!r} has category"
              f" {(tmpl.get('category') or 'worktree')!r} — message flow run"
              " requires a message-category template", EXIT_USAGE)
    row = store.resolve_message(msg)
    steps = validate_template(tmpl.get("steps") or [], get_action=store.get_action)
    if not (ref or "").strip():
        _fail(f"flow template {label!r} needs --ref <worktree> (message"
              f" {row.get('message_id', '')} is not a resolvable flow ref)",
              EXIT_USAGE)
    header = _message_header(row)
    for s in steps:
        if s.get("kind") == "action":
            cfg = s.get("config") or {}
            alabel = str(cfg.get("action") or cfg.get("label") or "")
            if alabel:
                act = store.get_action(alabel)
                if act is not None and (act.get("category") or "worktree") != "message":
                    _fail(f"flow template {label!r} step {alabel!r} is a"
                          " worktree-category action — message flows require"
                          " message-category actions", EXIT_USAGE)
            merged = dict(cfg)
            prev = str(merged.get("extra_prompt") or "").strip()
            merged["extra_prompt"] = f"{header}\n\n{prev}".strip() if prev else header
            s["config"] = merged
    from . import cli_flow as _cf
    _cf.flow_run(label, (ref or "").strip(), dry_run=dry_run, yes=yes, json_output=json_output)
