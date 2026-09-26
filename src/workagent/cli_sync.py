"""sync command + helpers (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from . import backend, refs, repos, store, trackers, worktrees
from . import sync as sync_mod
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
from .cli_harness import _guard_harness, _run_harness
from .cli_review import _ensure_branch_worktree
from .errors import HarnessError

_COMP = _init_completions()
_complete_refs = _COMP["refs"]

# ── sync ──────────────────────────────────────────────────────────────


@app.command("sync")
@_catch_harness_errors
def sync_cmd(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs,
                                        help="Issue/PR ref or worktree key (omit: interactive pick, or --all)."),
    merge: bool = typer.Option(False, "-m", "--merge", help="Merge locally in the worktree instead of the default remote rebase. The branch is pushed to origin afterwards."),
    harness: bool = typer.Option(False, "--harness", help="On unresolvable conflicts, launch the coding harness in the worktree."),
    all_sessions: bool = typer.Option(False, "--all", help="Sync every linked worktree (confirmed one by one)."),
    yes: bool = typer.Option(False, "--yes", "--force", "-y", help="Skip confirmations."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without touching anything."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path passed to the runtime on conflict resolution (omp --resume)."),
) -> None:
    from . import cli as _cli  # shim: tests patch cli._sync_one
    """Bring a worktree branch up to date with its base branch.

    Default: remote rebase via `gh pr update-branch --rebase` /
    `glab mr rebase` (host merges server-side). With -m/--merge: fetch,
    fast-forward the local default branch and merge it into the
    worktree's branch; the branch is pushed to origin afterwards
    (including after harness-resolved conflicts).

    Example:
      workagent sync IPG-929
      workagent sync IPG-929 --merge
      workagent sync --all --dry-run
    """
    if not isinstance(session_file, str):
        session_file = None
    links = store.load_links()
    if session_file and all_sessions:
        _fail("--session-file cannot be used with --all (one transcript per worktree — omit it and each conflict launch gets its own file)", EXIT_USAGE)
    if ref:
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            parsed_ref = None
            try:
                parsed_ref = refs.parse_ref(ref)
            except HarnessError:
                parsed_ref = None
            if parsed_ref is not None and parsed_ref["kind"] in ("pr", "mr"):
                key = _sync_create_pr_worktree(ref, parsed_ref, dry_run)
                if key is None:
                    return
                links = store.load_links()
            else:
                _fail(f"no linked state for {ref}", EXIT_USAGE)
        else:
            key = worktrees.pick_worktree(ref, resolved, links)
        keys = [key]
    elif all_sessions:
        keys = list(links)
    else:
        if not links:
            _fail("no linked worktrees", EXIT_USAGE)
        if not sys.stdin.isatty():
            _fail("no ref given and stdin is not a TTY — pass a ref or --all",
                  EXIT_USAGE)
        keys = [worktrees.pick_worktree("sync", list(links), links)]
    results = []
    for k in keys:
        entry = links[k]
        eprint(f"syncing {k} …")
        if not all_sessions and not yes and not dry_run and sys.stdin.isatty():
            if not typer.confirm(f"sync {k} ({entry.get('branch', '?')})?"):
                _fail("aborted", EXIT_USAGE)
        results.append(_cli._sync_one(k, entry, merge=merge,
                                 use_harness=harness, yes=yes or all_sessions,
                                 dry_run=dry_run, json_output=json_output,
                                 session_file=session_file))
        if all_sessions and result_failed(results[-1]):
            eprint(f"{k}: sync failed — continuing with remaining worktrees (--all)")
    if json_output:
        out = results[0] if len(results) == 1 and ref else results
        print(json.dumps(out, indent=2, ensure_ascii=False))


def _sync_create_pr_worktree(ref: str, parsed: dict, dry_run: bool) -> str | None:
    """Create the branch-keyed worktree for an unregistered PR/MR ref.

    Returns the link key (the source branch name); the shared helper
    reuses an already-recorded row when one exists. Dry-run prints the
    plan without creating anything and returns None.
    """
    tid = trackers.tracker_id(parsed)
    repo_dir, _ = trackers.resolve_for_tracker(
        tid, None, Path.cwd(), depth=7, yes=True, persist=not dry_run)
    base_branch = repos.default_branch(repo_dir)
    info, head_ref = refs.require_head_ref(parsed, repo_dir, ref)
    if dry_run:
        _print_result({"dry_run": True, "repo": str(repo_dir),
                       "pr_url": parsed["url"], "key": head_ref,
                       "branch": head_ref}, False)
        return None
    key, _worktree, _branch = _ensure_branch_worktree(
        repo_dir, head_ref, base_branch, parsed["url"])
    eprint(f"note: created worktree for {ref} on branch {head_ref}")
    return key


def result_failed(result: dict) -> bool:
    """True when a sync result reports a failure state."""
    return result.get("result") in ("conflict", "missing-worktree", "error") \
        or result.get("result", "").startswith("conflict-") \
        or result.get("result") is None


def _sync_one(key: str, entry: dict, merge: bool, use_harness: bool,
              yes: bool, dry_run: bool, json_output: bool,
              session_file: str | None = None) -> dict:
    from . import cli as _cli  # shim: tests patch cli.*
    if not isinstance(session_file, str):
        session_file = None
    wt = entry.get("worktree", "")
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    result: dict = {"key": key, "branch": branch, "strategy": None, "result": None}
    if not wt or not Path(wt).exists():
        result["result"] = "missing-worktree"
        eprint(f"{key}: worktree missing — skipped")
        return result
    dirty = sync_mod.dirty_files(Path(wt))
    if dirty:
        _fail(f"{key}: worktree has uncommitted changes: {', '.join(dirty)}",
              EXIT_GENERAL)
    cells = _cli._status_cells(entry, refresh_pr=False)
    pr = cells["pr_data"]
    tool = store.get_cached_pr_tool(branch) or _cli._repo_tool(repo)
    db = cells["base_branch"] or _cli._repo_default_branch(repo)
    # A recorded pr_url seed (state "") is display-only: it never reached a
    # live host query, so it must not drive the remote-rebase strategy.
    if merge or not pr or not pr.get("state"):
        if not db:
            _fail(f"{key}: cannot determine default branch for {repo}", EXIT_GENERAL)
        return _sync_local_merge(key, wt, branch, db, result,
                                 use_harness=use_harness, yes=yes,
                                 dry_run=dry_run, json_output=json_output,
                                 session_file=session_file)
    # Remote rebase (default): host rebases the branch on its base.
    result["strategy"] = "remote-rebase"
    if dry_run:
        result["result"] = f"would-rebase-via-{tool}"
        eprint(f"{key}: would run {tool} rebase for PR #{pr['number']} ({pr['url']})")
        return result
    try:
        sync_mod.rebase_remote(tool, pr, Path(wt))
    except HarnessError as e:
        eprint(f"{key}: {tool} rebase failed ({e}) — falling back to "
               "local merge")
        result["strategy"] = "local-merge"
        result["fallback"] = True
        if not db:
            _fail(f"{key}: cannot determine default branch for {repo}", EXIT_GENERAL)
        return _sync_local_merge(key, wt, branch, db, result,
                                 use_harness=use_harness, yes=yes,
                                 dry_run=False, json_output=json_output,
                                 session_file=session_file)
    result["result"] = "rebased"
    eprint(f"{key}: rebased PR #{pr['number']} via {tool}")
    result["pull"] = sync_mod.pull_rebased(Path(wt), branch)
    eprint(f"{key}: worktree updated from rebased origin/{branch} "
           f"({result['pull']})")
    return result


def _sync_local_merge(key: str, wt: str, branch: str, db: str, result: dict,
                      use_harness: bool, yes: bool, dry_run: bool,
                      json_output: bool, session_file: str | None = None) -> dict:
    """Local-merge flow shared by --merge, no-PR fallback, and rebase
    failure fallback."""
    from . import cli as _cli  # shim: tests patch cli._run_harness/_guard_harness
    if not isinstance(session_file, str):
        session_file = None
    result["strategy"] = "local-merge"
    if dry_run:
        result["result"] = "would-merge"
        eprint(f"{key}: would merge origin/{db} into {branch} in {wt}")
        return result
    out = sync_mod.local_merge(Path(wt), db)
    if out["status"] == "conflict":
        handled = sync_mod.auto_resolve_changelog(Path(wt), out["conflicts"])
        if not handled:
            eprint(f"{key}: conflicts in: {', '.join(out['conflicts'])}")
            run_harness_now = use_harness or yes
            if run_harness_now:
                result["result"] = "conflict-harness"
                prompt = (f"The branch {branch} has merge conflicts with "
                          f"{db} in files: {', '.join(out['conflicts'])}. "
                          "Resolve them, complete the merge, commit, push to "
                          f"origin/{branch}, and stop.")
                _cli._guard_harness(key, wt)
                _cli._run_harness("omp", prompt, wt, wt,
                             no_tty=bool(yes), no_runtime=False,
                             result=result, json_output=json_output, run_key=key,
                             session_file=session_file)
            elif sys.stdin.isatty():
                if typer.confirm("launch the harness to resolve?"):
                    result["result"] = "conflict-harness"
                    prompt = (f"The branch {branch} has merge conflicts with "
                              f"{db} in files: {', '.join(out['conflicts'])}. "
                              "Resolve them, complete the merge, commit, push to "
                              f"origin/{branch}, and stop.")
                    _cli._guard_harness(key, wt)
                    _cli._run_harness("omp", prompt, wt, wt,
                                 no_tty=False, no_runtime=False,
                                 result=result, json_output=json_output, run_key=key,
                                 session_file=session_file)
                else:
                    _fail("aborted (merge left in progress; abort with "
                          "`git merge --abort`)", EXIT_USAGE)
            else:
                result["result"] = "conflict"
                eprint(f"{key}: conflicts need human resolution — re-run "
                       "with --harness or --yes to auto-launch the harness")
            return result
        result["result"] = "merged"
    elif out["status"] == "up-to-date":
        result["result"] = "up-to-date"
        eprint(f"{key}: already up to date")
    else:
        result["result"] = "merged"
        eprint(f"{key}: merged {db} into {branch}")
    if result["result"] == "merged":
        sync_mod.push(Path(wt), branch)
        eprint(f"{key}: pushed {branch}")
        result["pushed"] = True
    return result

