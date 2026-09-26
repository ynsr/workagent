"""Harness launch helpers: one-live-harness guard, display cell,
re-launch in a linked worktree, and the _run_harness launch core.

Moved verbatim from cli.py (god-file split); cli.py re-exports these
names so `cli._run_harness` / `cli._guard_harness` keep working.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import backend, store
from .cli_core import _HARNESS_ARGS, _fail, _print_result, eprint


def _guard_harness(key: str | None, worktree: str) -> None:
    """Advisory pre-check: one live harness per worktree (issue #6).

    The atomic claim inside _run_harness is the real lock; this stays for
    early exits (review-all skip, --all skip, cleanup refusal) where we
    never reach a launch and must not claim a slot.
    """
    if not key:
        return
    rec = store.active_harness(key)
    if rec is None:
        # Same worktree may be recorded under a different key.
        for k, v in store.load_harnesses().items():
            if k != key and v.get("worktree") == worktree:
                rec = v
                break
    if rec is not None:
        _fail(f"worktree {worktree} already has a live harness "
              f"({rec['harness']}, pid {rec['pid']}) — wait for it to "
              "finish or kill it", 1)


def _harness_cell(key: str, worktree: str = "") -> str:
    """Display cell: "<harness> <pid>" while one is live, "" otherwise."""
    rec = store.active_harness(key)
    if rec is None and worktree:
        # The live harness may be recorded under a different key.
        for v in store.load_harnesses().values():
            if v.get("worktree") == worktree:
                rec = v
                break
    return f"{rec['harness']} {rec['pid']}" if rec else ""


def _launch_in_worktree(key: str, entry: dict, harness: str | None, no_tty: bool,
                        launch: bool, session_file: str | None, json_output: bool) -> None:
    """Re-launch the harness in an already-linked worktree (issue #26 rule 1)."""
    worktree = str(entry.get("worktree", ""))
    repo = str(entry.get("repo", worktree))
    harness_name = harness or store.load_config().get("default_harness", "omp")
    prompt = backend.prompt_for_issue(
        str(entry.get("issue", key)), "", key,
        worktree=worktree, branch=str(entry.get("branch", "")))
    result = {"worktree_path": worktree, "branch": str(entry.get("branch", "")),
              "key": key, "harness": harness_name, "reused": True}
    eprint(f"worktree: {worktree}  branch: {entry.get('branch', '')}")
    if launch:
        _guard_harness(key, worktree)
    _run_harness(harness_name, prompt, worktree, repo, no_tty, launch,
                 result, json_output, run_key=key, session_file=session_file)

def _run_harness(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                 no_tty: bool, launch: bool, result: dict, json_output: bool,
                 run_key: str | None = None, session_file: str | None = None) -> None:
    """Launch the harness in the worktree with --launch; without it print the exact
    command instead and hand the worktree to the user (shell exec on TTY).

    Every real launch carries a session file: an explicit session_file
    (CLI --session-file) wins, otherwise a path is generated under
    sessions/<harness>/ and passed to the harness (--resume for omp).
    Preview (no --launch) and --dry-run (never reaches here) write nothing.
    Sessions rows are recorded post-cutover (state.db exists) only.
    """
    from . import store_sqlite as _sq
    # Direct python-level calls (tests) bypass Typer/Click: the OptionInfo
    # default object leaks through instead of None. Normalize to None.
    if not isinstance(session_file, str):
        session_file = None
    harness = backend.get_harness(harness_name)
    preview_extra = harness.session_file_flag(session_file) if session_file else []
    preview_args = _HARNESS_ARGS + preview_extra if preview_extra else _HARNESS_ARGS
    preview_cmd = " ".join(shlex.quote(a) for a in
                           harness.command_argv(prompt, no_tty, preview_args))
    if not launch:
        # Copy-paste runnable: the harness must execute inside the worktree.
        full_cmd = f"cd {shlex.quote(worktree or fallback_dir)} && {preview_cmd}"
        eprint(f"harness command: {full_cmd}")
        result["harness_command"] = full_cmd
        _print_result(result, json_output)
        if sys.stdin.isatty():
            # "cd" for the user: replace this process with their shell in the
            # worktree; the printed harness command is theirs to run.
            backend.cd_worktree(worktree or fallback_dir)
            shell = os.environ.get("SHELL") or "/bin/sh"
            os.execvp(shell, [shell])
        return
    sid: str | None = None
    db = _sq.db_path()
    session_path = session_file or ""
    if run_key and launch:
        # Atomic one-harness-per-worktree lock: claim first, inside the same
        # flock that records it; launch only when we own the slot. A live
        # record (pid alive) fails here instead of spawning a second run.
        blocker = store.record_harness_run(run_key, harness_name, worktree or fallback_dir)
        if blocker is not None:
            _fail(f"worktree {worktree or fallback_dir} already has a live harness "
                  f"({blocker['harness']}, pid {blocker['pid']}) — wait for it to "
                  "finish or kill it", 1)
    # Every real launch gets a session file: explicit --session-file wins,
    # otherwise generate one (touched below so omp --resume can write it).
    # Pre-cutover (no state.db) there is no sessions row — the file alone
    # still lets the user resume the exact session later.
    if not session_path:
        session_path = str(_sq.session_file_path(_sq.gen_session_id(),
                                                 harness_name))
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    Path(session_path).touch(exist_ok=True)
    # Post-cutover sessions row (best effort; launch continues on failure).
    if run_key and db.exists():
        try:
            sid = _sq.insert_session(
                db, worktree_ref=run_key, harness_name=harness_name,
                initiator_command=result.get("command", harness_name),
                prompt=prompt, file_path=session_path,
                session_id=Path(session_path).stem)
        except Exception as e:
            # Post-cutover insert failure: launch continues, warn only;
            # drop the pre-created file so no orphan .jsonl remains.
            eprint(f"warning: session record failed: {e}")
            sid = None
            try:
                Path(session_path).unlink(missing_ok=True)
            except OSError:
                pass
            session_path = session_file or ""
    try:
        extra = harness.session_file_flag(session_path) if session_path else None
        backend.launch(harness_name, prompt, worktree or fallback_dir, no_tty,
                       (_HARNESS_ARGS + extra) if extra else _HARNESS_ARGS)
        if sid:
            _sq.finish_session(db, sid, "finished")
    except Exception:
        if sid:
            try:
                _sq.finish_session(db, sid, "failed")
            except Exception:
                pass
        raise
    finally:
        if run_key:
            store.clear_harness_run(run_key)
    _print_result(result, json_output)
