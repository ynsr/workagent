"""Supervisor shim: detach the real child so it survives serve restarts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LOG_CAP_BYTES = 1_048_576


def supervisor_argv(run_dir: Path, child_argv: list[str]) -> list[str]:
    return [sys.executable, "-m", "workagent", "_run-supervised",
            "--run-dir", str(run_dir), *child_argv]

def _append_capped(log_path: Path, chunk: bytes, state: dict) -> None:
    """Append *chunk*, head-truncating past LOG_CAP_BYTES (checked rarely).

    Sets state["truncated"] on first truncation so callers can surface it.
    """
    try:
        if state.get("checked", 0) >= 65536:
            state["checked"] = 0
            try:
                if log_path.stat().st_size > LOG_CAP_BYTES:
                    text = log_path.read_bytes()
                    log_path.write_bytes(text[-LOG_CAP_BYTES * 3 // 4:])
                    state["truncated"] = True
            except OSError:
                pass
        state["checked"] = state.get("checked", 0) + len(chunk)
        with open(log_path, "ab") as fh:
            fh.write(chunk)
    except OSError:
        pass


def run_supervised(run_dir: Path, child_argv: list[str]) -> int:
    """Detach *child_argv*, log its output, record pid + exit code.

    Returns 0 when the supervisor itself worked (child exit goes to the
    `exit_code` file); 127 when the child could not even spawn.
    """
    from . import run_store as _rs
    from . import store as _store
    run_dir = Path(run_dir)
    log_path = run_dir / "output.log"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.setsid()
    except OSError:
        pass
    try:
        proc = subprocess.Popen(child_argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True)
    except OSError as e:
        try:
            with open(log_path, "ab") as fh:
                fh.write(f"[supervisor] spawn failed: {e}\n".encode())
        except OSError:
            pass
        _rs.write_exit(run_dir, 127)
        return 127
    _rs.write_pid(run_dir, proc.pid, _store._proc_start_ticks(proc.pid))
    assert proc.stdout is not None
    state: dict = {}
    for chunk in iter(lambda: proc.stdout.read(65536), b""):
        _append_capped(log_path, chunk, state)
    if state.get("truncated"):
        try:
            (run_dir / "truncated").touch()
        except OSError:
            pass
    code = proc.wait()
    _rs.write_exit(run_dir, int(code))
    return 0
