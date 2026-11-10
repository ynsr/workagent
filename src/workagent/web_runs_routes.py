"""runs + sessions API routes (split from webapp; mounted by create_app)."""
from __future__ import annotations

import asyncio
import json
import sys
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
    _needs_confirm,
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


def _message_needs_yes(args: list[str]) -> bool:
    """True when a `message` child accepts --yes (agent-spawning subcommands).

    remove/draft-reply own their flow (remove prompts only on TTY with
    --yes; draft-reply has no --yes flag), so only adhoc/validate/action
    run/flow run forward the web confirm.
    """
    return bool(args) and args[0] in ("adhoc", "validate", "action", "flow")

def _child_session_file(db, stem: str) -> tuple[str, str]:
    """File + row id for the CLI child's rescoped session (stem match).

    The review child rescopes the web-minted branch-less path under
    sessions/omp/<branch>/<action>/ and inserts its own row keyed by the
    same stem — the web row keeps the stale branch-less path. Match by
    filename stem so resume finds the real transcript.
    """
    if not stem:
        return "", ""
    try:
        from . import store_sqlite as _sq
        row = _sq.get_session(db, stem, include_deleted_worktrees=True)
        if row and _transcript_ready(str(row.get("file_path") or "")):
            return str(row.get("file_path")), str(row.get("id"))
    except Exception:
        pass
    return "", ""

def _newest_under(scope, created_ts: float) -> str:
    """Newest non-empty .jsonl directly under scope, newest-first ("" when none)."""
    from pathlib import Path as _Path
    best = ""
    best_mtime = -1.0
    try:
        entries = list(scope.iterdir())
    except OSError:
        return ""
    for p in entries:
        try:
            if not (p.is_file() and p.suffix == ".jsonl" and p.stat().st_size > 0):
                continue
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt < created_ts:
            continue
        if mt > best_mtime:
            best_mtime = mt
            best = str(p)
    return best

def _resolve_session_file(session_file: str, session_id: str = "") -> str:
    """Best-effort session file: DB row wins, else adopt the minted one.

    omp mints its own filename: the pinned --session-file path (web-minted
    id) is only the scope hint — after the user runs the printed command
    manually the real .jsonl lands nearby. Resolution order:
    1. DB row `file_path` when it is a ready transcript (covers the child
       CLI rescoping a branch-less pinned path under
       `sessions/omp/<branch>/<action>/` — the registry still holds the
       stale branch-less path);
    2. child session row matched by filename stem (the child inserts its own
       row under the same stem after rescope; the web row keeps the stale
       path) when it holds a ready transcript;
    3. pinned path itself when ready;
    4. newest non-empty .jsonl under the scope dir (pinned parent, falling
       back to the DB row parent), then one level of branch/action
       subdirectories (the child rescopes the path but the registry/web row
       still point at the branch-less parent — the minted transcript lands
       one level down), created after the session row — persisted on the
       session so future runs reuse the full path.
    Returns "" when nothing resumable is found.
    """
    from pathlib import Path as _Path
    if _transcript_ready(session_file):
        return session_file
    try:
        from . import store_sqlite as _sq
        from datetime import datetime
        db = _sq.db_path()
        db_file = ""
        created = ""
        if session_id and db.exists():
            try:
                row = _sq.get_session(db, session_id, include_deleted_worktrees=True)
                db_file = str((row or {}).get("file_path") or "")
                created = str((row or {}).get("created_at") or "")
            except Exception:
                db_file = ""
                created = ""
        if db_file and db_file != session_file and _transcript_ready(db_file):
            return db_file
        # Stem fallback: the CLI child inserted its own row under the same
        # stem after rescoping to the branch dir; point the web row at it.
        stem = _Path(session_file).stem if session_file else ""
        child_file, child_sid = _child_session_file(db, stem) if db.exists() else ("", "")
        if child_file:
            if session_id and db.exists():
                try:
                    _sq.set_session_file_path(db, session_id, child_file)
                except Exception:
                    pass
            return child_file
        scope = _Path(session_file).parent if session_file else None
        if scope is None or not scope.is_dir():
            scope = _Path(db_file).parent if db_file else None
        if scope is None or not scope.is_dir():
            return ""
        try:
            created_ts = datetime.fromisoformat(created).timestamp() if created else 0.0
        except Exception:
            created_ts = 0.0
        best = _newest_under(scope, created_ts)
        if not best:
            # Rescoped layout: the child moves the path from
            # sessions/omp/<action>/<stem>.jsonl to
            # sessions/omp/<branch-slug>/<action>/<stem>.jsonl — the branch
            # dir is a SIBLING of the pinned parent, not a child. Search the
            # grandparent tree for the same stem first, then any newest .jsonl.
            stem = _Path(session_file).stem if session_file else ""
            roots = [scope]
            try:
                if scope.parent != scope:
                    roots.append(scope.parent)
            except Exception:
                pass
            same: list[tuple[float, str]] = []
            any_: list[tuple[float, str]] = []
            for root in roots:
                try:
                    nested = [p for p in root.rglob("*.jsonl") if p.is_file()]
                except OSError:
                    continue
                for p in nested:
                    try:
                        if p.suffix != ".jsonl" or p.stat().st_size == 0:
                            continue
                        mt = p.stat().st_mtime
                    except OSError:
                        continue
                    if mt < created_ts:
                        continue
                    any_.append((mt, str(p)))
                    if stem and p.stem == stem:
                        same.append((mt, str(p)))
            pool = same or any_
            if pool:
                pool.sort()
                best = pool[-1][1]
        if best and session_id and db.exists():
            try:
                _sq.set_session_file_path(db, session_id, best)
            except Exception:
                pass
        return best
    except Exception:
        return ""

def _transcript_ready(session_file: str) -> bool:
    """True when the path is a non-empty transcript resumable via --resume.

    omp v18+ rejects --resume on missing AND empty files, so both count
    as missing here (callers 404 with missing_session; the user can still
    start fresh via --session-dir from the copy button).
    """
    if not session_file:
        return False
    try:
        p = Path(session_file)
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False

def _ref_key_for_worktree(worktree: str) -> str:
    """Link ref_key owning a worktree path ("" when unlinked)."""
    try:
        from . import store as _store
        for key, entry in _store.load_links().items():
            if isinstance(entry, dict) and entry.get("worktree") == worktree:
                return key
    except Exception:
        pass
    return ""


def _target_ref_key(run) -> str:
    """Link ref_key behind a live run's target ("" when not link-shaped)."""
    try:
        target = str(getattr(run, "target", "") or "")
        command = str(getattr(run, "command", "") or "")
        args = list(getattr(run, "args", []) or [])
        for prefix in ("start:", "review:", "sync:", "cleanup:", "open:"):
            if target.startswith(prefix):
                ref = target[len(prefix):]
                if ref and ref != "all":
                    return ref
        if command in ("start", "review"):
            ref = _first_positional(args, VAL_FLAGS[command])
            if ref:
                return ref
    except Exception:
        pass
    return ""

def _live_terminal_blocker(worktree: str, ref_key: str = "") -> dict | None:
    """Live harness record blocking a terminal spawn (None when free)."""
    from . import store as _store
    key = ref_key or _ref_key_for_worktree(worktree) or worktree
    rec = _store.active_harness(key)
    if rec is None and worktree:
        for v in _store.load_harnesses().values():
            if isinstance(v, dict) and v.get("worktree") == worktree:
                rec = v
                break
    return rec


def _conflict_msg(worktree: str, blocker: dict) -> str:
    return (f"worktree {worktree} already has a live harness "
            f"({blocker.get('harness')}, pid {blocker.get('pid')})"
            " — wait for it to finish or kill it")


def _claim_terminal_lock(worktree: str, ref_key: str = "",
                         pid: int | None = None) -> None:
    """Claim the terminal harness lock; 409 when a live harness holds it."""
    from . import store as _store
    key = ref_key or _ref_key_for_worktree(worktree) or worktree
    blocker = _store.record_harness_run(key, "omp", worktree,
                                        origin="terminal", pid=pid)
    if blocker is not None:
        raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)


def _open_terminal(worktree: str, session_file: str,
                   env_file: str | None = None) -> int | None:
    from . import webapp as _w
    return _w._open_terminal(worktree, session_file, env_file)


def _open_terminal_watch(worktree: str, session_file: str, sid: str,
                         scope_dir: str, before: str,
                         env_file: str | None = None,
                         harness_name: str = "omp") -> int | None:
    """Spawn the OS terminal on a bare session file via the watch shim.

    A bare terminal launch has no prompt/command for the harness: build the
    bare `<harness> --session-dir <scope>` shell command (mirrors
    `web_runs._resume_shell_command` fresh-file branch — omp mints the
    .jsonl on first write) and spawn the terminal on it through
    `cli_harness.spawn_in_terminal` with the `watch-and-exec` watcher
    encoded in the shell command, so the minted transcript lands on the
    session row seconds after start like headless/interactive launches.
    Best effort on the watch encoding — the spawn must succeed even when
    the shim argv cannot be built.
    """
    import shlex as _shlex
    from pathlib import Path as _Path
    from . import env as _env
    from .cli_harness import spawn_in_terminal as _spawn_term
    scope = scope_dir or (str(_Path(session_file).parent) if session_file else "")
    harness_cmd = f"{harness_name} --session-dir {_shlex.quote(scope)}" if scope else harness_name
    full_cmd = _env.wrap_command(f"cd {_shlex.quote(worktree)} && {harness_cmd}",
                                 _env.resolve_env_file(env_file))
    shim_argv = [sys.executable, "-m", "workagent", "watch-and-exec",
                 "--sid", str(sid), "--scope-dir", scope or worktree,
                 "--before", before or "", "--", "bash", "-lc", full_cmd]
    shim_cmd = " ".join(_shlex.quote(a) for a in shim_argv)
    try:
        return _spawn_term(shim_cmd, worktree)
    except Exception:
        return _spawn_term(full_cmd, worktree)

def _env_file_arg(args: list[str]) -> str:
    """Explicit --env-file passthrough from run args ("" when absent)."""
    for i, a in enumerate(args):
        if a == "--env-file" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--env-file="):
            return a.split("=", 1)[1]
    return ""


def _session_file_env(run) -> str | None:
    """Explicit --env-file from a live run's args (None when absent)."""
    try:
        explicit = _env_file_arg(list(getattr(run, "args", []) or []))
    except Exception:
        return None
    return explicit or None

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
        if _needs_confirm(body.command, body.args) and not body.confirm:
            raise ApiError(
                "confirm_required",
                f"{body.command} is destructive — pass confirm: true", 400)
        target = _target_for(body.command, body.args)
        run = registry.create(body.command, body.args, target)
        argv = _build_argv(body.command, body.args)
        tail: list[str] = []
        if body.confirm and (body.command in ("start", "review", "cleanup",
                                             "sync") or (body.command == "message"
                                                         and _message_needs_yes(body.args))):
            tail.append("--yes")
        if body.force and spec["force"]:
            tail.append("--force")
        if body.command in ("start", "review", "sync") \
                and "--dry-run" not in body.args \
                and "--all" not in body.args \
                and not _has_session_file(body.command, body.args):
            # Preview (no --launch) gets a path too: the CLI preview carries it as
            # --resume (creating nothing), so resume/copy buttons work once
            # the user runs the printed command manually. Fix runs default to
            # a fresh minted path (the child links back via
            # fixed_from_session_id); --use-review-session reuses the latest
            # finished review transcript instead (explicit --session-file
            # always wins in the CLI).
            from . import store as _store
            from . import store_sqlite as _sq
            from . import worktrees as _worktrees_mod
            session_file = ""
            if body.command == "review" and "--fix-comments" in body.args \
                    and "--use-review-session" in body.args:
                ref = _first_positional(body.args, VAL_FLAGS["review"])
                links = _store.load_links()
                resolved = _worktrees_mod.resolve_worktree(ref, links)
                if isinstance(resolved, list):
                    resolved = _worktrees_mod.pick_worktree(ref, resolved, links)
                if isinstance(resolved, str):
                    db = _sq.db_path()
                    latest = _sq.latest_review_session(db, resolved) if db.exists() else None
                    if latest and latest.get("file_path"):
                        session_file = str(latest["file_path"])
            if not session_file:
                session_file = _session_file_for(_sq.gen_session_id(), body.command, body.args)
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
        the run executed no harness session or the transcript is missing.
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
            session_file = _resolve_session_file(session_file, str(row.get("session_id") or ""))
            if not _transcript_ready(session_file):
                raise ApiError("missing_session",
                               f"session transcript missing: {session_file}", 404)
            worktree = _persisted_summary(row).get("worktree") or ""
            if not worktree:
                raise ApiError("no_worktree",
                               f"no worktree for run {run_id!r}", 404)
            ref = str(row.get("worktree_ref") or "")
            if (blocker := _live_terminal_blocker(worktree, ref)) is not None:
                raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)
            pid = _open_terminal(worktree, session_file,
                                 _env_file_arg(list(args)) or None)
            _claim_terminal_lock(worktree, ref, pid)
            return {"id": run_id, "session_file": session_file,
                    "worktree": worktree}
        session_file = run.session_file \
            or _session_file_arg(run.command, run.args)
        if not session_file:
            raise ApiError("no_session",
                           f"run {run_id} executed no harness session", 404)
        arg_file = _session_file_arg(run.command, list(run.args) if isinstance(run.args, list) else [])
        resolved = ""
        for cand in (session_file, arg_file or ""):
            if not cand:
                continue
            resolved = _resolve_session_file(cand, Path(cand).stem)
            if resolved:
                break
        session_file = resolved
        if not _transcript_ready(session_file):
            raise ApiError("missing_session",
                           f"session transcript missing: {session_file}", 404)
        worktree = run.worktree or _worktree_for_target(run.target)
        if not worktree and run.command in ("start", "review"):
            worktree = _worktree_for_target(f"{run.command}:{_first_positional(run.args, VAL_FLAGS[run.command])}")
        if not worktree:
            raise ApiError("no_worktree",
                           f"no worktree for target {run.target!r}", 404)
        ref = _target_ref_key(run)
        if (blocker := _live_terminal_blocker(worktree, ref)) is not None:
            raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)
        pid = _open_terminal(worktree, session_file,
                             _session_file_env(run))
        _claim_terminal_lock(worktree, ref, pid)
        return {"id": run.id, "session_file": session_file,
                "worktree": worktree}

    @app.post("/api/runs/{run_id}/start-terminal")
    async def start_terminal(run_id: str) -> dict:
        """Open the OS default terminal running this run's harness command.

        Same command as the Run page "Copy harness command" button (the last
        logged `harness command:` entry); the button's disabled state mirrors
        the 404 `no_harness_command` case. Non-destructive (same class as
        `open`/resume): no confirm needed.
        """
        from . import webapp as _w

        ref_key = ""
        worktree = ""
        try:
            live = registry.get(run_id)
            worktree = live.worktree or _worktree_for_target(live.target)
            if not worktree and live.command in ("start", "review"):
                worktree = _worktree_for_target(
                    f"{live.command}:{_first_positional(live.args, VAL_FLAGS[live.command])}")
        except ApiError:
            row = _persisted_row(run_id)
            if row is not None:
                ref_key = str(row.get("worktree_ref") or "")
                worktree = _persisted_summary(row).get("worktree") or ""
        def _lines() -> list[str]:
            try:
                run = registry.get(run_id)
            except ApiError:
                row = _persisted_row(run_id)
                if row is None:
                    raise
                return [ln["text"] for ln in _persisted_lines(row)]
            with run.lock:
                return [text for _, text in run.lines]

        cmd = _w._extract_harness_command(_lines())
        if not cmd:
            raise ApiError("no_harness_command",
                           f"run {run_id} logged no harness command", 404)
        if (blocker := _live_terminal_blocker(worktree, ref_key)) is not None:
            raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)
        pid = _w._open_terminal_command(cmd, worktree)
        _claim_terminal_lock(worktree, ref_key, pid)
        return {"id": run_id}

    @app.post("/api/terminal/stop")
    async def terminal_stop(body: dict) -> dict:
        """SIGTERM→SIGKILL the terminal-origin agent running for a worktree
        ref (the `--terminal` run mode's spawned process). Headless and
        preview runs are not stoppable here — 404. Read the worktree's
        live lock via Dashboard status (`harness` column = `terminal <pid>`)."""
        from . import store as _store
        ref = str((body or {}).get("worktree_ref", ""))
        if not ref:
            raise ApiError("bad_arg", "worktree_ref is required", 400)
        if not _store.stop_terminal_run(ref):
            raise ApiError("not_found", f"no live terminal agent for {ref}", 404)
        return {"worktree_ref": ref, "stopped": True}

    @app.post("/api/worktrees/{ref_key}/terminal")
    async def launch_worktree_terminal(ref_key: str, body: dict) -> dict:
        """Spawn a bare harness in the OS terminal for a worktree (no prompt).

        Body: {"mode": "harness" | "prodtest"}. Creates a session row with a
        pinned transcript path (session_type from mode), spawns the terminal
        on it, claims the terminal lock, and records a runs row — so the
        StatusTable overlay re-run path finds it like other harness modes.
        """
        from . import store as _store
        from . import store_sqlite as _sq
        from . import worktrees as _wts
        mode = str((body or {}).get("mode", "harness") or "harness")
        if mode not in ("harness", "prodtest"):
            raise ApiError("bad_arg", f"unknown terminal mode: {mode!r}", 400)
        links = _store.load_links()
        key = ref_key if ref_key in links else ""
        if not key:
            resolved = _wts.resolve_worktree(ref_key, links)
            if isinstance(resolved, list):
                raise ApiError("ambiguous", f"ambiguous worktree ref {ref_key!r}", 400)
            if isinstance(resolved, str):
                key = resolved
        if not key or key not in links or not isinstance(links[key], dict):
            raise ApiError("not_found", f"no linked worktree for {ref_key!r}", 404)
        entry = links[key] or {}
        worktree = str(entry.get("worktree") or "")
        if not worktree:
            raise ApiError("no_worktree", f"no worktree for {key!r}", 404)
        harness_name = str(_store.load_config().get("default_harness", "omp") or "omp")
        branch = str(entry.get("branch") or "")
        db = _sq.db_path()
        sid = _sq.gen_session_id()
        if mode == "prodtest":
            command = "prodtest run"
            stype: str | None = "prodtest_run"
            meta: dict = {}
        else:
            command = "harness"
            stype = None
            meta = {}
        try:
            session_path = str(_sq.session_file_path(sid, harness_name, branch=branch or None, session_type=stype))
        except Exception:
            session_path = ""
        if (blocker := _live_terminal_blocker(worktree, key)) is not None:
            raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)
        # Claim the slot BEFORE the spawn: two concurrent POSTs must not
        # both pass the blocker check above. Re-claimed with the real pid
        # after a successful spawn (record_harness_run overwrites own slot).
        _claim_terminal_lock(worktree, key, None)
        try:
            if db.exists() and key in _store.load_links(include_inactive=True, include_deleted=True):
                nsid = _sq.insert_session(db, worktree_ref=key, harness_name=harness_name, initiator_command=command, prompt="", file_path=session_path, session_id=sid, session_type=stype, metadata=meta)
                sid = nsid
        except Exception:
            pass
        scope_dir = str(Path(session_path).parent) if session_path else worktree
        try:
            before = _sq.newest_transcript(Path(scope_dir)) if scope_dir else None
        except Exception:
            before = None
        try:
            pid = _open_terminal_watch(worktree, session_path or sid, sid, scope_dir, str(before) if before else "", harness_name=harness_name)
        except Exception:
            from . import store as _store2
            try:
                _store2.clear_harness_run(key)
            except Exception:
                pass
            raise
        _claim_terminal_lock(worktree, key, pid)
        try:
            if db.exists() and key in _store.load_links(include_inactive=True, include_deleted=True):
                _sq.finish_session(db, sid, "preview")
                _sq.insert_run(db, sid, command, [key], 0, output=[f"terminal pid: {pid}"])
        except Exception:
            pass
        return {"id": sid, "session_file": session_path, "worktree": worktree, "mode": mode}


    @app.post("/api/sessions/{sid}/resume")
    async def resume_session(sid: str) -> dict:
        """Open the OS default terminal resumed on a persisted session."""
        from . import store_sqlite as _sq
        db = _sq.db_path()
        row = _sq.get_session(db, sid, include_deleted_worktrees=True) if db.exists() else None
        if row is None:
            raise ApiError("not_found", f"no session {sid}", 404)
        session_file = str(row.get("file_path") or "")
        session_file = _resolve_session_file(session_file, sid)
        if not _transcript_ready(session_file):
            raise ApiError("missing_session",
                           f"session transcript missing: {session_file}", 404)
        worktree = _worktree_for_session(row) or ""
        if not worktree:
            raise ApiError("no_worktree",
                           f"no worktree for session {sid}", 404)
        env = ""
        try:
            runs = row.get("runs") or []
            if isinstance(runs, list) and runs:
                env = _env_file_arg(list(runs[-1].get("args") or []))
        except Exception:
            env = ""
        ref = str(row.get("worktree_ref") or "")
        if (blocker := _live_terminal_blocker(worktree, ref)) is not None:
            raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)
        pid = _open_terminal(worktree, session_file, env or None)
        _claim_terminal_lock(worktree, ref, pid)
        return {"id": sid, "session_file": session_file,
                "worktree": worktree}

