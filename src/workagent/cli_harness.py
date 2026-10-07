"""Harness launch helpers: one-live-harness guard, display cell,
re-launch in a linked worktree, and the _run_harness launch core.

Moved verbatim from cli.py (god-file split); cli.py re-exports these
names so `cli._run_harness` / `cli._guard_harness` keep working.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import backend, store
from .cli_core import _HARNESS_ARGS, _fail, _print_result, eprint
from .web_args import ApiError


def append_extra_prompt(prompt: str, extra_prompt: str | None) -> str:
    """Append user-supplied extra instructions to a harness prompt."""
    if not isinstance(extra_prompt, str):
        return prompt
    extra = extra_prompt.strip()
    return prompt if not extra else f"{prompt}\n\n{extra}"


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
    if rec is not None and rec.get("origin") == "terminal":
        return f"terminal {rec['pid']}"
    return f"{rec['harness']} {rec['pid']}" if rec else ""


def spawn_in_terminal(cmd: str, cwd: str = "") -> int | None:
    """Detached-spawn the OS default terminal running *cmd*; returns the
    terminal process pid (mirrors `workagent open` detachment). `cwd`
    seeds the terminal's directory — parsed from a leading `cd` when
    omitted. Shared by the CLI `--terminal` run-mode and the web Run page.
    """
    if not cwd:
        try:
            parts = shlex.split(cmd, posix=True)
            if len(parts) >= 2 and parts[0] == "cd":
                cwd = parts[1]
        except ValueError:
            cwd = ""
    kwargs: dict = {"stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    if sys.platform == "darwin":
        argv = ["open", "-a", "Terminal", *([cwd] if cwd else []),
                "--args", "bash", "-lc", cmd]
    elif os.name == "nt":
        argv = ["cmd", "/c", "start", "", "cmd", "/k", cmd]
    else:
        term = os.environ.get("TERMINAL", "")
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        for t in ([term] if term else []) + [
            "x-terminal-emulator", "ptyxis", "gnome-terminal", "kgx",
            "konsole", "xfce4-terminal", "alacritty", "kitty",
            "wezterm", "foot", "terminator", "xterm"]:
            if t and shutil.which(t):
                if t == "gnome-terminal":
                    argv = [t, "--", "bash", "-lc", cmd]
                elif t in ("konsole", "xfce4-terminal"):
                    argv = [t, "-e", "bash", "-lc", cmd]
                elif t == "ptyxis":
                    argv = [t, "-x", "bash", "-lc", cmd]
                elif t in ("kitty", "wezterm", "foot"):
                    argv = [t, "bash", "-lc", cmd]
                else:
                    argv = [t, "-e", "bash", "-lc", cmd]
                break
        else:
            if not has_display:
                raise ApiError("no_display",
                               "the server has no graphical session (no $DISPLAY/"
                               "$WAYLAND_DISPLAY) — copy the harness command instead", 500)
            raise ApiError("no_terminal",
                           "no terminal emulator found (set $TERMINAL)", 500)
    try:
        return subprocess.Popen(argv, **kwargs).pid
    except (FileNotFoundError, OSError, PermissionError) as e:
        raise ApiError("no_terminal", f"terminal spawn failed: {e}", 500)


def _launch_in_worktree(key: str, entry: dict, harness: str | None, no_tty: bool,
                        launch: bool, session_file: str | None, json_output: bool,
                        extra_prompt: str | None = None,
                        terminal: bool = False) -> None:
    """Re-launch the harness in an already-linked worktree (issue #26 rule 1)."""
    worktree = str(entry.get("worktree", ""))
    repo = str(entry.get("repo", worktree))
    harness_name = harness or store.load_config().get("default_harness", "omp")
    prompt = append_extra_prompt(backend.prompt_for_issue(
        str(entry.get("issue", key)), "", key,
        worktree=worktree, branch=str(entry.get("branch", ""))), extra_prompt)
    result = {"worktree_path": worktree, "branch": str(entry.get("branch", "")),
              "key": key, "harness": harness_name, "reused": True}
    eprint(f"worktree: {worktree}  branch: {entry.get('branch', '')}")
    if launch:
        _guard_harness(key, worktree)
    _run_harness(harness_name, prompt, worktree, repo, no_tty, launch,
                 result, json_output, run_key=key, session_file=session_file,
                 terminal=terminal)


def _run_harness(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                 no_tty: bool, launch: bool, result: dict, json_output: bool,
                 run_key: str | None = None, session_file: str | None = None,
                 terminal: bool = False) -> None:
    """Launch the harness in the worktree with --launch; without it print the exact
    command instead and hand the worktree to the user (shell exec on TTY).

    Every real launch carries a session file: an explicit session_file
    (CLI --session-file) wins, otherwise a path is generated under
    sessions/<harness>/ and passed to the harness (--resume for omp).
    Preview (no --launch, no --terminal) and --dry-run (never reaches
    here) write no transcript file; both persist a state='preview'
    session row + runs row via record_preview_session. --terminal spawns
    the harness command in the OS terminal and claims a terminal lock.
    Sessions rows are recorded post-cutover (state.db exists) only.
    """
    from . import store_sqlite as _sq
    if not isinstance(terminal, bool):
        terminal = False
    # Direct python-level calls (tests) bypass Typer/Click: the OptionInfo
    # default object leaks through instead of None. Normalize to None.
    if not isinstance(session_file, str):
        session_file = None
    harness = backend.get_harness(harness_name)
    preview_extra = harness.session_file_flag(session_file) if session_file else []
    preview_args = _HARNESS_ARGS + preview_extra if preview_extra else _HARNESS_ARGS
    preview_no_tty = no_tty and launch
    preview_cmd = " ".join(shlex.quote(a) for a in
                           harness.command_argv(prompt, preview_no_tty, preview_args))
    # Copy-paste runnable: the harness must execute inside the worktree.
    full_cmd = f"cd {shlex.quote(worktree or fallback_dir)} && {preview_cmd}"
    if terminal:
        sid = record_preview_session(
            run_key or str(result.get("key", "")) or fallback_dir,
            str(result.get("command", harness_name)),
            prompt, harness_name, f"harness command: {full_cmd}")
        if sid:
            result["session_id"] = sid
        tpid = spawn_in_terminal(full_cmd, worktree or fallback_dir)
        key_t = run_key or str(result.get("key", "")) or ""
        if key_t:
            blocker = store.record_harness_run(
                key_t, harness_name, worktree or fallback_dir,
                origin="terminal", pid=tpid if isinstance(tpid, int) else None)
            if blocker is not None:
                _fail(f"worktree {worktree or fallback_dir} already has a live harness "
                      f"({blocker['harness']}, pid {blocker['pid']}) — wait for it to "
                      "finish or kill it", 1)
        result["terminal_pid"] = tpid
        eprint(f"terminal agent pid: {tpid}")
        _print_result(result, json_output)
        return
    if not launch:
        eprint(f"harness command: {full_cmd}")
        result["harness_command"] = full_cmd
        sid = record_preview_session(
            run_key or str(result.get("key", "")) or fallback_dir,
            str(result.get("command", harness_name)),
            prompt, harness_name, f"harness command: {full_cmd}")
        if sid:
            result["session_id"] = sid
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
    # Every real launch gets a session path: explicit --session-file wins,
    # otherwise generate one under sessions/<harness>/. Fresh (missing or
    # empty) paths are passed as --session-dir (omp v18+ rejects --resume
    # on them); only non-empty transcripts resume via --resume (see
    # backend.session_file_flag). The parent dir is created; the file
    # itself must NOT be pre-touched — omp also rejects empty transcripts
    # (`holds no entries`). Pre-cutover (no state.db) there is no
    # sessions row — the dir alone still scopes the transcript location.
    if not session_path:
        session_path = str(_sq.session_file_path(_sq.gen_session_id(),
                                                 harness_name))
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    # Post-cutover sessions row (best effort; launch continues on failure).
    reuse_sid = result.get("reuse_session_id")
    stype = _sq.derive_session_type(str(result.get("command", "")),
                                    bool(result.get("fix_comments")))
    if run_key and db.exists() and not reuse_sid:
        try:
            sid = _sq.insert_session(
                db, worktree_ref=run_key, harness_name=harness_name,
                initiator_command=result.get("command", harness_name),
                prompt=prompt, file_path=session_path,
                session_id=Path(session_path).stem,
                session_type=stype,
                metadata=result.get("session_metadata"))
        except Exception as e:
            # Post-cutover insert failure: launch continues, warn only.
            # No transcript file is pre-created (omp owns the filename
            # under --session-dir), so there is nothing to clean up.
            eprint(f"warning: session record failed: {e}")
            sid = None
            session_path = session_file or ""
    finish_sid = sid or (reuse_sid if isinstance(reuse_sid, str) else None)
    try:
        extra = harness.session_file_flag(session_path) if session_path else None
        backend.launch(harness_name, prompt, worktree or fallback_dir, no_tty,
                       (_HARNESS_ARGS + extra) if extra else _HARNESS_ARGS)
        if finish_sid:
            _sq.finish_session(db, finish_sid, "finished")
    except Exception:
        if finish_sid:
            try:
                _sq.finish_session(db, finish_sid, "failed")
            except Exception:
                pass
        raise
    finally:
        if run_key:
            store.clear_harness_run(run_key)
    _print_result(result, json_output)

def record_preview_session(key: str, command: str, prompt: str,
                           harness_name: str, output: str,
                           exit_code: int = 0,
                           args: list[str] | None = None) -> str | None:
    """Persist a preview (no-launch) session + run; None when pre-cutover.

    Preview = DB row + full prompt stored, state='preview', empty
    file_path, plus a runs row (every work run must produce a runs
    record). session_type is derived from the command (start/review/sync;
    unknown → NULL, never guessed). Never raises: warns to stderr so
    callers keep their own result flow.

    Contract: preview callers (start/review/terminal) MUST pass output
    starting with the literal ``harness command: `` prefix — the
    RunDetail parser and ``/api/runs/{id}/start-terminal`` only find the
    command through it. Bare-command output renders as terminal output
    after a reload.
    """
    from . import store_sqlite as _sq
    try:
        db = _sq.db_path()
        if not db.exists():
            return None
        sid = _sq.insert_session(
            db, worktree_ref=key, harness_name=harness_name,
            initiator_command=command, prompt=prompt,
            file_path="", session_type=_sq.derive_session_type(command),
            metadata={"origin": "preview"})
        _sq.finish_session(db, sid, "preview")
        _sq.insert_run(db, sid, command, args if args is not None else [key],
                       exit_code, output=[output])
        return sid
    except Exception as e:
        eprint(f"warning: session record failed: {e}")
        return None
