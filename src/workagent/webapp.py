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
from .web_runs_routes import register_runs_routes  # noqa: E402
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


from .web_runs import (  # noqa: F401,E402
    _open_terminal,
    _resume_shell_command,
    _worktree_for_session,
)

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
    def status(ref: str | None = None, refresh: bool = False, include_inactive: bool = False):
        links = store.load_links(include_inactive=include_inactive)
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
        Never touches the CWD. `repos` lists every linked repo of the
        ref's tracker (plus the worktree-pinned default when outside it)
        so a picker can offer each viable `--repo`."""
        from . import trackers as _trackers
        default, cands = _trackers.repos_for_ref(ref)
        return {"ref": ref, "repo": default, "repos": cands}

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

    register_runs_routes(app, registry)

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
