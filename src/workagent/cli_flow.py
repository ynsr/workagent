"""`flow` command: template CRUD + runs (Start → … → Release)."""
from __future__ import annotations

import json
from typing import Optional

import typer

from . import cli_flow_steps as _steps
from . import cli_flow_util as _futil
from . import store, worktrees
from . import store_flows as sf
from .cli_core import (
    _catch_harness_errors,
    _fail,
    _init_completions,
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .errors import HarnessError

_COMP = _init_completions()
_complete_refs = _COMP["refs"]

flow_app = typer.Typer(help="Chain actions Start → … → Cleanup → Release.",
                       no_args_is_help=True)
app.add_typer(flow_app, name="flow")

template_app = typer.Typer(help="Manage flow templates.", no_args_is_help=True)
flow_app.add_typer(template_app, name="template")


# ── template CRUD (mirrors cli_action.py) ─────────────────────────────────


@template_app.command("add")
@_catch_harness_errors
def template_add(
    name: str = typer.Option(..., "--name", help="Display name."),
    label: Optional[str] = typer.Option(None, "--label", help="Unique key (default: normalized name)."),
    steps: Optional[str] = typer.Option(None, "--steps", help='JSON list of step dicts, e.g. \'[{"kind": "start"}, {"kind": "cleanup"}]\'.'),
    pre_execution: Optional[str] = typer.Option(None, "--pre-execution", help="Pre-flow shell script (cwd: worktree)."),
    post_execution: Optional[str] = typer.Option(None, "--post-execution", help="Post-flow shell script (cwd: worktree)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Add a flow template (steps required — the wizard builds them)."""
    if steps is None:
        _fail("flow template must have at least one step — pass --steps JSON", EXIT_USAGE)
    row = store.create_template(
        name=name, label=label or "", steps=steps,
        pre_execution=pre_execution or "",
        post_execution=post_execution or "")
    _print_result({"added": row["label"], "id": row["id"],
                   "name": row["name"]}, json_output)


@template_app.command("list")
def template_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List flow templates."""
    items = [{"id": r["id"], "label": r["label"], "name": r["name"],
              "steps": len(r.get("steps") or []),
              "updated": r["modified_at"]}
             for r in store.list_templates()]
    _print_rows(items, json_output, csv_output,
                ["id", "label", "name", "steps", "updated"],
                "Flow templates", "(no flow templates)")


@template_app.command("show")
@_catch_harness_errors
def template_show(
    label: str = typer.Argument(..., help="Template label."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Show one flow template."""
    row = store.get_template(label)
    if row is None:
        _fail(f"unknown flow template: {label!r}", EXIT_USAGE)
    out = {**row, "steps": json.dumps(row.get("steps") or [])}
    _print_result(out, json_output)


@template_app.command("update")
@_catch_harness_errors
def template_update(
    label: str = typer.Argument(..., help="Template label."),
    name: Optional[str] = typer.Option(None, "--name", help="New display name."),
    steps: Optional[str] = typer.Option(None, "--steps", help="New JSON step list."),
    pre_execution: Optional[str] = typer.Option(None, "--pre-execution", help="New pre-flow shell script."),
    post_execution: Optional[str] = typer.Option(None, "--post-execution", help="New post-flow shell script."),
    new_label: Optional[str] = typer.Option(None, "--new-label", help="Rename to a new label (^[a-z0-9_]+$; flows history retargeted)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Update a flow template (rename via --new-label; flows history retargeted)."""
    fields: dict = {}
    if name is not None:
        fields["name"] = name
    if steps is not None:
        fields["steps"] = steps
    if pre_execution is not None:
        fields["pre_execution"] = pre_execution
    if post_execution is not None:
        fields["post_execution"] = post_execution
    if new_label is not None:
        fields["new_label"] = new_label
    if not fields:
        _fail("nothing to update — pass at least one field flag", EXIT_USAGE)
    row = store.update_template(label, **fields)
    _print_result({"updated": row["label"], "id": row["id"],
                   "name": row["name"]}, json_output)


@template_app.command("delete")
@_catch_harness_errors
def template_delete(
    label: str = typer.Argument(..., help="Template label."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Delete a flow template."""
    import sys
    if not yes and sys.stdin.isatty():
        eprint(f"delete flow template {label}? [y/N] ", end="")
        if (sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    store.delete_template(label)
    _print_result({"deleted": label}, json_output)


# Seams dispatch each step to its runner in-process with merged flags.
# Success → {"status"}; preview/terminal → {"preview": True} (runner skipped).
def _resolve_ref(ref: str) -> tuple[str, dict, dict | None]:
    """Resolve *ref* to (key, entry, parsed); exit 2 when unresolvable."""
    # Deleted rows included: resume after a cleanup soft-delete must still
    # find its key (release runs post-cleanup by design).
    links = store.load_links(include_deleted=True)
    try:
        return worktrees.resolve_any(ref, links)
    except worktrees.NoLinkedState as e:
        from . import refs as _refs
        # `key@repo` suffixes (adopt_skipped_start_key's output) are real
        # link keys, not parseable refs — resolve the stripped base first
        # so matching_issue_keys finds bare/suffixed kin.
        base = ref
        try:
            base, _ = _refs.split_issue_key(ref)
        except Exception:
            base = ref
        if base != ref:
            try:
                return worktrees.resolve_any(base, links)
            except worktrees.NoLinkedState:
                pass
        try:
            parsed = _refs.parse_ref(ref)
        except HarnessError:
            _fail(str(e), EXIT_USAGE)
        return "", {}, parsed

def _merge_config(step: dict, overrides: dict) -> dict:
    return _futil.normalize_config({**(step.get("config") or {}), **overrides})


def _manual_step(kind: str, args: list[str]) -> dict:
    import shlex
    cmd = " ".join(shlex.quote(a) for a in [kind, *args] if a)
    return {"preview": True, "command": f"workagent {cmd}",
            "manual_kind": kind, "manual_args": [kind, *args]}


def _spawn_terminal_step(kind: str, args: list[str], cwd: str = "",
                         branch: str = "", harness: str = "omp") -> dict:
    """Terminal-mode step (delegates to cli_flow_steps; keeps this file small)."""
    return _futil.spawn_terminal_step(kind, args, cwd, branch, harness)

def _pick(config: dict, *names: str) -> dict:
    return _steps.pick(config, *names)


def _run_start_step(ref: str, key: str, config: dict,
                    json_output: bool) -> dict:
    """Start step (delegates to cli_flow_steps; keeps this file small)."""
    return _steps.run_start_step(ref, key, config, json_output,
                                 _spawn_terminal_step, _manual_step)


def _run_review_step(key: str, config: dict, fix_comments: bool,
                     json_output: bool) -> dict:
    """Review step (delegates to cli_flow_steps; keeps this file small)."""
    return _steps.run_review_step(key, config, fix_comments, json_output,
                                  _spawn_terminal_step, _manual_step)


def _run_sync_step(key: str, config: dict, json_output: bool) -> dict:
    """Sync step (delegates to cli_flow_steps; keeps this file small)."""
    return _steps.run_sync_step(key, config, json_output,
                                _spawn_terminal_step, _manual_step)


def _run_action_step(key: str, action_label: str, config: dict, json_output: bool) -> dict:
    """Custom-action step (delegates to cli_flow_steps; keeps this file small)."""
    return _steps.run_action_step(key, action_label, config, json_output,
                                  _spawn_terminal_step, _manual_step)


def _run_cleanup_step(key: str, config: dict, json_output: bool) -> dict:
    """Cleanup step (delegates to cli_flow_steps; keeps this file small)."""
    return _steps.run_cleanup_step(key, config, json_output,
                                   _spawn_terminal_step, _manual_step)


def _run_release_step(repo_snapshot: dict, config: dict, json_output: bool) -> dict:
    """Release step (delegates to cli_flow_steps; keeps this file small)."""
    return _steps.run_release_step(repo_snapshot, config, json_output,
                                    _spawn_terminal_step, _manual_step)


def _run_repo_command(repo_name: str, phase: str, command: str, cwd: str) -> None:
    _steps.run_repo_command(repo_name, phase, command, cwd)


def _tag_flow_session(key: str, flow_id: int) -> None:
    """Best-effort: stamp flow_id into the step's newest session metadata."""
    _steps.tag_flow_session(key, flow_id)


def _find_resumable(template_label: str, ref: str, key: str):
    """Latest non-done flow for the template+ref pair (delegated)."""
    from . import cli_flow_run as _run
    return _run._find_resumable(template_label, ref, key)


# Run/list/show engine lives in cli_flow_run.py (500-line cap split).
from . import cli_flow_run as _run_mod  # noqa: F401  (registers flow run/list/show)
