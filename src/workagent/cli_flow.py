"""`flow` command: flow-template CRUD + flow runs (Start → … → Release).

Registered on the shared `cli_core.app`; cli.py imports this module
for its side effect.

Execution fans out to the existing step runners in-process (zero logic
duplication). Each step mints its normal session + run rows; the flow
row only tracks the cursor (`current_step`), per-step results, the
worktree ref, and a repo snapshot (Release reads the snapshot, never
the possibly-cleaned link).
"""
from __future__ import annotations

import json
from typing import Optional

import typer

from . import store, worktrees
from . import store_flows as sf
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
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
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Update a flow template (label is immutable)."""
    fields: dict = {}
    if name is not None:
        fields["name"] = name
    if steps is not None:
        fields["steps"] = steps
    if pre_execution is not None:
        fields["pre_execution"] = pre_execution
    if post_execution is not None:
        fields["post_execution"] = post_execution
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


# ── step seams (module-level for testability) ─────────────────────────────
# Each _run_*_step returns a result dict; {"preview": True} (plus an
# optional "command") marks a preview/terminal outcome → flow pauses.


def _resolve_ref(ref: str) -> tuple[str, dict, dict | None]:
    """Resolve *ref* to (key, entry, parsed); exit 2 when unresolvable."""
    links = store.load_links()
    try:
        return worktrees.resolve_any(ref, links)
    except worktrees.NoLinkedState as e:
        try:
            from . import refs as _refs
            parsed = _refs.parse_ref(ref)
        except HarnessError:
            _fail(str(e), EXIT_USAGE)
        return "", {}, parsed


def _merge_config(step: dict, overrides: dict) -> dict:
    """Template step default + per-run override (wholesale merge)."""
    return {**(step.get("config") or {}), **overrides}


def _run_start_step(ref: str, key: str, config: dict,
                    json_output: bool) -> dict:
    """Run the Start step in-process (adopt when the worktree exists)."""
    from . import cli_start as _start_mod
    launch = bool(config.get("launch", True))
    terminal = bool(config.get("terminal", False))
    return {"key": key, "ref": ref, "preview": not (launch or terminal),
            "_runner": _start_mod.start, "config": config,
            "json_output": json_output}


def _run_review_step(key: str, config: dict, fix_comments: bool,
                     json_output: bool) -> dict:
    """Run the Review (or Fix-comments) step in-process."""
    from . import cli_review as _review_mod
    launch = bool(config.get("launch", True))
    terminal = bool(config.get("terminal", False))
    return {"key": key, "preview": not (launch or terminal),
            "fix_comments": fix_comments,
            "_runner": _review_mod.review, "config": config,
            "json_output": json_output}


def _run_sync_step(key: str, config: dict, json_output: bool) -> dict:
    """Run the Sync step in-process."""
    from . import cli_sync as _sync_mod
    return {"key": key, "preview": bool(config.get("dry_run", False)),
            "_runner": _sync_mod.sync_cmd, "config": config,
            "json_output": json_output}


def _run_action_step(key: str, action_label: str, config: dict,
                     json_output: bool) -> dict:
    """Run a custom-action step in-process (live label re-check)."""
    action = store.get_action(action_label)
    if action is None:
        raise HarnessError(f"unknown action_label {action_label!r}",
                           exit_code=2)
    from . import cli_action as _action_mod
    mode = ((action.get("default_options") or {}).get("run_mode")
            or "preview")
    launch = bool(config.get("launch", mode == "headless"))
    terminal = bool(config.get("terminal", mode == "terminal"))
    return {"key": key, "label": action_label,
            "preview": not (launch or terminal),
            "_runner": _action_mod.action_run, "config": config,
            "json_output": json_output}


def _run_cleanup_step(key: str, config: dict, json_output: bool) -> dict:
    """Run the Cleanup step (fixed squash + soft-delete; overrides rejected)."""
    for banned in ("hard_delete", "hard-delete", "no_squash", "no-squash"):
        if banned in json.dumps(config):
            raise HarnessError(
                f"{banned!r} is rejected in cleanup steps — cleanup "
                f"is fixed to squash + soft-delete", exit_code=2)
    from . import cli_cleanup as _cleanup_mod
    return {"key": key, "_runner": _cleanup_mod._cleanup_one,
            "config": config, "json_output": json_output}


def _run_release_step(repo_snapshot: dict, config: dict,
                      json_output: bool) -> dict:
    """Run the Release step from the flow's repo snapshot (never links)."""
    from . import cli_release as _release_mod
    from . import release_lock as _lock
    from . import release_util as _util
    repo = str((repo_snapshot or {}).get("repo", "") or "")
    if not repo:
        raise HarnessError("flow has no repo snapshot — cannot release",
                           exit_code=2)
    lock = _lock.lock_path(str(_util.worktree_base()), repo)
    if _lock.locked(lock):
        return {"skipped": "release-locked", "repo": repo}
    return {"repo": repo, "_runner": _release_mod.run_release,
            "config": config, "json_output": json_output}


def _run_repo_command(repo_name: str, phase: str, command: str,
                      cwd: str) -> None:
    """Template pre/post script via run_repo_command (raises on failure)."""
    from . import release_gates as _gates
    _gates.run_repo_command(repo_name, phase, command, cwd, 600, None)


def _tag_flow_session(key: str, flow_id: int) -> None:
    """Best-effort: stamp flow_id into the step's newest session metadata."""
    try:
        from . import store_sqlite as _sq
        sessions = _sq.list_sessions(_sq.db_path())
        mine = [s for s in sessions
                if (s.get("worktree_ref") or "") == key]
        if mine:
            _sq.set_session_metadata(
                _sq.db_path(), mine[-1]["id"], {"flow_id": flow_id})
    except Exception as e:
        eprint(f"flow session tagging skipped: {e}")


# ── flow run ──────────────────────────────────────────────────────────────

def _find_resumable(template_label: str, ref: str,
                    key: str) -> dict | None:
    """Latest non-done flow for the template+worktree pair (None: fresh)."""
    for flow in reversed(store.list_flows()):
        if flow.get("template_label") != template_label:
            continue
        if flow.get("status") == "done":
            continue
        wref = flow.get("worktree_ref") or ""
        if key and wref == key:
            return flow
        if not key and not wref:
            return flow
    return None


@flow_app.command("run")
@_catch_harness_errors
def flow_run(
    template: str = typer.Argument(..., help="Flow template label."),
    ref: str = typer.Argument(..., autocompletion=_complete_refs, help="Issue ref or worktree key/branch/path."),
    step_config: Optional[str] = typer.Option(None, "--step-config", help='Per-run step overrides as JSON ({"<index>": {...}}).'),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the resolved chain without executing."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    fresh: bool = typer.Option(False, "--fresh", help="Force a new flow row instead of resuming."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Run a flow template against an issue ref or worktree."""
    tpl = store.get_template(template)
    if tpl is None:
        _fail(f"unknown flow template: {template!r}", EXIT_USAGE)
    assert tpl is not None
    steps = sf.validate_template(tpl.get("steps") or [],
                                 get_action=store.get_action)
    try:
        overrides = json.loads(step_config or "{}")
    except json.JSONDecodeError as e:
        _fail(f"--step-config is not valid JSON: {e}", EXIT_USAGE)
    if not isinstance(overrides, dict):
        _fail("--step-config must be a JSON object", EXIT_USAGE)
    merged_steps = []
    for i, step in enumerate(steps):
        cfg = _merge_config(step, dict(overrides.get(str(i), {})
                                       if isinstance(overrides.get(str(i)), dict)
                                       else {}))
        merged = {**step, "config": cfg}
        merged_steps.append(merged)
    # Fail fast on per-run cleanup overrides (before any flow row exists).
    for step in merged_steps:
        if step["kind"] == "cleanup":
            for banned in ("hard_delete", "hard-delete",
                           "no_squash", "no-squash"):
                if banned in json.dumps(step.get("config") or {}):
                    _fail(f"{banned!r} is rejected in cleanup steps — "
                          f"cleanup is fixed to squash + soft-delete",
                          EXIT_USAGE)
    if dry_run:
        _print_result({"template": tpl["label"], "ref": ref,
                       "chain": merged_steps}, json_output)
        return
    import sys as _sys
    if not yes and _sys.stdin.isatty():
        eprint(f"run flow {tpl['label']} on {ref}? [y/N] ", end="")
        if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    key, entry, parsed = _resolve_ref(ref)
    flow = None if fresh else _find_resumable(tpl["label"], ref, key)
    if flow is None:
        flow = store.create_flow(template_label=tpl["label"],
                                 worktree_ref=key)
    fid = flow["id"]
    store.update_flow(fid, status="running")
    current = int(flow.get("current_step") or 0)
    results = list(flow.get("step_results") or [])
    pre = (tpl.get("pre_execution") or "").strip()
    wt_path = str((entry or {}).get("worktree", "") or "")
    if pre and current == 0:
        if key and wt_path:
            try:
                _run_repo_command(tpl["label"], "pre-execution", pre,
                                  wt_path)
            except HarnessError as e:
                store.update_flow(fid, status="failed",
                                  step_results=results + [{
                                      "kind": "pre_execution",
                                      "status": "failed", "detail": str(e)}])
                _fail(f"pre-execution failed: {e}", EXIT_GENERAL)
        # No worktree yet for issue flows: skip pre until step 0 lands
        # (the Start step creates the worktree; see below).
    for idx in range(current, len(merged_steps)):
        step = merged_steps[idx]
        kind = step["kind"]
        config = step.get("config") or {}
        try:
            if kind == "start":
                outcome = _run_start_step(ref, key, config, json_output)
                if not outcome.get("preview") and not outcome.get("dry_run"):
                    new_key = str(outcome.get("key") or key or "")
                    if new_key and new_key != key:
                        key = new_key
                        store.update_flow(fid, worktree_ref=key)
                        links = store.load_links()
                        entry = links.get(key, entry)
                        wt_path = str((entry or {}).get("worktree", "")
                                      or wt_path)
                    if key and not (flow.get("repo_snapshot") or {}):
                        repo = str((entry or {}).get("repo", "") or "")
                        registry = store.load_repos()
                        info = registry.get(repo, {})
                        store.update_flow(
                            fid, repo_snapshot={
                                "repo": repo,
                                "default_branch": str(info.get(
                                    "default_branch", "") or ""),
                                "release_branch": str(info.get(
                                    "release_branch", "") or "")})
                        flow = store.get_flow(fid) or flow
                    if pre and not wt_path:
                        wt_path = str(((store.load_links().get(key, {})
                                                or {}).get("worktree", "")) or "")
                        if wt_path:
                            # Deferred pre for issue flows: worktree now
                            # exists after step 0.
                            _run_repo_command(tpl["label"], "pre-execution",
                                              pre, wt_path)
            elif kind in ("review", "fix_comments"):
                outcome = _run_review_step(key, config,
                                           kind == "fix_comments",
                                           json_output)
            elif kind == "sync":
                outcome = _run_sync_step(key, config, json_output)
            elif kind == "action":
                outcome = _run_action_step(key,
                                           str(step.get("action_label", "")),
                                           config, json_output)
            elif kind == "cleanup":
                outcome = _run_cleanup_step(key, config, json_output)
            elif kind == "release":
                snap = (store.get_flow(fid) or {}).get("repo_snapshot",
                                                       {}) or {}
                outcome = _run_release_step(snap, config, json_output)
            else:  # pragma: no cover — validate_template guards kinds
                raise HarnessError(f"unknown step kind {kind!r}",
                                   exit_code=2)
        except HarnessError as e:
            results.append({"kind": kind, "status": "failed",
                            "detail": str(e)})
            store.update_flow(fid, status="failed", current_step=idx,
                              step_results=results)
            raise typer.Exit(e.exit_code)
        if outcome.get("skipped"):
            results.append({"kind": kind, "status": "skipped",
                            "detail": str(outcome["skipped"])})
            store.update_flow(fid, current_step=idx + 1,
                              step_results=results)
            continue
        if outcome.get("preview"):
            results.append({"kind": kind, "status": "paused",
                            "detail": str(outcome.get("command", ""))})
            store.update_flow(fid, status="paused", current_step=idx,
                              step_results=results)
            cmd = outcome.get("command") or f"flow run {tpl['label']} {ref}"
            eprint(f"paused after step {idx} ({kind}) — preview/terminal "
                   f"needs a human:\n{cmd}\nre-run to resume.")
            _print_result({"flow": fid, "status": "paused",
                           "step": idx, "kind": kind}, json_output)
            return
        results.append({"kind": kind, "status": "ok",
                        "detail": str(outcome.get("status", ""))})
        store.update_flow(fid, current_step=idx + 1,
                          step_results=results)
        _tag_flow_session(key, fid)
        if kind == "cleanup":
            # The worktree row is soft-deleted after a successful
            # cleanup — later steps must not touch links.
            key = key
    post = (tpl.get("post_execution") or "").strip()
    if post:
        snap_wt = wt_path or str(((store.load_links().get(key, {})
                                           or {}).get("worktree", "")) or "")
        if snap_wt:
            try:
                _run_repo_command(tpl["label"], "post-execution", post,
                                  snap_wt)
            except HarnessError as e:
                store.update_flow(fid, status="failed",
                                  step_results=results + [{
                                      "kind": "post_execution",
                                      "status": "failed", "detail": str(e)}])
                _fail(f"post-execution failed: {e}", EXIT_GENERAL)
    store.update_flow(fid, status="done", step_results=results)
    _print_result({"flow": fid, "status": "done",
                   "steps": len(merged_steps)}, json_output)


@flow_app.command("list")
def flow_list(
    worktree: Optional[str] = typer.Option(None, "--worktree", help="Scope to one worktree ref."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List flow runs."""
    items = [{"id": f["id"], "template": f["template_label"],
              "worktree": f.get("worktree_ref", ""),
              "status": f["status"],
              "step": f.get("current_step", 0),
              "updated": f.get("modified_at", "")}
             for f in store.list_flows(worktree=worktree or "")]
    _print_rows(items, json_output, csv_output,
                ["id", "template", "worktree", "status", "step", "updated"],
                "Flows", "(no flows)")


@flow_app.command("show")
@_catch_harness_errors
def flow_show(
    flow_id: int = typer.Argument(..., help="Flow id."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Show one flow run (steps + per-step results + repo snapshot)."""
    flow = store.get_flow(flow_id)
    if flow is None:
        _fail(f"unknown flow id: {flow_id!r}", EXIT_USAGE)
    assert flow is not None
    tpl = store.get_template(flow["template_label"])
    out = {**flow,
           "steps": json.dumps((tpl or {}).get("steps") or []),
           "step_results": json.dumps(flow.get("step_results") or []),
           "repo_snapshot": json.dumps(flow.get("repo_snapshot") or {})}
    _print_result(out, json_output)
