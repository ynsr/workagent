"""`action` command: custom-action CRUD + per-worktree harness execution.

Registered on the shared `cli_core.app`; cli.py imports this module
for its side effect.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from . import store, worktrees
from .cli_core import (
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _init_completions,
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .cli_harness import _guard_harness, _run_harness, append_extra_prompt
from .errors import HarnessError

_COMP = _init_completions()
_complete_refs = _COMP["refs"]

action_app = typer.Typer(help="Manage and run custom actions.", no_args_is_help=True)
app.add_typer(action_app, name="action")


def _db():
    from . import store_sqlite as _sq
    return _sq.db_path()


def build_action_prompt(action: dict, entry: dict, key: str,
                        links: dict) -> str:
    """Action prompt + worktree-paths block (target first, then children)."""
    base = (action.get("prompt") or "").strip()
    paths = [str(entry.get("worktree", ""))]
    children = sorted(
        str(v.get("worktree", ""))
        for k, v in links.items()
        if isinstance(v, dict) and k != key
        and (v.get("parent_key_ref", "") or "") == key
        and v.get("worktree"))
    for c in children:
        if c not in paths:
            paths.append(c)
    paths = [p for p in paths if p]
    block = "Related worktrees:\n" + "\n".join(f"- {p}" for p in paths)
    return f"{base}\n\n{block}" if base else block


def _resolve_mode(action: dict, launch: bool, terminal: bool) -> str:
    """CLI flags win, then default_options.run_mode, then preview."""
    if launch and terminal:
        _fail("--terminal cannot be used with --launch (pick one run mode)",
              EXIT_USAGE)
    if launch:
        return "headless"
    if terminal:
        return "terminal"
    mode = ((action.get("default_options") or {}).get("run_mode")
            or "preview")
    if mode not in ("preview", "headless", "terminal"):
        _fail(f"unknown run_mode {mode!r} in default_options", EXIT_USAGE)
    return mode


@action_app.command("add")
@_catch_harness_errors
def action_add(
    name: str = typer.Option(..., "--name", help="Display name."),
    label: Optional[str] = typer.Option(None, "--label", help="Unique key (default: normalized name)."),
    icon: Optional[str] = typer.Option(None, "--icon", help="Lucide icon name (default: Zap)."),
    prompt: Optional[str] = typer.Option(None, "--prompt", help="Agent prompt."),
    pre_execution: Optional[str] = typer.Option(None, "--pre-execution", help="Pre-harness shell script (cwd: worktree)."),
    post_execution: Optional[str] = typer.Option(None, "--post-execution", help="Post-harness shell script (cwd: worktree)."),
    default_options: Optional[str] = typer.Option(None, "--default-options", help='JSON execution config ({"run_mode": ...}).'),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Add a custom action.

    Example:
      workagent action add --name "Generate Post PRD" --prompt "Write it."
    """
    row = store.create_action(
        name=name, label=label or "", icon=icon or "",
        prompt=prompt or "", pre_execution=pre_execution or "",
        post_execution=post_execution or "",
        default_options=default_options or "")
    _print_result({"added": row["label"], "id": row["id"],
                   "name": row["name"]}, json_output)


@action_app.command("list")
def action_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List custom actions."""
    items = [{"id": r["id"], "label": r["label"], "name": r["name"],
              "icon": r["icon"],
              "mode": (r.get("default_options") or {}).get("run_mode", "preview"),
              "updated": r["modified_at"]}
             for r in store.list_actions()]
    _print_rows(items, json_output, csv_output,
                ["id", "label", "name", "icon", "mode", "updated"],
                "Custom actions", "(no custom actions)")


@action_app.command("show")
@_catch_harness_errors
def action_show(
    label: str = typer.Argument(..., help="Action label."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Show one custom action."""
    row = store.get_action(label)
    if row is None:
        _fail(f"unknown action label: {label!r}", EXIT_USAGE)
    out = {**row, "default_options": json.dumps(row.get("default_options") or {})}
    _print_result(out, json_output)


@action_app.command("update")
@_catch_harness_errors
def action_update(
    label: str = typer.Argument(..., help="Action label."),
    name: Optional[str] = typer.Option(None, "--name", help="New display name."),
    icon: Optional[str] = typer.Option(None, "--icon", help="New Lucide icon name."),
    prompt: Optional[str] = typer.Option(None, "--prompt", help="New agent prompt."),
    pre_execution: Optional[str] = typer.Option(None, "--pre-execution", help="New pre-harness shell script."),
    post_execution: Optional[str] = typer.Option(None, "--post-execution", help="New post-harness shell script."),
    default_options: Optional[str] = typer.Option(None, "--default-options", help='New JSON execution config ({"run_mode": ...}).'),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Update a custom action (label is immutable)."""
    fields: dict = {}
    if name is not None:
        fields["name"] = name
    if icon is not None:
        fields["icon"] = icon
    if prompt is not None:
        fields["prompt"] = prompt
    if pre_execution is not None:
        fields["pre_execution"] = pre_execution
    if post_execution is not None:
        fields["post_execution"] = post_execution
    if default_options is not None:
        fields["default_options"] = default_options
    if not fields:
        _fail("nothing to update — pass at least one field flag", EXIT_USAGE)
    row = store.update_action(label, **fields)
    _print_result({"updated": row["label"], "id": row["id"],
                   "name": row["name"]}, json_output)


@action_app.command("delete")
@_catch_harness_errors
def action_delete(
    label: str = typer.Argument(..., help="Action label."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Delete a custom action."""
    import sys
    if not yes and sys.stdin.isatty():
        eprint(f"delete action {label}? [y/N] ", end="")
        if (sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    store.delete_action(label)
    _print_result({"deleted": label}, json_output)


@action_app.command("run")
@_catch_harness_errors
def action_run(
    label: str = typer.Argument(..., help="Action label."),
    ref: str = typer.Argument(..., autocompletion=_complete_refs, help="Worktree ref key/branch/path."),
    launch: bool = typer.Option(False, "--launch", help="Run headless (default: preview)."),
    terminal: bool = typer.Option(False, "--terminal", help="Run in OS terminal (XOR --launch)."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured or action default)."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the prompt."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before commands."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Run a custom action against one linked worktree.

    Example:
      workagent action run generate_post_prd jira:IPG-932 --launch
    """
    action = store.get_action(label)
    if action is None:
        _fail(f"unknown action label: {label!r}", EXIT_USAGE)
    if not (action.get("prompt") or "").strip() \
            and not (action.get("pre_execution") or "").strip():
        _fail(f"action {label!r} has neither prompt nor pre_execution",
              EXIT_USAGE)
    links = store.load_links()
    try:
        key, entry, _ = worktrees.resolve_any(ref, links)
    except worktrees.NoLinkedState as e:
        _fail(str(e), EXIT_USAGE)
    if (entry.get("active", 1) == 0):
        _fail(f"{key}: deactivated — skipping", EXIT_USAGE)
    wt = str(entry.get("worktree", ""))
    if not worktrees.is_valid_worktree(wt):
        _fail(f"{key}: invalid worktree {wt}", EXIT_USAGE)
    mode = _resolve_mode(action, launch, terminal)
    no_tty = mode in ("headless", "terminal")
    use_launch = mode == "headless"
    use_terminal = mode == "terminal"
    harness_name = harness or (action.get("default_options") or {}).get("harness") or None
    if dry_run:
        _print_result({"label": label, "key": key, "worktree": wt,
                       "mode": mode,
                       "has_prompt": bool((action.get("prompt") or "").strip()),
                       "has_pre": bool((action.get("pre_execution") or "").strip()),
                       "has_post": bool((action.get("post_execution") or "").strip())},
                      json_output)
        return
    import sys as _sys
    if not yes and not no_tty and _sys.stdin.isatty():
        eprint(f"run action {label} on {key} ({wt})? [y/N] ", end="")
        if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    pre = (action.get("pre_execution") or "").strip()
    if pre:
        from . import release_gates as _gates
        pre_env = {"WORKAGENT_PREVIEW": "1"} if mode == "preview" else None
        try:
            _gates.run_repo_command(label, "pre-execution", pre, wt, 600,
                                    env_file, pre_env)
        except _gates.SkippedGate as e:
            _print_result({"label": label, "key": key, "skipped": e.reason},
                          json_output)
            return {"label": label, "key": key, "skipped": e.reason}
        except HarnessError as e:
            eprint(f"{key}: pre-execution failed: {e} — skipping harness")
            _print_result({"label": label, "key": key, "skipped": "pre-execution failed"},
                          json_output)
            return
    if not (action.get("prompt") or "").strip():
        eprint(f"{key}: prompt empty — scripts only, no harness session")
        result = {"label": label, "key": key, "skipped": "empty prompt"}
    else:
        if mode != "preview":
            _guard_harness(key, wt)
        harness_name = harness_name or store.load_config().get("default_harness", "omp")
        prompt = build_action_prompt(action, entry, key, links)
        if extra_prompt and extra_prompt.strip():
            prompt = append_extra_prompt(prompt, extra_prompt)
        result = _run_harness(harness_name, prompt, wt, str(entry.get("repo", wt)),
                     no_tty, use_launch, {"command": "action"}, json_output, run_key=key,
                     session_file=session_file if isinstance(session_file, str) else None,
                     terminal=use_terminal, env_file=env_file)
        if mode == "preview":
            # Preview never executes the harness, so post (which gates on
            # harness success) is out of scope — note it and stop. This
            # also keeps stdout to the single _run_harness result blob.
            if (action.get("post_execution") or "").strip():
                eprint(f"{key}: preview — post-execution runs after a real launch only")
            return
    post = (action.get("post_execution") or "").strip()
    if post:
        from . import release_gates as _gates
        try:
            _gates.run_repo_command(label, "post-execution", post, wt, 600,
                                    env_file)
        except _gates.SkippedGate as e:
            _print_result({**result, "skipped": e.reason}, json_output)
            return {**result, "skipped": e.reason}
        except HarnessError as e:
            eprint(f"{key}: post-execution failed: {e}")
            _print_result({**result, "post_failed": str(e)}, json_output)
            import sys as _sys2
            _sys2.exit(1)
