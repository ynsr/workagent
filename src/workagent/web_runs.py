"""Run registry + spawn/cancel/mirror (from webapp.py).

Import cost mirrors the parent: only stdlib + dataclasses here. FastAPI
imports stay in webapp (create_app).
"""
from __future__ import annotations

import itertools
import os
import shlex
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

    Only headless-launched runs mirror: their child CLI owns the sessions
    row (created around the harness launch), so the mirror targets the
    session id matching the transcript stem. The buffered output lines go
    with it (capped at MAX_RUN_OUTPUT_LINES) so the log survives a
    restart. Preview/terminal runs already record their own runs row via
    record_preview_session — mirroring them would duplicate it. Anything
    missing (no db, no session row yet) is a silent skip — the live
    registry remains the source of truth.
    """
    if not run.session_file:
        return
    argv = run.argv or []
    if "--launch" not in argv:
        return
    try:
        from . import store_sqlite as _sq
        db = _sq.db_path()
        if not db.exists():
            return
        row = _sq.session_by_file_path(db, run.session_file, include_deleted_worktrees=True)
        if row is None and (sid := Path(run.session_file).stem):
            row = _sq.get_session(db, sid, include_deleted_worktrees=True)
        if row is None:
            return
        sid = str(row.get("id", ""))
        db_file = str(row.get("file_path") or "")
        if db_file and db_file != run.session_file:
            # The child rescoped the path (branch-less → branch-scoped, or
            # pinned → minted): point the live row at the real transcript.
            with run.lock:
                run.session_file = db_file
        # argv is [python, -m, workagent, <command>, ...] (see _build_argv);
        # fall back to the body fields when the shape is unexpected.
        if len(argv) > 3:
            command, args = argv[3], argv[4:]
        else:
            command, args = run.command, list(run.args)
        with run.lock:
            output = [text for _, text in run.lines]
            truncated = run.truncated
        final = "finished" if run.exit_code == 0 else "failed"
        try:
            if (_sq.get_session(db, sid, include_deleted_worktrees=True) or {}).get("state") == "running":
                _sq.finish_session(db, sid, final)
        except Exception:
            pass
        try:
            if any(r.get("session_id") == sid
                   for r in _sq.list_runs(db, limit=1000,
                                         include_deleted_worktrees=True)):
                # CLI headless already recorded this run (same session):
                # mirroring would duplicate it (preview-skip rule).
                return
        except Exception:
            pass
        _sq.insert_run(db, sid, command, args, run.exit_code,
                       output=output, truncated=truncated)
        try:
            is_fix_continue = (command == "review" and "--fix-comments" in args
                               and "--use-review-session" in args and run.exit_code == 0)
            if is_fix_continue and (_sq.get_session(db, sid, include_deleted_worktrees=True) or {}).get("session_type") == "review":
                from datetime import datetime, timezone
                _sq.set_session_metadata(db, sid, {
                    "review_comments_fixed_at": datetime.now(timezone.utc).isoformat(),
                    "fixed_by_run_id": run.id})
        except Exception:
            pass
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
    run_dir: str = ""

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
                           "session_id": (Path(run.session_file).stem if run.session_file else None),
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
        for row in _sq.list_runs(db, limit=1000, include_deleted_worktrees=True):
            if f"db-{row.get('id')}" == run_id:
                return row
    except Exception:
        return None
    return None




def _run_dir(run: Run) -> Path:
    """State dir for a run (cached on the Run; derived when missing)."""
    if run.run_dir:
        return Path(run.run_dir)
    from . import run_store as _rs
    return _rs.runs_root() / run.id


def _finish_state(code: int | None) -> str:
    if code == 0:
        return "succeeded"
    if code == 2:
        return "needs_input"
    if code is not None and code < 0:
        return "cancelled"
    return "failed"


def _start_tailer(run: Run, registry: Registry, backfill: bool = False) -> None:
    """Follow output.log into run.lines; close out when exit_code lands."""
    from . import run_store as _rs
    d = _run_dir(run)

    def tailer() -> None:
        offset = 0
        if backfill:
            # Post-restart adopt: load the tail (≤10k lines) without OOM.
            try:
                data = (d / "output.log").read_bytes()
                lines = data.decode(errors="replace").splitlines()[-MAX_LINES:]
                for ln in lines:
                    run.append(ln + "\n")
                offset = len(data)
            except OSError:
                offset = 0
        else:
            try:
                offset = (d / "output.log").stat().st_size
            except OSError:
                offset = 0
        while True:
            try:
                with open(d / "output.log", "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
            except OSError:
                chunk = b""
            if chunk:
                run.append(chunk.decode(errors="replace"))
                if (d / "truncated").exists():
                    run.truncated = True
            code = _rs.read_exit(d)
            if code is not None:
                with run.lock:
                    run.exit_code = code
                    if run.state == "running":
                        run.state = _finish_state(code)
                registry.release_target(run)
                _mirror_run(run)
                return
            time.sleep(0.2)

    threading.Thread(target=tailer, daemon=True).start()


def _wait_log(run: Run, needle: str, timeout: float = 5.0) -> bool:
    """Poll run.lines for *needle* (integration-test helper)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with run.lock:
            if any(needle in t for _, t in run.lines):
                return True
        time.sleep(0.05)
    return False


def _spawn(run: Run, registry: Registry) -> None:
    from . import run_store as _rs
    from .run_supervised import supervisor_argv
    d = _rs.new_run_dir(run.id)
    run.run_dir = str(d)
    with run.lock:
        created = run.created
    _rs.write_meta(d, {"command": run.command, "args": run.args,
                       "target": run.target, "created": created,
                       "session_file": run.session_file,
                       "worktree": run.worktree})
    try:
        subprocess.Popen(
            supervisor_argv(d, run.argv), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False, start_new_session=True, close_fds=True)
    except OSError as e:  # spawn failure — surface in the buffer
        run.append(f"[web] spawn failed: {e}\n")
        with run.lock:
            run.exit_code = 1
            if run.state == "running":
                run.state = "failed"
        registry.release_target(run)
        _mirror_run(run)
        return
    _start_tailer(run, registry)


def _cancel(run: Run) -> None:
    from . import run_store as _rs
    from . import store as _store
    # Fake-spawned runs (tests): keep the old in-memory branch.
    proc = run.proc
    if proc is not None:
        if proc.poll() is not None:
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
        return
    # Detached path: pid group persisted in the run dir (works post-restart).
    d = _run_dir(run)
    pid, ticks = _rs.read_pid(d)
    if pid is None:
        return
    if ticks is not None and _store._proc_start_ticks(pid) not in (None, ticks):
        # Pid reused by an unrelated process: never kill it; close the run.
        with run.lock:
            run.exit_code = -1
            if run.state == "running":
                run.state = "failed"
        _rs.write_exit(d, -1)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    def escalate_detached() -> None:
        deadline = time.time() + CANCEL_GRACE
        while time.time() < deadline:
            if not _store._pid_alive(pid):
                break
            time.sleep(0.2)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        with run.lock:
            if run.state == "running":
                run.state = "cancelled"

    threading.Thread(target=escalate_detached, daemon=True).start()
    with run.lock:
        if run.state == "running":
            run.state = "cancelled"


def _worktree_for_session(row: dict) -> str:
    wt = row.get("worktree", "")
    if wt:
        return wt
    return _worktree_for_target(row.get("worktree_ref", ""))


def _resume_shell_command(worktree: str, session_file: str,
                          env_file: str | None = None) -> str:
    """Shell command that cds to the worktree then continues the session.

    A non-empty existing transcript resumes via omp --resume; anything
    else (missing/empty/preview path) starts fresh scoped by
    --session-dir on the parent dir — omp v18+ rejects --resume on
    those (`Session ... not found` / `holds no entries`). The env file
    (explicit or configured default) is sourced first so terminal
    launches see the same env as login shells.
    """
    from pathlib import Path as _Path
    from . import env as _env
    fresh = True
    try:
        p = _Path(session_file)
        fresh = not (p.is_file() and p.stat().st_size > 0)
    except OSError:
        fresh = True
    if fresh:
        cmd = (f"cd {shlex.quote(worktree)} && omp --session-dir "
               f"{shlex.quote(str(_Path(session_file).parent))}")
    else:
        cmd = (f"cd {shlex.quote(worktree)} && omp --resume "
               f"{shlex.quote(session_file)}")
    resolved = _env.resolve_env_file(env_file)
    return _env.wrap_command(cmd, resolved)


def _extract_harness_command(texts: list[str]) -> str:
    """Last logged `harness command:` entry, rejoining quote-continued lines.

    Mirrors the RunDetail parser: the CLI preview embeds the prompt as one
    shlex-quoted argv element, so prompts containing newlines split the
    logged command across lines. Scan from the end — the last harness
    command is the runnable one.
    """
    import re

    start = -1
    for i in range(len(texts) - 1, -1, -1):
        if re.search(r"harness command:\s*\S", texts[i] or ""):
            start = i
            break
    if start < 0:
        return ""
    cmd = re.sub(r"^.*harness command:\s*", "", texts[start] or "").rstrip()
    for i in range(start + 1, len(texts)):
        if cmd.count("'") % 2 == 0:
            break
        cmd += "\n" + (texts[i] or "")
    return cmd.strip()


def _open_terminal_command(cmd: str, cwd: str = "") -> int | None:
    """Detached-spawn the OS default terminal running an arbitrary shell
    command (core moved to `cli_harness.spawn_in_terminal`; kept here as
    the web-side alias). `cwd` seeds the terminal's directory — parsed
    from a leading `cd` when omitted. Returns the spawned pid."""
    from .cli_harness import spawn_in_terminal
    return spawn_in_terminal(cmd, cwd)


def _open_terminal(worktree: str, session_file: str,
                   env_file: str | None = None) -> int | None:
    """Detached-spawn the OS default terminal resumed on the session
    (mirrors `workagent open` detachment). Returns the spawned pid."""
    return _open_terminal_command(
        _resume_shell_command(worktree, session_file, env_file), worktree)
