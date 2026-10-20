"""Release gates: local worktree pre-release + pinned-SHA CI poll.

Local gate runs the repo's pre-release command inside a detached worktree
at the pinned SHA (never the user's clone). The child runs in its own
process group so a timeout kills the whole tree (SIGTERM, 10s grace,
SIGKILL). CI gate polls the pinned commit's pipeline status with
5s→30s backoff until timeout_s elapses.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import release_lock as _lock
from . import release_worktree as _wt
from .cli_core import _fail, eprint
from .errors import HarnessError

_now = time.monotonic
_sleep = time.sleep
_GRACE_S = 10

SKIP_EXIT_CODE = 10


class SkippedGate(HarnessError):
    """Pre/post script requested skip via exit 10 (carries the SKIP: reason)."""

    def __init__(self, reason: str, exit_code: int = SKIP_EXIT_CODE) -> None:
        super().__init__(reason, exit_code)
        self.reason = reason


def _run_group(argv: list[str], cwd: str, timeout_s: int,
               env_file: str | None = None,
               extra_env: dict | None = None) -> str:
    """Run argv in its own process group; timeout kills the group.

    The env file is sourced first via the parsed KEY=VAL overlay (same
    semantics as the headless harness launch) so repos.commands entries
    see the same env as login shells. *extra_env* (e.g. preview flag) is
    merged over the resolved env without affecting existing callers.
    stdout is captured and returned as text; both streams are echoed to
    our stderr so logs are preserved (stderr streams live as today).
    Exit 10 raises SkippedGate (reason = last SKIP:-prefixed line in the
    combined stdout+stderr text);
    all other non-zero exits raise HarnessError exactly as before.
    """
    from . import env as _env
    resolved = _env.resolve_env_file(env_file)
    eprint(f"sourcing env: {resolved}" if resolved else "sourcing env: none")
    merged = _env.merged_env(resolved)
    if extra_env:
        merged.update(extra_env)
    proc = subprocess.Popen(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True, env=merged,
    )
    assert proc.stdout is not None and proc.stderr is not None
    try:
        out, err = proc.communicate(timeout=timeout_s)
        if out:
            print(out, end="", file=sys.stderr, flush=True)
        if err:
            print(err, end="", file=sys.stderr, flush=True)
    except subprocess.TimeoutExpired:
        assert proc.pid is not None
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=_GRACE_S)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        raise HarnessError(f"command timed out: {' '.join(argv)}")
    if proc.returncode == SKIP_EXIT_CODE:
        reason = f"exit {SKIP_EXIT_CODE}"
        for line in ((out or "") + "\n" + (err or "")).splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("SKIP:"):
                reason = stripped.split(":", 1)[1].strip() or reason
        raise SkippedGate(reason)
    if proc.returncode != 0:
        raise HarnessError(f"{' '.join(argv)} failed with exit {proc.returncode}")
    return out or ""

def run_repo_command(repo_name: str, key: str, command: str, cwd: str,
                     timeout_s: int, env_file: str | None = None,
                     extra_env: dict | None = None) -> str:
    """Run any repos.commands entry with the env file sourced first.

    Shared choke point for every current and future commands-column
    execution: resolves the env file (None = configured default), logs
    the sourcing line, and runs via _run_group (own process group, whole
    tree killed on timeout). Returns the captured stdout text (stderr is
    echoed live, not returned). Raises HarnessError on failure/timeout
    (SkippedGate, a HarnessError subclass, on exit 10),
    usage error when the command is blank.
    """
    from . import store as _store
    if not (command or "").strip():
        _fail(f"{repo_name}: {key} command missing.", 2)
    assert command is not None
    known = set((_store.load_repos().get(repo_name) or {}).get("commands") or {})
    eprint(f"running {key} for {repo_name}: {command.strip()}"
           + ("" if key in known else f" (ad-hoc key, known: {sorted(known)})"))
    return _run_group(["/bin/sh", "-c", command.strip()], cwd, timeout_s, env_file,
               extra_env)


def run_local_gate(repo_path: str, repo_name: str, sha: str,
                   command: str, timeout_s: int,
                   env_file: str | None = None) -> None:
    """Run pre-release *command* in a detached worktree at *sha* (via run_repo_command)."""
    from .release_util import worktree_base
    dest = _wt.release_path(str(worktree_base()), repo_name)
    _wt.prepare(repo_path, dest, sha)
    try:
        run_repo_command(repo_name, "pre-release", command, str(dest),
                         timeout_s, env_file)
    except HarnessError:
        _wt.finish(dest, False)
        raise
    _wt.finish(dest, True)

def local_gate_path(repo_name: str) -> Path:
    from .release_util import worktree_base
    return _wt.release_path(str(worktree_base()), repo_name)


def _ci_sha_status(tool: str, path: str, sha: str) -> tuple[str, str]:
    """Pinned-SHA CI status: (status, url). Statuses mirror fetch_ci_status."""
    from .refs_ci import _split_remote
    try:
        remote = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                                capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        remote = ""
    _, proj = _split_remote(remote)
    if tool == "glab":
        host = (_split_remote(remote)[0] or "gitlab.com")
        out = subprocess.run(["glab", "api",
                              f"projects/{proj.replace('/', '%2F')}/repository/commits/{sha}/statuses",
                              "--hostname", host],
                             capture_output=True, text=True, timeout=60).stdout
        try:
            rows = json.loads(out or "[]")
        except json.JSONDecodeError:
            rows = []
        if not rows:
            return "not_started", ""
        states = {str(r.get("status", "")).lower() for r in rows}
        url = str((rows[0] or {}).get("target_url", "") or "")
        if states <= {"success"}:
            return "success", url
        if states & {"failed", "canceled"}:
            return "failure", url
        return "running", url
    if tool == "gh":
        out = subprocess.run(["gh", "api", f"repos/{proj}/commits/{sha}/check-runs"],
                             capture_output=True, text=True, timeout=60).stdout
        try:
            runs = (json.loads(out or "{}") or {}).get("check_runs", [])
        except json.JSONDecodeError:
            runs = []
        if not runs:
            return "not_started", ""
        conclusions = {str(r.get("conclusion", "") or "").lower() for r in runs}
        statuses = {str(r.get("status", "") or "").lower() for r in runs}
        url = str((runs[0] or {}).get("html_url", "") or "")
        if conclusions and conclusions <= {"success"}:
            return "success", url
        if conclusions & {"failure", "cancelled", "timed_out", "action_required"}:
            return "failure", url
        if statuses <= {"completed"}:
            return "failure", url
        return "running", url
    raise HarnessError(f"unknown host CLI: {tool}", exit_code=2)


def run_ci_gate(tool: str, path: str, sha: str, timeout_s: int) -> None:
    """Poll pinned-SHA CI until success/failure/timeout. Raises HarnessError."""
    deadline = _now() + timeout_s
    wait = 5.0
    last: tuple[str, str] = ("running", "")
    while True:
        status, _url = _ci_sha_status(tool, path, sha)
        last = (status, _url)
        if status == "success":
            return
        if status == "failure":
            _fail(f"CI failed for {sha}.", 1)
        if status == "not_started":
            _fail(f"no CI configured for {sha}.", 1)
        if _now() + wait > deadline:
            # one last probe at the deadline before giving up
            _sleep(max(0.0, deadline - _now()))
            status, _url = _ci_sha_status(tool, path, sha)
            if status == "success":
                return
            _fail(f"CI timed out waiting for {sha} ({timeout_s}s).", 1)
        _sleep(wait)
        wait = min(wait * 2, 30.0)
__all__ = ["run_repo_command", "run_local_gate", "run_ci_gate", "local_gate_path",
           "SKIP_EXIT_CODE", "SkippedGate", "_lock"]

