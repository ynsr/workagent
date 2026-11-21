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
from .cli_core import _HARNESS_ARGS, _fail, _print_result, app, eprint
from .errors import HarnessError
from .web_args import ApiError

import typer


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
    # serve's unit exports NO_COLOR=1 plus WORKAGENT_SERVICE=1 (agy-guard
    # honors NO_COLOR, omp doesn't). Strip it only in that case so a
    # user-set NO_COLOR survives. Two layers: env= covers the terminal
    # process; the `unset` prefix covers emulators that reuse an existing
    # server (ptyxis/kitty/macOS open) and ignore our env.
    from_service = bool(os.environ.get("WORKAGENT_SERVICE"))
    if os.name == "posix" and from_service:
        cmd = f"unset NO_COLOR WORKAGENT_SERVICE; {cmd}"
    kwargs: dict = {"stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    if from_service:
        kwargs["env"] = {
            k: v for k, v in os.environ.items()
            if k not in ("NO_COLOR", "WORKAGENT_SERVICE")
        }
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
                        terminal: bool = False, env_file: str | None = None,
                        plan: bool = False,
                   acp: bool = False, run_mode: "str | None" = None,
                   model: "str | None" = None, effort: "str | None" = None) -> None:
    """Re-launch the harness in an already-linked worktree (issue #26 rule 1)."""
    worktree = str(entry.get("worktree", ""))
    repo = str(entry.get("repo", worktree))
    harness_name = harness or store.default_harness()
    prompt = append_extra_prompt(backend.prompt_for_issue(
        str(entry.get("issue", key)), "", key,
        worktree=worktree, branch=str(entry.get("branch", "")), plan_mode=plan), extra_prompt)
    result = {"worktree_path": worktree, "branch": str(entry.get("branch", "")),
              "key": key, "harness": harness_name, "reused": True, "command": "start"}
    eprint(f"worktree: {worktree}  branch: {entry.get('branch', '')}")
    if launch:
        _guard_harness(key, worktree)
    _run_harness(harness_name, prompt, worktree, repo, no_tty, launch,
                 result, json_output, run_key=key, session_file=session_file,
                 terminal=terminal, env_file=env_file,
                 acp=acp, run_mode=run_mode, model=model, effort=effort)


def _session_action(result: dict) -> str | None:
    """Session-type dir for a result: start/review/sync/fix_comments.

    Derived from result["command"] (+ result["fix_comments"]) — the same
    rule as store.derive_session_type. Unknown → None (no action segment).
    """
    try:
        from . import store_sqlite as _sq
        return _sq.derive_session_type(str(result.get("command", "")),
                                       bool(result.get("fix_comments")))
    except Exception:
        return None


def _rescope_session_path(session_path: str, harness_name: str, branch: str,
                          session_type: str | None = None) -> str:
    """Move a branch-less pinned path under runs/<harness>/<branch>/<action>/.

    The web server mints --session-file before the branch is known (Review
    learns the MR head branch only after fetching it). The CLI child knows
    the branch — rescope there so transcripts land branch-scoped like Start.
    The transcript id (file stem) is unchanged. No-op when the branch is
    empty or the path is already scoped.
    """
    if not session_path or not branch:
        return session_path
    try:
        from pathlib import Path as _Path
        if _Path(session_path).is_file() and _Path(session_path).stat().st_size > 0:
            return session_path
    except OSError:
        pass
    try:
        from . import store_sqlite as _sq
        rescoped = str(_sq.session_file_path(_Path(session_path).stem,
                                             harness_name,
                                             branch=branch or None,
                                             session_type=session_type))
        return rescoped if rescoped != session_path else session_path
    except Exception:
        return session_path


def _run_harness(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                 no_tty: bool, launch: bool, result: dict, json_output: bool,
                 run_key: str | None = None, session_file: str | None = None,
                 terminal: bool = False, env_file: str | None = None,
                 timeout_s: "int | None" = None,
                 acp: bool = False, run_mode: "str | None" = None,
                 model: "str | None" = None,
                 effort: "str | None" = None) -> None:
    """Launch the harness in the worktree with --launch; without it print the exact
    command instead and hand the worktree to the user (shell exec on TTY).

    Every real launch carries a transcript file: an explicit session_file
    (CLI --session-file) wins, otherwise a path is generated under
    runs/<harness>/<branch>/<action>/ and passed to the harness.
    Preview (no --launch, no --terminal) and --dry-run (never reaches
    here) write no transcript file; both persist a state='preview' run
    row via record_preview_run. --terminal spawns the harness command in
    the OS terminal and claims a terminal lock. Runs rows are recorded
    post-cutover (state.db exists) only.
    """
    from . import store_sqlite as _sq
    if not isinstance(terminal, bool):
        terminal = False
    # Direct python-level calls (tests) bypass Typer/Click: the OptionInfo
    # default object leaks through instead of None. Normalize to None.
    if not isinstance(session_file, str):
        session_file = None
    if not isinstance(env_file, str):
        env_file = None
    for _k in ("run_mode", "model", "effort"):
        _v = {"run_mode": run_mode, "model": model, "effort": effort}[_k]
        if not isinstance(_v, (str, type(None))):
            if _k == "run_mode":
                run_mode = None
            elif _k == "model":
                model = None
            else:
                effort = None
    if isinstance(acp, str):
        acp = False
    if acp and (launch or terminal):
        from .cli_core import _fail as _fail_mode, EXIT_USAGE as _EXIT_USAGE
        _fail_mode("--acp cannot be used with --launch/--terminal (pick one run mode)", _EXIT_USAGE)
    mode = (run_mode or "").strip().lower() if isinstance(run_mode, str) else ""
    if mode and mode not in ("preview", "headless", "acp", "terminal"):
        from .cli_core import _fail as _fail_mode, EXIT_USAGE as _EXIT_USAGE
        _fail_mode(f"bad --run-mode {run_mode!r}: use preview, headless, acp, or terminal", _EXIT_USAGE)
    if not mode:
        mode = "acp" if acp else ("terminal" if terminal else ("headless" if launch else "preview"))
    elif mode == "acp":
        acp = True
    elif mode == "terminal":
        terminal = True
    elif mode == "headless":
        launch = True
    if mode in ("preview", "terminal"):
        no_tty = False
    from . import env as _env
    # Env file for every launch path (preview included): explicit
    # --env-file wins, else the configured default (auto-created with
    # sane defaults on first launch). Terminal spinners source it
    # in-shell; headless children get it parsed into their environment;
    # the preview shows the exact same wrapped command.
    env_path = _env.ensure_env_file(env_file)
    harness = backend.get_harness(harness_name)
    if no_tty and launch and not terminal:
        prompt += backend.VERDICT_SUFFIX
    action = _session_action(result)
    branch0 = str(result.get("branch", "") or "")
    if result.get("reuse_run_id"):
        # Fix-continue reuses the review transcript as-is: rescoping it
        # into fix_comments/ would split one transcript across two dirs.
        pass
    elif isinstance(session_file, str) and session_file:
        rescoped = _rescope_session_path(session_file, harness_name,
                                         branch0, action)
        if rescoped != session_file:
            session_file = rescoped
            result["session_file"] = rescoped
    elif branch0:
        try:
            session_file = str(_sq.session_file_path(
                _sq.gen_session_id(), harness_name,
                branch=branch0 or None, session_type=action))
            result["session_file"] = session_file
        except Exception:
            pass
    preview_extra = harness.session_file_flag(session_file) if session_file else []
    preview_args = _HARNESS_ARGS + preview_extra if preview_extra else _HARNESS_ARGS
    preview_no_tty = no_tty and launch
    preview_cmd = " ".join(shlex.quote(a) for a in
                           harness.command_argv(prompt, preview_no_tty, preview_args,
                           model=model, effort=effort))
    # Copy-paste runnable: the harness must execute inside the worktree.
    # Terminal/headless commands source the env file first; the preview
    # shows the exact same command the user (or terminal) will run.
    full_cmd = _env.wrap_command(
        f"cd {shlex.quote(worktree or fallback_dir)} && {preview_cmd}", env_path)
    if terminal:
        # Terminal spawns detach: the harness mints its .jsonl seconds after
        # we return, so no post-launch detection is possible here. Record the
        # branch-scoped dir in the preview metadata; the next status/serve
        # read can resolve the minted file via newest_transcript.
        # Best effort — preview continues regardless.
        session_dir = ""
        bpath = ""
        try:
            from . import store_sqlite as _sq2
            bpath = _sq2.branch_path(branch0)
            d = Path(session_file).parent if session_file else None
            if d is None:
                d = _sq2.runs_dir() / harness_name
                if bpath:
                    d = d / bpath
                if action:
                    d = d / action
            d.mkdir(parents=True, exist_ok=True)
            session_dir = str(d)
        except Exception:
            session_dir = ""
        rid = record_preview_run(
            run_key or str(result.get("key", "")) or fallback_dir,
            str(result.get("command", harness_name)),
            prompt, harness_name, f"harness command: {full_cmd}",
            metadata={"session_dir": session_dir, "branch_path": bpath} if session_dir else None,
            session_file=session_file if isinstance(session_file, str) else None,
            fix_comments=bool(result.get("fix_comments")))
        if rid:
            result["run_id"] = rid
        term_cmd = full_cmd
        if rid:
            try:
                before_t = _sq.newest_transcript(Path(session_dir)) if session_dir else None
                watch_shim = [
                    sys.executable, "-m", "workagent", "watch-and-exec",
                    "--run-id", str(rid),
                    "--harness", harness_name,
                    "--workdir", worktree or fallback_dir,
                    "--session-file", session_file or "",
                    "--scope-dir", session_dir or "",
                    "--before", str(before_t) if before_t else "",
                    "--", "bash", "-lc", full_cmd,
                ]
                term_cmd = " ".join(shlex.quote(a) for a in watch_shim)
            except Exception:
                term_cmd = full_cmd
        tpid = spawn_in_terminal(term_cmd, worktree or fallback_dir)
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
        rid = record_preview_run(
            run_key or str(result.get("key", "")) or fallback_dir,
            str(result.get("command", harness_name)),
            prompt, harness_name, f"harness command: {full_cmd}",
            session_file=session_file if isinstance(session_file, str) else None,
            fix_comments=bool(result.get("fix_comments")))
        if rid:
            result["run_id"] = rid
        _print_result(result, json_output)
        if sys.stdin.isatty():
            # "cd" for the user: replace this process with their shell in the
            # worktree; the printed harness command is theirs to run.
            backend.cd_worktree(worktree or fallback_dir)
            shell = os.environ.get("SHELL") or "/bin/sh"
            os.execvp(shell, [shell])
        return
    run_id: int | None = None
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
    # Every real launch gets a transcript path: explicit --session-file wins,
    # otherwise generate one under runs/<harness>/<branch>/<action>/.
    # The parent dir is created; the file itself must NOT be pre-touched.
    # Pre-cutover (no state.db) there is no runs row — the dir alone still
    # scopes the transcript location.
    branch = str(result.get("branch", "") or "")
    reuse_run_id = result.get("reuse_run_id")
    rescoped_launch = (session_path if reuse_run_id
                       else _rescope_session_path(session_path, harness_name, branch, action))
    if rescoped_launch != session_path:
        session_path = rescoped_launch
        if isinstance(session_file, str):
            session_file = rescoped_launch
    if not session_path:
        session_path = str(_sq.session_file_path(_sq.gen_session_id(),
                                                 harness_name, branch=branch or None,
                                                 session_type=action))
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    if isinstance(session_file, str) and session_file != session_path and branch:
        # Report the rescoped path back so run summaries / resume buttons
        # carry the branch-scoped path from the very first run.
        result["session_file"] = session_path
    # Transcript chain head: a launch continuing an already-recorded transcript
    # (pinned --session-file adopted from a preview, or a fix-continue reuse)
    # links back to that transcript's first run. Exact file_path first — a
    # harness-minted filename never matches its stem — then the legacy stem.
    stype = action
    chain_root: int | None = None
    anchor = None
    if db.exists() and session_path:
        if reuse_run_id is not None:
            try:
                anchor = _sq.get_run(db, int(reuse_run_id),
                                     include_deleted_worktrees=True)
            except Exception:
                anchor = None
        if anchor is None:
            try:
                anchor = (_sq.run_by_file_path(db, session_path,
                                               include_deleted_worktrees=True)
                          or _sq.run_by_file_stem(db, Path(session_path).stem,
                                                  include_deleted_worktrees=True))
            except Exception:
                anchor = None
        if anchor is not None:
            try:
                root = (_sq.transcript_root_run(
                    db, str(anchor.get("file_path") or ""),
                    include_deleted_worktrees=True) or anchor)
            except Exception:
                root = anchor
            chain_root = int(root["id"])
    # The launch owns one runs row: state='running' until the execution
    # reports back (a 'finished' default would hide it from
    # latest_review_run, which skips running rows for fix-continue).
    # Every row this launch writes (the launch itself and its headless
    # validation attempts) carries the worktree ref when the key is a
    # recorded link, so transcript GC can attribute and purge them all.
    wt_ref: str | None = None
    if run_key and db.exists():
        try:
            from . import store as _store2
            linked = run_key in _store2.load_links(include_inactive=True,
                                                  include_deleted=True)
            wt_ref = run_key if linked else None
        except Exception:
            wt_ref = None
    if run_key and db.exists():
        try:
            run_id = _sq.insert_run(
                db, str(result.get("command", harness_name)), [run_key],
                None,
                worktree_ref=wt_ref,
                harness_name=harness_name,
                initiator_command=result.get("command", harness_name),
                prompt=prompt, file_path=session_path,
                session_type=stype,
                metadata=result.get("session_metadata"),
                state="running",
                continued_from_run_id=chain_root)
        except Exception as e:
            # Post-cutover insert failure: launch continues, warn only.
            # No transcript file is pre-created, so there is nothing to clean up.
            eprint(f"warning: run record failed: {e}")
            run_id = None
            session_path = session_file or ""
    if run_id and not result.get("session_file") and session_path:
        # First-run command with no minted transcript yet: report the pinned
        # scope path so callers carry the full path from the very first run
        # and reuse it for future runs of the same transcript.
        result["session_file"] = session_path
    # The harness mints its own transcript or mirrors it after start, so
    # the pre-launch guessed path is only a scope hint.
    # Headless (blocking): daemon watcher thread records the minted file
    # ASAP during the run. Interactive (execvp, never returns): exec goes
    # through the watch-and-exec shim, which watches then execs the harness
    # — same UX, full path in DB seconds after start. Terminal spawns detach
    # (return instantly): no watcher; metadata carries the scope dir.
    scope_dir = Path(session_path).parent if session_path else None
    before = _sq.newest_transcript(scope_dir) if scope_dir is not None else None
    stop_watch = (_watch_minted_transcript(db, scope_dir, before, run_id, result,
                                           harness_name=harness_name,
                                           workdir=worktree or fallback_dir,
                                           session_file=session_path)
                  if no_tty and run_id and getattr(harness, "watches_headless", True) else None)
    watch = ({"run_id": run_id,
              "scope_dir": str(scope_dir) if scope_dir else "",
              "before": str(before) if before else "",
              "session_file": session_path}
             if not no_tty and run_id else None)
    try:
        extra = harness.session_file_flag(session_path) if session_path else None
        _launch_kw: dict = {}
        if timeout_s is not None:
            _launch_kw["timeout_s"] = timeout_s
        exit_code = 0
        validation = None
        if no_tty and mode == "acp":
            from . import env as _env_acp
            if not getattr(harness, "supports_acp", False):
                from .cli_core import _fail as _fail_acp, EXIT_USAGE as _EXIT_ACP
                _fail_acp(f"harness {harness_name!r} does not support --acp", _EXIT_ACP)
            merged = _env_acp.merged_env(str(env_path) if env_path else None)
            run_dir = ""
            try:
                run_dir = os.environ.get("WORKAGENT_RUN_DIR", "") or ""
            except Exception:
                run_dir = ""
            code = harness.launch_acp(prompt, worktree or fallback_dir,
                                      model=model, effort=effort,
                                      session_file=session_path or None,
                                      env=merged,
                                      timeout_s=timeout_s,
                                      input_stream=(Path(run_dir) / "input.jsonl"
                                                    if run_dir else None),
                                      no_tty=True)
            if session_path:
                result["session_file"] = session_path
                if run_id:
                    try:
                        _sq.set_run_file_path(db, run_id, session_path)
                    except Exception:
                        pass
            if run_id:
                _sq.finish_run(db, run_id, "finished" if code == 0 else "failed")
            if run_key:
                store.clear_harness_run(run_key)
            _print_result(result, json_output)
            if code:
                from .errors import HarnessError as _HE
                raise _HE(f"acp run failed (exit {code})", exit_code=code or 1)
            return
        if no_tty:
            import tempfile
            from . import cli_validate as _cv, harness_summary as _hs

            current_path = session_path

            def run_once(attempt_prompt):
                nonlocal current_path
                flags = harness.session_file_flag(current_path) if current_path else []

                def on_cid(cid: str) -> None:
                    if run_id:
                        try:
                            _sq.set_run_metadata(db, run_id, {"conversation_id": cid})
                        except Exception:
                            pass
                    result["session_file"] = current_path

                code = backend.launch(harness_name, attempt_prompt, worktree or fallback_dir,
                                      True, _HARNESS_ARGS + flags,
                                      env_file=str(env_path) if env_path else None,
                                      watch=None,
                                      session_file=current_path,
                                      on_conversation_id=on_cid,
                                      model=model, effort=effort,
                                      **_launch_kw)
                minted = _sq.newest_transcript(scope_dir) if scope_dir is not None else None
                if minted is not None and minted != before:
                    current_path = str(minted)
                if current_path and Path(current_path).is_file():
                    result["session_file"] = current_path
                    if run_id:
                        _sq.set_run_file_path(db, run_id, current_path)
                text = harness.extract_last_assistant_text(current_path, max_chars=None) if current_path else ""
                return {"exit_code": code, "transcript": text,
                        "transcript_path": current_path}

            def run_judge(judge_prompt):
                with tempfile.TemporaryDirectory(prefix="workagent-judge-") as directory:
                    judge_path = str(Path(directory) / "judge.jsonl")
                    flags = harness.session_file_flag(judge_path)
                    # Bare judge session: cheap model, no skills/tools/rules/
                    # context (judge_argv) — prompt carries everything inline.
                    code = backend.launch(harness_name, judge_prompt, directory,
                                          True, _HARNESS_ARGS + harness.judge_argv + flags,
                                          env_file=str(env_path) if env_path else None,
                                          watch=None,
                                          session_file=judge_path,
                                          **_launch_kw)
                    target = (Path(judge_path)
                              if (Path(judge_path).is_file() and Path(judge_path).stat().st_size > 0)
                              else (_sq.newest_transcript(Path(directory)) if Path(directory).is_dir() else None))
                    if code != 0 or target is None:
                        raise RuntimeError(f"judge failed (exit {code}) or produced no transcript")
                    return harness.extract_last_assistant_text(str(target), max_chars=None)

            validation = _cv.validate_headless(
                worktree=worktree or fallback_dir, branch=branch, prompt=prompt,
                session_type=stype, run_once=run_once, run_judge=run_judge,
                run_db=db if db.exists() else None,
                # The attempt counter belongs to the transcript, not to this
                # invocation's fresh row: key it on the chain head so a
                # WAITING resume continues where validation stopped. A new
                # transcript has no chain yet — its fresh row is the head.
                run_id=chain_root or run_id,
                harness_name=harness_name)
            verdict = validation["verdict"]
            exit_code = 2 if verdict == "WAITING_USER_RESPONSE" else (
                0 if verdict in ("JOB_DONE", "INTENTIONALLY_UNCOMMITTED_CHANGES") else 1)
            if run_id and run_key:
                last = len(validation["runs"]) - 1
                for index, run in enumerate(validation["runs"]):
                    try:
                        _sq.insert_run(db, str(result.get("command", harness_name)),
                                       [run_key],
                                       exit_code if index == last else run["exit_code"],
                                       worktree_ref=wt_ref,
                                       harness_name=harness_name,
                                       output=[f"harness command: {full_cmd}", run["output"]],
                                       validation=run["trace"],
                                       continued_from_run_id=chain_root)
                    except Exception as e:
                        eprint(f"warning: run record failed: {e}")
        else:
            backend.launch(harness_name, prompt, worktree or fallback_dir, False,
                           (_HARNESS_ARGS + extra) if extra else _HARNESS_ARGS,
                           env_file=str(env_path) if env_path else None, watch=watch,
                           session_file=session_path,
                           model=model, effort=effort,
                           **_launch_kw)
            minted = _sq.newest_transcript(scope_dir) if scope_dir is not None else None
            if minted is not None and minted != before and run_id:
                _sq.set_run_file_path(db, run_id, str(minted))
        if run_id:
            _sq.finish_run(db, run_id, "failed" if exit_code == 1 else "finished")
        if no_tty and run_key:
            _publish_headless_summary(result, json_output, harness_name=harness_name)

    except Exception:
        if run_id:
            try:
                _sq.finish_run(db, run_id, "failed")
            except Exception:
                pass
        raise
    finally:
        if stop_watch is not None:
            stop_watch.set()
        if run_key:
            store.clear_harness_run(run_key)
    _print_result(result, json_output)
    if exit_code:
        if exit_code == 2:
            eprint(f"waiting for user response: worktree {worktree or fallback_dir}; "
                   f"transcript {result.get('session_file', session_path)}")
            raise HarnessError(
                f"waiting for user response: worktree {worktree or fallback_dir}",
                exit_code=2)
        eprint(f"headless validation failed: {validation['verdict']} "
               f"after {validation['attempts']} attempts")
        raise HarnessError(
            f"headless validation failed: {validation['verdict']} "
            f"after {validation['attempts']} attempts",
            exit_code=exit_code)


def _publish_headless_summary(result: dict, json_output: bool = False,
                              harness_name: str | None = None) -> None:
    """Print the run summary and post it to the linked issue (best effort).

    Headless runs only (no_tty): parses the last assistant text from the
    minted transcript, prints it to stdout (human) or as result["summary"]
    (--json), and posts it to the worktree record's issue_url when present.
    Never raises; failures warn on stderr.
    """
    from . import issue_comments as _ic
    summary = ""
    try:
        session_file = str(result.get("session_file", "") or "")
        if session_file:
            h_name = harness_name or str(result.get("harness") or "") or store.default_harness()
            harness_obj = backend.get_harness(h_name)
            summary = harness_obj.extract_last_assistant_text(session_file)
    except Exception as e:
        eprint(f"warning: summary parse failed: {e}")
    if not summary:
        summary = (f"headless {result.get('command', 'harness')} run finished "
                   f"(branch {result.get('branch', '')}); no transcript summary.")
    if json_output:
        result["summary"] = summary
    else:
        print(summary)
    key = str(result.get("key", "") or "")
    if not key:
        return
    try:
        entry = store.lookup_link(key) or {}
    except Exception as e:
        eprint(f"warning: link lookup failed for {key}: {e}")
        return
    issue_url = str(entry.get("issue_url", "") or "")
    if issue_url:
        _ic.post_issue_comment(issue_url, summary)


def _watch_minted_transcript(db, scope_dir, before, run_id, result,
                             harness_name: str | None = None,
                             workdir: str = "", session_file: str = ""):
    """Poll for the harness-minted transcript or conversation; record it ASAP.

    Returns a threading.Event the caller sets when the launch returns
    (stops the daemon). Never raises; all DB writes best-effort.
    Only started for blocking headless launches (returning control).
    """
    import threading
    import time
    stop = threading.Event()
    if run_id is None:
        return stop

    h_name = harness_name or store.default_harness()
    harness_obj = backend.get_harness(h_name)

    def _poll() -> None:
        try:
            harness_obj.watch_transcript(
                workdir, scope_dir, before, run_id, db,
                result=result, session_file=session_file, launched_at=time.time(),
                stop_event=stop,
            )
        except Exception:
            pass

    t = threading.Thread(target=_poll, name="workagent-minted-watch",
                         daemon=True)
    t.start()
    return stop


def record_headless_attempt(key: str, command: str, args: list[str],
                            output: str, exit_code: int = 1,
                            harness_name: str = "",
                            session_file: str | None = None,
                            prompt: str = "",
                            metadata: dict | None = None,
                            fix_comments: bool = False) -> int | None:
    """Persist a failed-before-launch headless attempt as one run row.

    Pre-link failures (no worktree row for the key yet) persist too —
    runs carry no worktree FK, so the row survives with a dangling ref
    until the worktree is hard-deleted. Same ``harness command:`` output
    contract as previews. Never raises.
    """
    return record_preview_run(
        key, command, prompt, harness_name, output,
        exit_code=exit_code, args=args,
        metadata={**(metadata or {}), "origin": "headless-attempt"},
        session_file=session_file, fix_comments=fix_comments)


def record_preview_run(key: str, command: str, prompt: str,
                       harness_name: str, output: str,
                       exit_code: int = 0,
                       args: list[str] | None = None,
                       metadata: dict | None = None,
                       session_file: str | None = None,
                       fix_comments: bool = False) -> int | None:
    """Persist a preview (no-launch) invocation as one run row.

    None pre-cutover (no state.db). Preview = transcript identity + full
    prompt stored, state='preview' — one row, no separate session entity.
    session_type is derived from the command + the *fix_comments*
    discriminator (`review --fix-comments` types as fix_comments, not
    review; unknown → NULL, never guessed). Never raises: warns to stderr
    so callers keep their own result flow.

    Contract: preview callers (start/review/terminal) MUST pass output
    starting with the literal ``harness command: `` prefix — the
    RunDetail parser and ``/api/runs/{id}/start-terminal`` only find the
    command through it. Bare-command output renders as terminal output
    after a reload.

    An explicit *session_file* (the web server's minted --session-file, or
    CLI --session-file) pins the transcript: the first run command already
    carries the full path and later runs of the same transcript reuse it.
    Without it file_path stays "" until the harness mints a transcript.
    A pinned path that already matches a run (fix-continue reusing the
    review transcript, a preview rerun) does not re-declare the transcript
    identity: only the invocation row is added, linked to that
    transcript's first run via ``continued_from_run_id`` — and that row
    carries the derived session_type too (it has none of its own) while
    the adopted row keeps its state (a finished review row must not flip
    back to preview). The invocation row still carries its own worktree
    attribution (worktree_ref, harness_name, initiator command, prompt):
    only the transcript identity (file_path) stays with the adopted row.
    """
    from . import store_sqlite as _sq
    try:
        db = _sq.db_path()
        if not db.exists():
            return None
        try:
            from . import store as _store
            if key and key not in _store.load_links(include_inactive=True,
                                                   include_deleted=True):
                # Dangling past ref (hard-deleted worktree): the ref no
                # longer names anything — persist unattached ('' ref).
                key = ""
        except Exception:
            pass
        meta = {"origin": "preview"}
        if metadata:
            meta.update(metadata)
        pinned = session_file if isinstance(session_file, str) and session_file else ""
        run_args = args if args is not None else [key]
        stype = _sq.derive_session_type(command, fix_comments)
        if pinned:
            try:
                existing = (_sq.run_by_file_path(db, pinned,
                                                 include_deleted_worktrees=True)
                            or _sq.run_by_file_stem(db, Path(pinned).stem,
                                                    include_deleted_worktrees=True))
            except Exception:
                existing = None
            if existing is not None:
                try:
                    root = (_sq.transcript_root_run(
                        db, str(existing.get("file_path") or ""),
                        include_deleted_worktrees=True) or existing)
                except Exception:
                    root = existing
                return _sq.insert_run(
                    db, command, run_args, exit_code,
                    worktree_ref=key or None, harness_name=harness_name,
                    initiator_command=command, prompt=prompt,
                    metadata=meta, state="preview", session_type=stype,
                    continued_from_run_id=int(root["id"]),
                    output=[output])
        return _sq.insert_run(
            db, command, run_args, exit_code,
            worktree_ref=key or None, harness_name=harness_name,
            initiator_command=command, prompt=prompt, file_path=pinned,
            session_type=stype,
            metadata=meta, state="preview", output=[output])
    except Exception as e:
        eprint(f"warning: preview run record failed: {e}")
        return None

@app.command("watch-and-exec", hidden=True)
def watch_and_exec(
    run_id: int = typer.Option(..., "--run-id", help="Run row id to update."),
    harness: str = typer.Option("", "--harness", help="AI harness name (empty = default)."),
    workdir: str = typer.Option("", "--workdir", help="Working directory."),
    session_file: str = typer.Option("", "--session-file", help="Pinned session file path."),
    scope_dir: str = typer.Option("", "--scope-dir", help="Branch-scoped transcript dir to watch."),
    before: str = typer.Option("", "--before", help="Pre-launch newest transcript (empty = none)."),
    argv: list[str] = typer.Argument(..., help="Harness argv after --."),
) -> None:
    """Watch transcript, record it ASAP, exec *argv*.

    Hidden shim: `backend.launch` execs through here for interactive runs so
    the minted transcript lands in the DB seconds after start even though the
    parent process is replaced. Signals need no forwarding — exec replaces
    this process with the harness, restoring native semantics.
    """
    import time
    from . import store_sqlite as _sq
    harness_name = harness or store.default_harness()
    w_dir = workdir or os.getcwd()
    scope = Path(scope_dir) if scope_dir else None
    before_p = Path(before) if before else None
    harness_obj = backend.get_harness(harness_name)
    db = _sq.db_path()

    if hasattr(os, "fork"):
        parent_pid = os.getpid()
        pid = os.fork()
        if pid == 0:
            import threading
            stop_event = threading.Event()

            def _parent_monitor():
                deadline = time.time() + 86400.0
                while not stop_event.is_set() and time.time() < deadline:
                    if os.getppid() != parent_pid:
                        stop_event.set()
                        break
                    time.sleep(1.0)
                stop_event.set()

            monitor = threading.Thread(target=_parent_monitor, name="parent-liveness-monitor", daemon=True)
            monitor.start()
            try:
                harness_obj.watch_transcript(
                    w_dir, scope, before_p, run_id, db,
                    session_file=session_file, launched_at=time.time(),
                    stop_event=stop_event,
                )
            except Exception:
                pass
            finally:
                os._exit(0)
        else:
            os.execvp(argv[0], argv)
    else:
        import threading
        stop = threading.Event()
        t = threading.Thread(
            target=lambda: harness_obj.watch_transcript(
                w_dir, scope, before_p, run_id, db,
                session_file=session_file, launched_at=time.time(),
                stop_event=stop,
            ),
            name="workagent-watch-thread", daemon=True
        )
        t.start()
        os.execvp(argv[0], argv)

@app.command("_run-supervised", hidden=True,
              context_settings={"allow_extra_args": True,
                                "ignore_unknown_options": True})
def run_supervised_cmd(
    ctx: typer.Context,
    run_dir: str = typer.Option(..., "--run-dir", help="Run state dir."),
) -> None:
    """Detach *argv* so it survives serve restarts; log + record exit.

    Hidden shim: `web_runs._spawn` runs through here for every /api/runs
    child. The supervisor double-detaches (setsid + own group), streams
    child output to output.log, and writes pid/exit_code files.
    """
    from pathlib import Path as _Path
    from .run_supervised import run_supervised as _run
    child = [a for a in (ctx.args or []) if a != "--"]
    raise SystemExit(_run(_Path(run_dir), child))
