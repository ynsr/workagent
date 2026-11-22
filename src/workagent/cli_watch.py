"""`workagent watch` — closed-loop PR & CI auto-watcher CLI (issue #154)."""
from __future__ import annotations

import time
from typing import Optional

import typer

from . import repos, watcher
from . import completions as _completions
from .cli_core import _catch_harness_errors, _print_result, app, eprint

_complete_repos = _completions.complete_names(repos.repo_names)


@app.command("watch")
@_catch_harness_errors
def watch(
    once: bool = typer.Option(False, "--once", help="Run a single observer pass and exit."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview watcher actions without dispatching or updating state."),
    interval: float = typer.Option(watcher.DEFAULT_WATCH_INTERVAL, "--interval", help="Polling interval in seconds when running continuously."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Limit watcher to a single registered repo."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Watch active worktrees for failing CI or unresolved PR review comments.

    Example: workagent watch --once --dry-run --json
    """
    if once or dry_run:
        res = watcher.run_watch_once(dry_run=dry_run, repo_filter=repo)
        _print_result(res, json_output)
        return

    poll = max(1.0, float(interval or watcher.DEFAULT_WATCH_INTERVAL))
    eprint(f"watching worktrees every {poll:.0f}s (Ctrl-C to stop)...")
    try:
        while True:
            res = watcher.run_watch_once(dry_run=False, repo_filter=repo)
            if json_output:
                _print_result(res, True)
            elif res["dispatched"]:
                eprint(f"watch: dispatched {res['dispatched']} fix run(s) across {res['checked']} worktree(s)")
            time.sleep(poll)
    except KeyboardInterrupt:
        eprint("watch: stopped.")
