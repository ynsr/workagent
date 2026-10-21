"""`flow run`/`list`/`show`: execution engine (split from cli_flow.py: 500-line cap)."""
from __future__ import annotations

import json
from typing import Optional

import typer

from . import cli_flow_util as _futil
from . import store, worktrees
from . import store_flows as sf
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .cli_core import _init_completions as _init_comp2
from .errors import HarnessError

_COMP2 = _init_comp2()
_complete_refs = _COMP2["refs"]

from . import cli_flow as _flow
from .cli_flow import flow_app


def _seam(name: str):
    """Late-bound cli_flow helper: tests monkeypatch cli_flow._run_* seams."""
    return getattr(_flow, name)


def _manual_step(*a, **k):
    return _seam("_manual_step")(*a, **k)


def _merge_config(*a, **k):
    return _seam("_merge_config")(*a, **k)


def _resolve_ref(*a, **k):
    return _seam("_resolve_ref")(*a, **k)


def _run_action_step(*a, **k):
    return _seam("_run_action_step")(*a, **k)


def _run_cleanup_step(*a, **k):
    return _seam("_run_cleanup_step")(*a, **k)


def _run_release_step(*a, **k):
    return _seam("_run_release_step")(*a, **k)


def _run_repo_command(*a, **k):
    return _seam("_run_repo_command")(*a, **k)


def _run_review_step(*a, **k):
    return _seam("_run_review_step")(*a, **k)


def _run_start_step(*a, **k):
    return _seam("_run_start_step")(*a, **k)


def _run_sync_step(*a, **k):
    return _seam("_run_sync_step")(*a, **k)


def _tag_flow_session(*a, **k):
    return _seam("_tag_flow_session")(*a, **k)

# ── flow run ──────────────────────────────────────────────────────────────

def _find_resumable(template_label: str, ref: str,
                    key: str) -> dict | None:
    """Latest non-done flow for the template+ref pair (None: fresh)."""
    for flow in reversed(store.list_flows(include_deleted_worktrees=True)):
        if flow.get("template_label") != template_label:
            continue
        if flow.get("status") == "done":
            continue
        wref = flow.get("worktree_ref") or ""
        if key and wref == key:
            return flow
        snap = flow.get("repo_snapshot") or {}
        launch = snap.get("launch_ref") if isinstance(snap, dict) else ""
        if not key and launch and launch == ref:
            return flow
        if not key and not wref and not launch:
            return flow
    return None


@flow_app.command("run")
@_catch_harness_errors
def flow_run(
    template: str = typer.Argument(..., help="Flow template label."),
    ref: str = typer.Argument(..., autocompletion=_complete_refs, help="Issue ref or worktree key/branch/path."),
    step_config: Optional[str] = typer.Option(None, "--step-config", help='Per-run step overrides as JSON ({"<index>": {...}}).'),
    skip_steps: Optional[str] = typer.Option(None, "--skip-steps", help='Comma-separated step indexes to mark done-without-running on resume (e.g. "0" or "0,2"). Only paused/done work applies.'),
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
    merged_steps = [{**s, "config": _merge_config(
        s, overrides.get(str(i)) if isinstance(overrides.get(str(i)), dict) else {})}
        for i, s in enumerate(steps)]
    # Fail fast on per-run overrides (before any flow row exists): banned
    # shortcut/cleanup flags rejected via the shared keys+values scanner.
    for step in merged_steps:
        hit = _futil.has_banned(step.get("config") or {}, sf._BANNED_STEP_FLAGS)
        if hit:
            _fail(f"{hit!r} is rejected inside flows — use an explicit "
                  f"'fix_comments' step", EXIT_USAGE)
        if step["kind"] == "cleanup":
            hit = _futil.has_banned(step.get("config") or {}, sf._BANNED_CLEANUP_FLAGS)
            if hit:
                _fail(f"{hit!r} is rejected in cleanup steps — fixed to squash + soft-delete",
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
        snap: dict = {"launch_ref": ref}
        # Templates without a start step (e.g. cleanup_release) never hit
        # the start-step backfill below — seed repo info from the resolved
        # link up front so the release step finds it (deleted row included:
        # resume after cleanup soft-delete still needs the repo name).
        link = entry or store.load_links(include_deleted=True).get(key, {})
        repo = str((link or {}).get("repo", "") or "")
        if repo:
            info = store.load_repos().get(repo, {})
            snap = {"repo": repo, "launch_ref": ref, "default_branch": str(
                info.get("default_branch", "") or ""),
                "release_branch": str(info.get("release_branch", "") or "")}
        flow = store.create_flow(template_label=tpl["label"], worktree_ref=key,
                                 repo_snapshot=snap)
    fid = flow["id"]
    if not (flow.get("repo_snapshot") or {}).get("repo"):
        # Pre-fix rows (flow 26) carry {'launch_ref'} only — repair on
        # resume from the deleted-aware link row instead of failing.
        link = entry or store.load_links(include_deleted=True).get(key, {})
        repo = str((link or {}).get("repo", "") or "")
        if repo:
            info = store.load_repos().get(repo, {})
            store.update_flow(fid, repo_snapshot={
                "repo": repo, "launch_ref": ref, "default_branch": str(
                    info.get("default_branch", "") or ""),
                "release_branch": str(info.get("release_branch", "") or "")})
            flow = store.get_flow(fid, include_deleted_worktrees=True) or flow
    store.update_flow(fid, status="running")
    current = int(flow.get("current_step") or 0)
    results = list(flow.get("step_results") or [])
    if skip_steps:
        wanted: set[int] = set()
        for tok in str(skip_steps).split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                wanted.add(int(tok))
            except ValueError:
                _fail(f"--skip-steps takes comma-separated step indexes, got {tok!r}", EXIT_USAGE)
        bad = sorted(i for i in wanted if i < 0 or i >= len(merged_steps))
        if bad:
            _fail(f"--skip-steps index out of range (0..{len(merged_steps) - 1}): {bad}", EXIT_USAGE)
        for i in sorted(wanted):
            if i < current:
                continue  # already green — nothing to mark
            if i > current:
                _fail(f"--skip-steps {i} is beyond the resume cursor (step {current}) — "
                      f"only the resume step can be marked done", EXIT_USAGE)
            kind = merged_steps[i]["kind"]
            if results and results[-1].get("kind") == kind and results[-1].get("status") == "paused":
                results.pop()  # replace the stale paused entry for this step
            results.append({"kind": kind, "status": "skipped",
                            "detail": "marked done on resume (--skip-steps)"})
            current = i + 1
            if kind == "start":
                # Skipped start = the human finished in the terminal (or the
                # worktree already exists): adopt whatever the ref resolves
                # to NOW so downstream steps get a real key. Without this
                # the review step runs with key="" and fails "Needs input".
                key, adopted = _futil.adopt_skipped_start_key(
                    _resolve_ref, ref, key)
                if adopted:
                    entry = adopted
                    wt_path = str(adopted.get("worktree", "") or wt_path)
                    store.update_flow(fid, worktree_ref=key)
                # Option A backfill: the detached terminal minted its .jsonl
                # after the pause row was written. Re-resolve every
                # flow-tagged pause session's minted transcript now.
                try:
                    _futil.backfill_terminal_transcripts(fid, key, ref)
                except Exception:
                    pass
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
                if not outcome.get("preview"):
                    new_key = str(outcome.get("key") or key or "")
                    if new_key and new_key != key:
                        key = new_key
                        store.update_flow(fid, worktree_ref=key)
                        links = store.load_links()
                        entry = links.get(key, entry)
                        wt_path = str((entry or {}).get("worktree", "")
                                      or wt_path)
                    if key and not (flow.get("repo_snapshot") or {}).get("repo"):
                        repo = str((entry or {}).get("repo", "") or "")
                        info = store.load_repos().get(repo, {})
                        snap = {"repo": repo, "launch_ref": ref, "default_branch": str(
                            info.get("default_branch", "") or ""),
                            "release_branch": str(
                            info.get("release_branch", "") or "")}
                        store.update_flow(fid, repo_snapshot=snap)
                        flow = store.get_flow(fid) or flow
                    if pre and not wt_path:
                        wt = str((store.load_links().get(key, {}) or {}
                                 ).get("worktree", "") or "")
                        if wt:  # deferred pre: worktree exists after step 0
                            wt_path = wt
                            _run_repo_command(tpl["label"], "pre-execution", pre, wt)
            elif kind in ("review", "fix_comments"):
                outcome = _run_review_step(key, config, kind == "fix_comments", json_output)
            elif kind == "sync":
                outcome = _run_sync_step(key, config, json_output)
            elif kind == "action":
                outcome = _run_action_step(key, str(step.get("action_label", "")), config, json_output)
            elif kind == "cleanup":
                outcome = _run_cleanup_step(key, config, json_output)
            elif kind == "release":
                snap = (store.get_flow(fid, include_deleted_worktrees=True) or {}).get("repo_snapshot") or {}
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
        if outcome.get("status") == "skipped":
            results.append({"kind": kind, "status": "skipped",
                            "detail": str(outcome.get("detail", ""))})
            store.update_flow(fid, current_step=idx + 1,
                              step_results=results)
            continue
        if outcome.get("skipped"):
            results.append({"kind": kind, "status": "skipped",
                            "detail": str(outcome["skipped"])})
            store.update_flow(fid, current_step=idx + 1,
                              step_results=results)
            continue
        if outcome.get("preview"):
            detail = str(outcome.get("command", ""))
            if outcome.get("terminal_pid") is not None:
                detail = f"{detail} [terminal pid {outcome.get('terminal_pid')}]"
            elif outcome.get("terminal_error"):
                detail = f"{detail} [terminal spawn failed: {outcome.get('terminal_error')}]"
            results.append({"kind": kind, "status": "paused",
                            "detail": detail})
            store.update_flow(fid, status="paused", current_step=idx,
                              step_results=results)
            cmd = outcome.get("command") or f"flow run {tpl['label']} {ref}"
            try:
                _futil.persist_terminal_pause(
                    key or ref, str(outcome.get("manual_kind") or kind),
                    list(outcome.get("manual_args") or [kind]),
                    f"harness command: {cmd}", fid,
                    terminal_pid=outcome.get("terminal_pid"),
                    scope=str(outcome.get("session_scope", "") or ""),
                    scope_dir=str(outcome.get("session_dir", "") or ""))
            except Exception:
                pass
            if outcome.get("terminal_pid") is not None:
                eprint(f"paused after step {idx} ({kind}) — terminal opened "
                       f"(pid {outcome.get('terminal_pid')}): finish there, then re-run to resume:\n{cmd}")
            else:
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
