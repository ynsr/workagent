"""harness serve — local web UI server (FastAPI). Imported lazily by
`harness serve` so the rest of the CLI never needs the web extra.

Security model (no authentication, by design):
- default bind 127.0.0.1; a non-loopback bind prints a startup warning
- Host header allow-list (localhost / IP literals / --allowed-host)
- JSON content-type + same-origin check on non-GET requests
- no CORS (development uses the Vite proxy)

Run lifecycle: mutating CLI commands run as child processes of this same
code version (`sys.executable -m harness`). Output is buffered per run
(≤10 000 lines, oldest dropped), ANSI-stripped, and streamed over SSE.
At most 100 runs are kept; finished runs are evicted oldest-first.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, store, worktrees

ANSI_RE = None  # set lazily in _strip_ansi (keeps import cost tiny)
MAX_LINES = 10_000
MAX_RUNS = 100
CANCEL_GRACE = 10.0


def _strip_ansi(text: str) -> str:
    import re
    return re.compile(r"\x1b\[[0-9;]*[A-Za-z]").sub("", text)


# ── command inventory (mirrors web/FEATURE_INVENTORY.md) ──────────────
# subcommand → {confirm: destructive?, force flag?, target key builder}
SPECS: dict[str, dict[str, Any]] = {
    "start": {"confirm": True, "force": False, "key": "start"},
    "review": {"confirm": True, "force": False,
               "key": lambda args: "review:all" if "--all" in args
               else "review"},
    "cleanup": {"confirm": True, "force": True,
                "key": lambda args: "cleanup:all" if "--merged" in args
                else f"cleanup:{_first_positional(args)}"},
    "sync": {"confirm": True, "force": False,
             "key": lambda args: "sync:all" if "--all" in args
             else f"sync:{_first_positional(args)}"},
    "open": {"confirm": False, "force": False,
             "key": lambda args: f"open:{_first_positional(args)}"},
    "register": {"confirm": False, "force": True, "key": "config"},
    "repo": {"confirm": False, "force": False, "key": "config"},
    "link": {"confirm": False, "force": False, "key": "config"},
}

BOOL_FLAGS: dict[str, tuple[str, ...]] = {
    "start": ("-N", "--no-tty", "--no-harness", "--dry-run", "--yes",
              "--json"),
    "review": ("-N", "--no-tty", "--no-harness", "--dry-run", "--yes",
               "--json", "--all", "--sequential", "--fix"),
    "cleanup": ("--force", "--yes", "--dry-run", "--json", "--merged"),
    "open": (),
    "sync": ("-m", "--merge", "--harness", "--all", "--yes", "--dry-run",
             "--json"),
    "register": ("--yes", "--force", "--json"),
    "repo add": ("--json",),
    "repo remove": ("--json",),
    "repo list": ("--json", "--csv"),
    "link set": ("--json",),
    "link remove": ("--json",),
    "link list": ("--worktree", "--refresh-pr", "--json", "--csv"),
}
VAL_FLAGS: dict[str, tuple[str, ...]] = {
    "start": ("--repo", "--depth", "--base", "--harness"),
    "review": ("--repo", "--depth", "--harness"),
    "sync": ("--harness",),
    "register": ("--key", "--issue", "--repo"),
    "repo add": ("--name", "--path", "--tracker"),
    "link remove": ("--repo",),
}
SUBCOMMANDS: dict[str, set[str]] = {"repo": {"add", "list", "remove"},
                                    "link": {"list", "set", "remove"}}


def _first_positional(args: list[str]) -> str:
    for a in args:
        if not a.startswith("-"):
            return a
        if a in VAL_FLAGS.get("", ()) :  # pragma: no cover
            continue
    return ""


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    args: list[str] = Field(default_factory=list)
    confirm: bool = False
    force: bool = False


def _sub_of(command: str, args: list[str]) -> str:
    if command in SUBCOMMANDS and args:
        return f"{command} {args[0]}"
    return command


def _validate_args(command: str, args: list[str]) -> None:
    if command not in SPECS:
        raise ApiError("bad_command", f"command not allowed: {command!r}", 400)
    rest = [a for a in args if a not in ("-v", "--verbose")]
    if command in SUBCOMMANDS and (
            not rest or rest[0] not in SUBCOMMANDS[command]):
        raise ApiError("bad_subcommand",
                       f"{command} needs one of: "
                       f"{', '.join(sorted(SUBCOMMANDS[command]))}", 400)
    sub = _sub_of(command, rest) if command in SUBCOMMANDS else command
    bools = set(BOOL_FLAGS.get(sub, ()))
    vals = set(VAL_FLAGS.get(sub, ()))
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in bools:
            i += 1
        elif a in vals:
            if i + 1 >= len(rest):
                raise ApiError("bad_arg", f"{a} needs a value", 400)
            value = rest[i + 1]
            if value.startswith("-"):
                raise ApiError(
                    "bad_arg",
                    f"value for {a} must not start with '-': {value!r}", 400)
            i += 2
        elif a.startswith("-"):
            raise ApiError("bad_arg", f"unknown option for {sub}: {a!r}", 400)
        else:
            i += 1


def _build_argv(command: str, args: list[str]) -> list[str]:
    """Same-code child invocation: this interpreter, `python -m harness`.

    Global flags (only -v is exposed) precede the subcommand.
    """
    argv = [sys.executable, "-m", "harness"]
    verbose = any(a in ("-v", "--verbose") for a in args)
    if verbose:
        argv.append("-v")
    argv.append(command)
    argv.extend(a for a in args if a not in ("-v", "--verbose"))
    return argv


def _target_for(command: str, args: list[str]) -> str:
    key = SPECS[command]["key"]
    rest = [a for a in args if a not in ("-v", "--verbose")]
    return key(rest) if callable(key) else key


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
    out: dict[str, Any] = {"id": run.id, "command": run.command,
                           "args": run.args, "state": run.state,
                           "exit_code": run.exit_code,
                           "truncated": run.truncated,
                           "created": run.created, "target": run.target,
                           "last_seq": run.last_seq}
    if lines is not None:
        out["lines"] = [{"seq": s, "text": t} for s, t in lines]
    return out


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


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def create_app(static_dir: Path, host: str, port: int,
               allowed_hosts: list[str]) -> FastAPI:
    app = FastAPI(title="harness", docs_url=None, redoc_url=None)
    registry = Registry()
    allowed = set(allowed_hosts)
    network_exposed = not _is_loopback(host)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host_header = request.headers.get("host", "")
        hostname = host_header.rsplit(":", 1)[0].lower() \
            if not host_header.startswith("[") else \
            host_header.split("]")[0].lstrip("[").lower()
        is_ip = _is_ip(hostname)
        if hostname != "localhost" and not is_ip \
                and hostname not in allowed:
            return JSONResponse(
                {"error": {"code": "bad_host",
                           "message": f"host not allowed: {host_header}"}},
                status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            has_body = request.headers.get("content-length", "0") not in (
                "", "0")
            ctype = request.headers.get("content-type", "").split(";")[0]
            if has_body and ctype.strip().lower() != "application/json":
                return JSONResponse(
                    {"error": {"code": "bad_content_type",
                               "message": "Content-Type must be "
                                          "application/json"}},
                    status_code=403)
            origin = request.headers.get("origin")
            if origin:
                ohost = urlparse(origin).netloc.lower()
                if ohost != host_header.lower():
                    return JSONResponse(
                        {"error": {"code": "bad_origin",
                                   "message": "Origin does not match Host"}},
                        status_code=403)
        return await call_next(request)

    # ── read endpoints (reuse in-process core functions) ──────────────
    from .cli import (
        _candidates,
        _enrich_entry,
        _session_detail,
    )
    from . import trackers as trackers_mod

    @app.get("/api/info")
    def info() -> dict:
        return {"version": __version__, "host": host, "port": port,
                "network_exposed": network_exposed}

    @app.get("/api/status")
    def status(ref: str | None = None, refresh: bool = False):
        links = store.load_links()
        if not ref:
            return {k: _enrich_entry(k, v, refresh)
                    for k, v in links.items()}
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            raise HTTPException(404, f"no linked state for {ref}")
        key = worktrees.pick_worktree(ref, resolved, links)
        return _session_detail(key, links[key], refresh=refresh)

    @app.get("/api/path")
    def path(ref: str):
        links = store.load_links()
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            raise HTTPException(404, f"no linked state for {ref}")
        key = worktrees.pick_worktree(ref, resolved, links)
        wt = links[key].get("worktree", "")
        if not wt or not Path(wt).exists():
            raise HTTPException(404, f"worktree missing for {key}: {wt}")
        return {"key": key, "worktree": wt}

    @app.get("/api/repos")
    def repos_list() -> list[dict]:
        return [{"name": n, **v}
                for n, v in store.load_config().get("repos", {}).items()]

    @app.get("/api/links")
    def links_list() -> dict:
        return {"trackers": store.load_config().get("trackers", {}),
                "sessions": {k: _enrich_entry(k, v, False)
                             for k, v in store.load_links().items()}}

    @app.get("/api/doctor")
    def doctor() -> dict:
        from . import doctor as doctor_mod
        return doctor_mod.check(json_output=True)

    @app.get("/api/issues")
    def my_issues() -> dict:
        """My open issues; 200 + warning even when the tracker CLIs are
        missing (read-only, never raises)."""
        warnings: list[str] = []
        return {"issues": trackers_mod.list_my_issues(warnings),
                "warning": "; ".join(warnings) or None}

    @app.get("/api/candidates")
    def candidates() -> dict:
        """Unlinked open PR/MRs + my recent issues (server-side 7-day
        filter; read-only)."""
        out = _candidates()
        return {"prs": out["prs"], "issues": out["issues"],
                "warnings": out["warnings"]}

    # ── runs ──────────────────────────────────────────────────────────
    @app.post("/api/runs", status_code=202)
    async def create_run(body: RunIn) -> dict:
        if body.force and not body.confirm:
            raise ApiError("force_needs_confirm",
                           "force requires confirm: true", 400)
        _validate_args(body.command, body.args)
        spec = SPECS[body.command]
        destructive = spec["confirm"] and "--dry-run" not in body.args
        if destructive and not body.confirm:
            raise ApiError(
                "confirm_required",
                f"{body.command} is destructive — pass confirm: true", 400)
        target = _target_for(body.command, body.args)
        run = registry.create(body.command, body.args, target)
        argv = _build_argv(body.command, body.args)
        tail: list[str] = []
        if body.confirm and body.command in ("start", "review", "cleanup",
                                             "sync"):
            tail.append("--yes")
        if body.force and spec["force"]:
            tail.append("--force")
        run.argv = argv + tail
        _spawn(run, registry)
        return {"run_id": run.id}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        runs = sorted(registry.runs.values(), key=lambda r: r.created)
        return [_summary(r) for r in runs]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = registry.get(run_id)
        with run.lock:
            return _summary(run, lines=list(run.lines))

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request) -> StreamingResponse:
        run = registry.get(run_id)
        last = request.headers.get("last-event-id", "")
        start_seq = int(last) if last.isdigit() else 0

        async def stream() -> Any:
            seq = start_seq
            last_beat = time.monotonic()
            while True:
                with run.lock:
                    buf = [(s, t) for s, t in run.lines if s >= seq]
                for s, t in buf:
                    seq = s + 1
                    payload = json.dumps({"seq": s, "text": t})
                    yield (f"id: {s}\nevent: log\ndata: {payload}\n\n") \
                        .encode()
                if run.state != "running":
                    payload = json.dumps({"state": run.state,
                                          "exit_code": run.exit_code})
                    yield (f"id: {run.last_seq + 1}\nevent: state\n"
                           f"data: {payload}\n\n").encode()
                    return
                if time.monotonic() - last_beat > 15:
                    last_beat = time.monotonic()
                    yield b": keepalive\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict:
        run = registry.get(run_id)
        if run.state != "running":
            raise ApiError("conflict", f"run {run_id} is not running", 409)
        _cancel(run)
        return {"id": run.id, "state": run.state}

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code,
                                       "message": exc.message}},
                            status_code=exc.status)

    # ── static SPA (index fallback for non-/api GET paths) ────────────
    index = static_dir / "index.html"

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_file() \
                and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        if not index.exists():
            raise HTTPException(404, "web UI is not built")
        return FileResponse(index)

    app.state.registry = registry
    return app


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def _is_ip(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def run_server(host: str, port: int, static_dir: Path,
               allowed_hosts: list[str]) -> None:
    try:
        import uvicorn
    except ImportError:
        print("error: the web extra is required — install with "
              "`pip install 'harness[web]'` or `uv tool install "
              "--force --with fastapi --with uvicorn .`", file=sys.stderr)
        raise SystemExit(1)
    if not _port_free(host, port):
        print(f"error: port {port} is busy — pick another with --port",
              file=sys.stderr)
        raise SystemExit(1)
    if not (static_dir / "index.html").exists():
        print("error: web UI is not built — run: cd web && npm ci && "
              "npm run build", file=sys.stderr)
        raise SystemExit(1)
    if not _is_loopback(host):
        print("WARNING: the server is reachable from the network without "
              "authentication; anyone who can reach this port can run "
              "agents with your user privileges.", file=sys.stderr)
    app = create_app(static_dir, host, port, allowed_hosts)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    registry = app.state.registry

    def handle_exit(sig: int, frame: Any) -> None:
        for run in registry.running():
            _cancel(run)
        server.should_exit = True

    import types
    server.handle_exit = types.MethodType(
        lambda self, sig, frame: handle_exit(sig, frame), server)
    server.run()
