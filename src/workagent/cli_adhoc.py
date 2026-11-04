"""`adhoc` command: run a user prompt on linked worktrees, sequentially."""
from __future__ import annotations

from typing import Optional

import typer

from . import store, worktrees
from .cli_core import EXIT_USAGE, _catch_harness_errors, _fail, _print_result, app, eprint
from .cli_harness import _guard_harness, _run_harness, append_extra_prompt


@app.command("adhoc")
@_catch_harness_errors
def adhoc(
    refs: list[str] = typer.Argument(..., help="Worktree refs (key/branch/path)."),
    prompt: str = typer.Option("", "--prompt", help="Adhoc prompt body (required)."),
    launch: bool = typer.Option(False, "--launch", help="Run headless (default when neither --launch nor --terminal)."),
    terminal: bool = typer.Option(False, "--terminal", help="Run in OS terminal (XOR --launch)."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the prompt."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before commands."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Run an adhoc prompt on one or more linked worktrees, sequentially."""
    if launch and terminal:
        _fail("--terminal cannot be used with --launch (pick one run mode)", EXIT_USAGE)
    if not (refs or []):
        _fail("at least one worktree ref is required", EXIT_USAGE)
    if not (prompt or "").strip():
        _fail("--prompt is required and must not be blank", EXIT_USAGE)
    mode = "terminal" if terminal else "headless"
    use_launch = not terminal
    links = store.load_links()
    rows: list[dict] = []
    failed = False
    for ref in refs or []:
        try:
            key, entry, _ = worktrees.resolve_any(ref, links)
        except worktrees.NoLinkedState as e:
            eprint(f"{ref}: {e} — skipping")
            rows.append({"ref": ref, "skipped": str(e)})
            failed = True
            continue
        if (entry.get("active", 1) == 0):
            eprint(f"{key}: deactivated — skipping")
            rows.append({"key": key, "skipped": "deactivated"})
            failed = True
            continue
        wt = str(entry.get("worktree", ""))
        if not worktrees.is_valid_worktree(wt):
            eprint(f"{key}: invalid worktree {wt} — skipping")
            rows.append({"key": key, "skipped": "invalid worktree"})
            failed = True
            continue
        body = (prompt or "").strip()
        if (extra_prompt or "").strip():
            body = append_extra_prompt(body, extra_prompt.strip())
        if dry_run:
            rows.append({"key": key, "worktree": wt, "mode": mode,
                         "dry_run": True, "prompt_chars": len(body)})
            continue
        import sys as _sys
        if not yes and _sys.stdin.isatty():
            eprint(f"run adhoc prompt on {key} ({wt})? [y/N] ", end="")
            if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
                _fail("aborted", EXIT_USAGE)
        _guard_harness(key, wt)
        harness_name = harness or store.load_config().get("default_harness", "omp")
        _run_harness(harness_name, body, wt, str(entry.get("repo", wt)),
                     True, use_launch, {"command": "adhoc",
                                        "session_metadata": {"action": "adhoc", "mode": mode}},
                     json_output, run_key=key,
                     session_file=session_file if isinstance(session_file, str) else None,
                     terminal=terminal, env_file=env_file)
        rows.append({"key": key, "worktree": wt, "mode": mode})
    _print_result({"results": rows}, json_output)
    if failed:
        import sys as _sys2
        _sys2.exit(1)
