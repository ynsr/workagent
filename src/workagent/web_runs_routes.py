"""runs + sessions API routes (split from webapp; mounted by create_app)."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .web_args import (
    SPECS,
    VAL_FLAGS,
    ApiError,
    RunIn,
    _build_argv,
    _first_positional,
    _has_session_file,
    _session_file_arg,
    _session_file_for,
    _target_for,
    _validate_args,
)
from .web_runs import (
    _cancel,
    _persisted_lines,
    _persisted_row,
    _persisted_summary,
    _summary,
    _worktree_for_session,
    _worktree_for_target,
)


def _open_terminal(worktree: str, session_file: str) -> None:
    """Open terminal via the webapp namespace so tests patching workagent.webapp._open_terminal apply."""
    from . import webapp as _w
    return _w._open_terminal(worktree, session_file)


def _spawn(run, registry) -> None:
    """Spawn via the webapp namespace so tests patching workagent.webapp._spawn apply."""
    from . import webapp as _w
    return _w._spawn(run, registry)


def register_runs_routes(app, registry) -> None:
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
        live = [_summary(r) for r in runs]
        live_ids = {str(r["id"]) for r in live}
        persisted: list[dict] = []
        try:
            from . import store_sqlite as _sq
            db = _sq.db_path()
            for row in _sq.list_runs(db):
                if str(row["id"]) in live_ids:
                    continue
                persisted.append(_persisted_summary(row))
        except Exception:
            persisted = []
        return live + persisted
    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            run = registry.get(run_id)
        except ApiError:
            row = _persisted_row(run_id)
            if row is None:
                raise
            out = _persisted_summary(row)
            out["lines"] = _persisted_lines(row)
            return out
        with run.lock:
            return _summary(run, lines=list(run.lines))
    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request) -> StreamingResponse:
        try:
            run = registry.get(run_id)
        except ApiError:
            row = _persisted_row(run_id)
            if row is None:
                raise HTTPException(status_code=404,
                                    detail={"code": "not_found",
                                            "message": f"no such run: {run_id}"})
            lines = _persisted_lines(row)
            out = _persisted_summary(row)

            async def _replay() -> Any:
                for ln in lines:
                    payload = json.dumps({"seq": ln["seq"], "text": ln["text"]})
                    yield (f"id: {ln['seq']}\nevent: log\ndata: {payload}\n\n").encode()
                payload = json.dumps({"state": out["state"],
                                      "exit_code": out["exit_code"]})
                yield (f"id: {out['last_seq']}\nevent: state\n"
                       f"data: {payload}\n\n").encode()

            return StreamingResponse(_replay(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})
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
        Persisted runs (db-<id>) resolve from the runs table since the
        registry is empty after a restart.
        """
        try:
            run = registry.get(run_id)
        except ApiError:
            row = _persisted_row(run_id)
            if row is None:
                raise
            args = row.get("args") if isinstance(row.get("args"), list) else []
            session_file = str(row.get("session_file") or "") or _session_file_arg(
                str(row.get("command") or ""), list(args))
            if not session_file:
                raise ApiError("no_session",
                               f"run {run_id} executed no runtime session", 404)
            if not Path(session_file).exists():
                raise ApiError("missing_session",
                               f"session transcript missing: {session_file}", 404)
            worktree = _persisted_summary(row).get("worktree") or ""
            if not worktree:
                raise ApiError("no_worktree",
                               f"no worktree for run {run_id!r}", 404)
            _open_terminal(worktree, session_file)
            return {"id": run_id, "session_file": session_file,
                    "worktree": worktree}
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

