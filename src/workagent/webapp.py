"""workagent serve — local web UI server (FastAPI). Imported lazily by
`workagent serve` so the rest of the CLI never needs the web extra.

Security model (no authentication, by design):
- default bind 127.0.0.1; a non-loopback bind prints a startup warning
- Host header allow-list (localhost / IP literals / --allowed-host)
- JSON content-type + same-origin check on non-GET requests
- no CORS (development uses the Vite proxy)

Run lifecycle: mutating CLI commands run as child processes of this same
code version (`sys.executable -m workagent`). Output is buffered per run
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



def _strip_ansi(text: str) -> str:
    import re
    return re.compile(r"\x1b\[[0-9;]*[A-Za-z]").sub("", text)


from .web_args import (  # noqa: F401
    BOOL_FLAGS,
    SPECS,
    SUBCOMMANDS,
    VAL_FLAGS,
    ApiError,
    RunIn,
    _build_argv,
    _first_positional,
    _has_session_file,
    _session_file_arg,
    _session_file_for,
    _sub_of,
    _target_for,
    _validate_args,
)
from .web_runs import (  # noqa: F401  (test compatibility)
    CANCEL_GRACE,
    MAX_LINES,
    MAX_RUNS,
    Registry,
    Run,
    _cancel,
    _mirror_run,
    _spawn,
    _strip_ansi,
    _summary,
    _worktree_for_target,
)

def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


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


def create_app(static_dir: Path, host: str, port: int,
               allowed_hosts: list[str]) -> FastAPI:
    app = FastAPI(title="workagent", docs_url=None, redoc_url=None)
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
        _fetch_origins,
        _session_detail,
    )
    from . import trackers as trackers_mod

    @app.get("/api/status")
    def status(ref: str | None = None, refresh: bool = False):
        links = store.load_links()
        if not ref:
            _fetch_origins(links, refresh)
            return {k: _enrich_entry(k, v, refresh)
                    for k, v in links.items()}
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            raise HTTPException(404, f"no linked state for {ref}")
        key = worktrees.pick_worktree(ref, resolved, links)
        _fetch_origins({key: links[key]}, refresh)
        return _session_detail(key, links[key], refresh=refresh)

    @app.get("/api/info")
    def info() -> dict:
        return {"version": __version__, "host": host, "port": port,
                "network_exposed": network_exposed}

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
        return [{"name": n, "path": v.get("path", ""),
                 "tracker": v.get("tracker", ""),
                 "trackers": v.get("trackers", [v.get("tracker", "")] if v.get("tracker") else []),
                 **{k: val for k, val in v.items() if k not in ("path", "tracker", "trackers")}}
                for n, v in store.load_repos().items()]

    @app.get("/api/trackers")
    def trackers_list() -> dict:
        """Issue trackers with linked-repo counts (CRUD page source)."""
        from . import store_sqlite as _sq
        db = _sq.db_path()
        if db.exists():
            return {"trackers": _sq.load_tracker_rows(db)}
        return {"trackers": [
            {"key": t, "vendor": "", "remote_url": "",
             "repos": len((v or {}).get("repos", []))}
            for t, v in store.load_trackers().items()]}
    @app.get("/api/links")
    def links_list() -> dict:
        return {"trackers": store.load_trackers(),
                "worktrees": {k: _enrich_entry(k, v, False)
                             for k, v in store.load_links().items()}}

    @app.get("/api/default-repo")
    def default_repo(ref: str) -> dict:
        """Issue #26 default repo for *ref* — linked-worktree repo first,
        else the tracker's single linked repo; "" when ambiguous/unknown.
        Never touches the CWD."""
        from . import trackers as _trackers
        return {"ref": ref, "repo": _trackers.default_repo_for_ref(ref)}

    @app.get("/api/doctor")
    def doctor() -> dict:
        from . import doctor as doctor_mod
        return doctor_mod.check(json_output=True)

    @app.get("/api/issues")
    def my_issues(force: bool = False) -> dict:
        """My open issues; 200 + warning even when the tracker CLIs are
        missing (read-only, never raises)."""
        warnings: list[str] = []
        return {"issues": trackers_mod.list_my_issues(warnings, force=force),
                "warning": "; ".join(warnings) or None}

    @app.get("/api/candidates")
    def candidates(force: bool = False) -> dict:
        """Unlinked open PR/MRs + my recent issues (server-side 7-day
        filter) + unregistered on-disk worktrees; read-only."""
        out = _candidates(force=force)
        return {"prs": out["prs"], "issues": out["issues"],
                "worktrees": out["worktrees"], "warnings": out["warnings"]}

    @app.get("/api/sessions")
    def sessions_list() -> dict:
        """Persisted harness sessions (newest first); [] pre-migration."""
        from . import store_sqlite as sq
        db = sq.db_path()
        if not db.exists():
            return {"sessions": []}
        rows = [{k: v for k, v in r.items() if k != "prompt"}
                for r in sq.list_sessions(db)]
        return {"sessions": rows}

    @app.get("/api/sessions/{sid}")
    def session_detail(sid: str) -> dict:
        from . import store_sqlite as sq
        from pathlib import Path
        db = sq.db_path()
        row = sq.get_session(db, sid) if db.exists() else None
        if row is None:
            raise HTTPException(404, f"no session {sid}")
        if row.get("file_path") and not Path(row["file_path"]).exists():
            row["transcript"] = "missing"
        return row

    # ── runs ──────────────────────────────────────────────────────────
    @app.post("/api/runs", status_code=202)
    async def create_run(body: RunIn) -> dict:
        if body.force and not body.confirm:
            raise ApiError("force_needs_confirm",
                           "force requires confirm: true", 400)
        _validate_args(body.command, body.args, body_force=body.force)
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
        if body.command in ("start", "review") \
                and "--dry-run" not in body.args \
                and not _has_session_file(body.command, body.args):
            # --no-runtime gets a path too: the CLI preview carries it as
            # --resume (creating nothing), so resume/copy buttons work once
            # the user runs the printed command manually.
            from . import store_sqlite as _sq
            session_file = _session_file_for(_sq.gen_session_id())
            tail += ["--session-file", session_file]
            run.session_file = session_file
        else:
            run.session_file = _session_file_arg(body.command, body.args) or ""
        if body.command in ("start", "review", "sync"):
            run.worktree = _worktree_for_target(target) or ""
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

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict:
        """Open the OS default terminal resumed on this run's session.

        Non-destructive (same class as `open`): no confirm needed. 404 when
        the run executed no runtime session or the transcript is missing.
        """
        # 404 not_found when unknown (e.g. server restarted since the run).
        run = registry.get(run_id)
        session_file = run.session_file \
            or _session_file_arg(run.command, run.args)
        if not session_file:
            raise ApiError("no_session",
                           f"run {run_id} executed no runtime session", 404)
        if not Path(session_file).exists():
            raise ApiError("missing_session",
                           f"session transcript missing: {session_file}", 404)
        worktree = run.worktree or _worktree_for_target(run.target)
        if not worktree and run.command in ("start", "review"):
            worktree = _worktree_for_target(f"{run.command}:{_first_positional(run.args, VAL_FLAGS[run.command])}")
        if not worktree:
            raise ApiError("no_worktree",
                           f"no worktree for target {run.target!r}", 404)
        _open_terminal(worktree, session_file)
        return {"id": run.id, "session_file": session_file,
                "worktree": worktree}

    @app.post("/api/sessions/{sid}/resume")
    async def resume_session(sid: str) -> dict:
        """Open the OS default terminal resumed on a persisted session."""
        from . import store_sqlite as _sq
        db = _sq.db_path()
        row = _sq.get_session(db, sid) if db.exists() else None
        if row is None:
            raise ApiError("not_found", f"no session {sid}", 404)
        session_file = row.get("file_path", "")
        if not session_file or not Path(session_file).exists():
            raise ApiError("missing_session",
                           f"session transcript missing: {session_file}", 404)
        worktree = _worktree_for_session(row) or ""
        if not worktree:
            raise ApiError("no_worktree",
                           f"no worktree for session {sid}", 404)
        _open_terminal(worktree, session_file)
        return {"id": sid, "session_file": session_file,
                "worktree": worktree}

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
              "`pip install 'workagent[web]'` or `uv tool install "
              "--force --from '.[web]' workagent`", file=sys.stderr)
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
