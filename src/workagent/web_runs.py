"""Run registry + spawn/cancel/mirror (from webapp.py).

Import cost mirrors the parent: only stdlib + dataclasses here. FastAPI
imports stay in webapp (create_app).
"""
from __future__ import annotations

import itertools
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .web_args import VAL_FLAGS, ApiError, _first_positional

ANSI_RE = None  # set lazily in _strip_ansi (keeps import cost tiny)
MAX_LINES = 10_000
MAX_RUNS = 100
CANCEL_GRACE = 10.0


def _strip_ansi(text: str) -> str:
    import re

    global ANSI_RE
    if ANSI_RE is None:
        ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    return ANSI_RE.sub("", text)

def _mirror_run(run: Run) -> None:
    """Best-effort mirror of a session-linked run into the runs table.

    Only runs with a session file are session-linked; the child CLI owns
    the sessions row (created around the harness launch), so the mirror
    targets the session id matching the transcript stem. The buffered
    output lines go with it (capped at MAX_RUN_OUTPUT_LINES) so the log
    survives a restart. Anything missing (no db, no session row yet, e.g.
    --no-runtime never launched) is a silent skip — the live registry
    remains the source of truth.
    """
    if not run.session_file:
        return
    try:
        from . import store_sqlite as _sq
        db = _sq.db_path()
        if not db.exists():
            return
        sid = Path(run.session_file).stem
        if _sq.get_session(db, sid) is None:
            return
        argv = run.argv or []
        # argv is [python, -m, workagent, <command>, ...] (see _build_argv);
        # fall back to the body fields when the shape is unexpected.
        if len(argv) > 3:
            command, args = argv[3], argv[4:]
        else:
            command, args = run.command, list(run.args)
        with run.lock:
            output = [text for _, text in run.lines]
            truncated = run.truncated
        _sq.insert_run(db, sid, command, args, run.exit_code,
                       output=output, truncated=truncated)
    except Exception as e:
        print(f"[web] run mirror failed: {e}", file=sys.stderr, flush=True)


def _worktree_for_target(target: str) -> str:
    """Best-effort worktree path for a run target (review:/sync:/start: keys
    strip to the recorded worktree ref)."""
    from . import store as _store
    try:
        links = _store.load_links()
    except Exception:
        return ""
    if target in links and isinstance(links[target], dict):
        wt = links[target].get("worktree", "")
        if wt:
            return wt
    for prefix in ("start:", "review:", "sync:", "cleanup:", "open:"):
        if target.startswith(prefix):
            ref = target[len(prefix):]
            if ref in links and isinstance(links[ref], dict):
                wt = links[ref].get("worktree", "")
                if wt:
                    return wt
            for key, entry in links.items():
                if not isinstance(entry, dict):
                    continue
                if key == ref or entry.get("branch") == ref \
                        or entry.get("worktree") == ref:
                    wt = entry.get("worktree", "")
                    if wt:
                        return wt
    return ""


@dataclass
class Run:
    id: str
    command: str
    args: list[str]
    target: str
    argv: list[str] = field(default_factory=list)
    state: str = "running"
    exit_code: int | None = None
    truncated: bool = False
    lines: list[tuple[int, str]] = field(default_factory=list)
    _seq: itertools.count = field(default_factory=itertools.count)
    proc: subprocess.Popen | None = None
    created: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    session_file: str = ""
    worktree: str = ""

    def append(self, text: str) -> None:
        with self.lock:
            for ln in _strip_ansi(text).splitlines():
                n = next(self._seq)
                if len(self.lines) >= MAX_LINES:
                    self.lines.pop(0)
                    self.truncated = True
                self.lines.append((n, ln))

    @property
    def last_seq(self) -> int:
        with self.lock:
            return self.lines[-1][0] if self.lines else 0

class Registry:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.targets: dict[str, str] = {}
        self.lock = threading.Lock()

    def create(self, command: str, args: list[str], target: str) -> Run:
        with self.lock:
            finished = [r for r in self.runs.values() if r.state != "running"]
            while len(self.runs) >= MAX_RUNS and finished:
                old = min(finished, key=lambda r: r.created)
                del self.runs[old.id]
                finished.remove(old)
            if target in self.targets:
                raise ApiError(
                    "conflict",
                    f"a run for target {target!r} is already in progress "
                    f"({self.targets[target]})", 409)
            run = Run(id=uuid.uuid4().hex[:12], command=command, args=args,
                      target=target)
            self.runs[run.id] = run
            self.targets[target] = run.id
            return run

    def get(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise ApiError("not_found", f"no such run: {run_id}", 404)
        return run

    def release_target(self, run: Run) -> None:
        with self.lock:
            if self.targets.get(run.target) == run.id:
                del self.targets[run.target]

    def running(self) -> list[Run]:
        with self.lock:
            return [r for r in self.runs.values() if r.state == "running"]


def _summary(run: Run, lines: list[tuple[int, str]] | None = None) -> dict:
    worktree = run.worktree
    if not worktree and run.state != "running" and run.command in ("start", "review"):
        # Replays after the child recorded its link: the global `start` key
        # can't know the worktree at spawn time.
        worktree = _worktree_for_target(f"{run.command}:{_first_positional(run.args, VAL_FLAGS[run.command])}") or ""
    out: dict[str, Any] = {"id": run.id, "command": run.command,
                           "args": run.args, "state": run.state,
                           "exit_code": run.exit_code,
                           "truncated": run.truncated,
                           "created": run.created, "target": run.target,
                           "session_file": run.session_file,
                           "worktree": worktree,
                           "last_seq": run.last_seq}
    if lines is not None:
        out["lines"] = [{"seq": s, "text": t} for s, t in lines]
    return out

def _persisted_lines(row: dict) -> list[dict]:
    """Log lines for a runs-table row (seqs rebuilt in stored order)."""
    text = str(row.get("output") or "")
    if not text:
        return []
    return [{"seq": i, "text": ln} for i, ln in enumerate(text.split("\n"))]



def _persisted_summary(row: dict) -> dict:
    """Map a runs-table row to the live Run summary shape (log included)."""
    import json
    from datetime import datetime
    args = row.get("args", [])
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = []
    exit_code = row.get("exit_code")
    state = "succeeded" if exit_code == 0 else "failed" if exit_code is not None else "failed"
    created = 0.0
    try:
        created = datetime.fromisoformat(str(row.get("created_at", ""))).timestamp()
    except Exception:
        created = 0.0
    session_file = str(row.get("session_file") or "")
    worktree = ""
    try:
        from . import store as _store
        links = _store.load_links()
        ref = str(row.get("worktree_ref") or "")
        if ref in links and isinstance(links[ref], dict):
            worktree = str(links[ref].get("worktree") or "")
        if not worktree:
            cmd = str(row.get("command") or "")
            if cmd in ("start", "review") and isinstance(args, list):
                worktree = _worktree_for_target(
                    f"{cmd}:{_first_positional(list(args), VAL_FLAGS[cmd])}") or ""
    except Exception:
        worktree = ""
    target = ""
    try:
        from .web_args import _target_for
        target = _target_for(str(row.get("command") or ""), list(args) if isinstance(args, list) else [])
    except Exception:
        target = ""
    return {"id": f"db-{row.get('id')}", "command": row.get("command"),
            "args": args if isinstance(args, list) else [], "state": state,
            "exit_code": exit_code, "truncated": bool(row.get("truncated")),
            "created": created, "target": target,
            "session_file": session_file, "worktree": worktree,
            "last_seq": max((ln["seq"] for ln in _persisted_lines(row)), default=-1) + 1,
            "session_id": row.get("session_id")}
def _persisted_row(run_id: str) -> dict | None:
    """Fetch a runs-table row for a `db-<id>` run id (None otherwise)."""
    if not run_id.startswith("db-"):
        return None
    try:
        from . import store_sqlite as _sq
        db = _sq.db_path()
        for row in _sq.list_runs(db, limit=1000):
            if f"db-{row.get('id')}" == run_id:
                return row
    except Exception:
        return None
    return None




def _spawn(run: Run, registry: Registry) -> None:
    def reader() -> None:
        code: int | None = 1
        try:
            proc = subprocess.Popen(
                run.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, shell=False, start_new_session=True,
                text=True, bufsize=1)
            run.proc = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                run.append(line)
            code = proc.wait()
        except OSError as e:  # spawn failure — surface in the buffer
            run.append(f"[web] spawn failed: {e}\n")
            code = 1
        with run.lock:
            run.exit_code = code
            if run.state == "running":
                if code == 0:
                    run.state = "succeeded"
                elif code == 2:
                    run.state = "needs_input"
                elif code is not None and code < 0:
                    run.state = "cancelled"
                else:
                    run.state = "failed"
        registry.release_target(run)
        _mirror_run(run)

    threading.Thread(target=reader, daemon=True).start()


def _cancel(run: Run) -> None:
    proc = run.proc
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    def escalate() -> None:
        try:
            proc.wait(timeout=CANCEL_GRACE)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        with run.lock:
            if run.state == "running":
                run.state = "cancelled"

    threading.Thread(target=escalate, daemon=True).start()
    with run.lock:
        if run.state == "running":
            run.state = "cancelled"


def _worktree_for_session(row: dict) -> str:
    wt = row.get("worktree", "")
    if wt:
        return wt
    return _worktree_for_target(row.get("worktree_ref", ""))


def _resume_shell_command(worktree: str, session_file: str) -> str:
    return f"cd {shlex.quote(worktree)} && omp --resume {shlex.quote(session_file)}"


def _open_terminal(worktree: str, session_file: str) -> None:
    """Detached-spawn the OS default terminal resumed on the session
    (mirrors `workagent open` detachment)."""
    cmd = _resume_shell_command(worktree, session_file)
    kwargs: dict = {"stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    if sys.platform == "darwin":
        argv = ["open", "-a", "Terminal", worktree, "--args",
                "bash", "-lc", cmd]
    elif os.name == "nt":
        argv = ["cmd", "/c", "start", "", "cmd", "/k", cmd]
    else:
        term = os.environ.get("TERMINAL", "")
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        candidates = ([term] if term else []) + [
            "gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
        for t in candidates:
            if t and shutil.which(t):
                if t == "gnome-terminal":
                    argv = [t, "--", "bash", "-lc", cmd]
                elif t == "konsole":
                    argv = [t, "-e", "bash", "-lc", cmd]
                else:
                    argv = [t, "-e", f"bash -lc {shlex.quote(cmd)}"]
                break
        else:
            if not has_display:
                raise ApiError("no_display",
                               "the server has no graphical session (no $DISPLAY/"
                               "$WAYLAND_DISPLAY) — copy the resume command instead", 500)
            raise ApiError("no_terminal",
                           "no terminal emulator found (set $TERMINAL)", 500)
    try:
        subprocess.Popen(argv, **kwargs)
    except (FileNotFoundError, OSError, PermissionError) as e:
        raise ApiError("no_terminal", f"terminal spawn failed: {e}", 500)
