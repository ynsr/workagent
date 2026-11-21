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
    acp: bool = typer.Option(False, "--acp", help="Run this step via ACP stdio (JSON-RPC) instead of CLI argv."),
    run_mode: Optional[str] = typer.Option(None, "--run-mode", help="Run mode: preview|headless|acp|terminal (default: derived from --launch/--terminal/--acp)."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id (ACP-discovered; non-ACP harnesses pass --model through when supported)."),
    effort: Optional[str] = typer.Option(None, "--effort", help="Effort level (ACP-discovered; non-ACP harnesses pass --effort through when supported)."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the prompt."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before commands."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path."),
    continue_run: Optional[int] = typer.Option(None, "--continue-run", help="Continue from a previous run id (links continued_from_run_id and resumes ACP session when supported)."),
    follow_up: bool = typer.Option(False, "--follow-up", help="Mark this adhoc run as a follow-up turn."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Run an adhoc prompt on one or more linked worktrees, sequentially."""
    if not (refs or []):
        _fail("at least one worktree ref is required", EXIT_USAGE)
    if not (prompt or "").strip():
        _fail("--prompt is required and must not be blank", EXIT_USAGE)
    if sum(bool(x) for x in (launch, terminal, acp)) > 1:
        _fail("--launch, --terminal, and --acp are mutually exclusive", EXIT_USAGE)
    rm = (run_mode or "").strip().lower() if isinstance(run_mode, str) else ""
    mode = rm if rm in ("preview", "headless", "acp", "terminal") else (
        "acp" if acp else ("terminal" if terminal else "headless")
    )
    use_launch = mode == "headless"
    use_acp = mode == "acp"
    use_terminal = mode == "terminal"
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
        harness_name = harness or store.default_harness()
        run_meta: dict = {"action": "adhoc", "mode": mode}
        if follow_up or continue_run is not None:
            run_meta["follow_up"] = True
        run_result: dict = {"command": "adhoc", "session_metadata": run_meta}
        if isinstance(continue_run, int) and continue_run > 0:
            run_result["continued_from_run_id"] = continue_run
            run_meta["continued_from_run_id"] = continue_run
            try:
                from . import backend as _backend, store_sqlite as _sq
                db = _sq.db_path()
                prev = _sq.get_run(db, continue_run, include_deleted_worktrees=True) if db.exists() else None
                if prev:
                    prev_meta = prev.get("metadata") if isinstance(prev.get("metadata"), dict) else {}
                    prev_sid = str(prev_meta.get("acp_session_id") or "")
                    if prev_sid:
                        run_result["resume_session_id"] = prev_sid
                    prev_path = str(prev.get("file_path") or "")
                    if prev_path:
                        h = _backend.get_harness(harness_name)
                        summary = h.extract_last_assistant_text(prev_path, max_chars=2000)
                        if summary:
                            run_result["resume_context"] = (
                                f"[Continuing from run #{continue_run} — previous assistant summary]\n{summary}"
                            )
            except Exception:
                pass
        _run_harness(harness_name, body, wt, str(entry.get("repo", wt)),
                     True, use_launch, run_result,
                     json_output, run_key=key,
                     session_file=session_file if isinstance(session_file, str) else None,
                     terminal=use_terminal, env_file=env_file,
                     acp=use_acp, run_mode=run_mode, model=model, effort=effort)
        rows.append({"key": key, "worktree": wt, "mode": mode})
    _print_result({"results": rows}, json_output)
    if failed:
        import sys as _sys2
        _sys2.exit(1)
