"""runs API routes (split from webapp; mounted by create_app)."""
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
    _harness_arg,
    _persisted_lines,
    _persisted_row,
    _persisted_summary,
    _summary,
    _transcript_ready,
    _worktree_for_target,
)


def _message_needs_yes(args: list[str]) -> bool:
    """True when a `message` child accepts --yes (agent-spawning subcommands).

    remove prompts only on TTY with --yes, so only adhoc/validate/action
    run/flow run forward the web confirm.
    """
    return bool(args) and args[0] in ("adhoc", "validate", "action", "flow")

def _stored_transcript(file_path: str) -> str:
    """Stored transcript path → the readable file (new tree first, old as fallback).

    A restored backup, or a row the transcripts fold could not rewrite,
    still names the pre-rename ``sessions/`` tree while the file lives under
    ``runs/``. Readers resolve that mirror (and the reverse direction for a
    row naming a moved-away ``runs/`` path) before any readiness check, so
    resume works instead of 404ing on a directory that no longer exists.
    Empty stays empty; an unresolvable path is returned unchanged.
    """
    if not file_path:
        return ""
    try:
        from . import store_sqlite as _sq
        return str(_sq.resolve_transcript_path(file_path))
    except Exception:
        return file_path

def _child_session_file(db, stem: str) -> str:
    """Ready transcript file of the CLI child's rescoped row (stem match).

    The review child rescopes the web-minted branch-less path under
    runs/<harness>/<branch>/<action>/ and records its own row naming the
    same stem — the web row keeps the stale branch-less path. Match by
    filename stem so resume finds the real transcript ("" when none).
    """
    if not stem:
        return ""
    try:
        from . import store_sqlite as _sq
        row = _sq.run_by_file_stem(db, stem, include_deleted_worktrees=True)
        file_path = _stored_transcript(str((row or {}).get("file_path") or ""))
        if row and _transcript_ready(file_path):
            return file_path
    except Exception:
        pass
    return ""

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

def _resolve_session_file(session_file: str, run_id: int | str = "") -> str:
    """Best-effort transcript file: DB row wins, else adopt the minted one.

    omp mints its own filename: the pinned --session-file path (web-minted
    id) is only the scope hint — after the user runs the printed command
    manually the real .jsonl lands nearby. Stored paths are first resolved
    through the transcript roots (a legacy `sessions/` path maps to its
    `runs/` sibling — spec §4.1 readers try the new tree first), then the
    order is:
    1. the run row's `file_path` when it is a ready transcript (covers the
       child CLI rescoping a branch-less pinned path under
       `runs/omp/<branch>/<action>/` — the registry still holds the stale
       branch-less path);
    2. child row matched by filename stem (the child records its own row
       under the same stem after rescope; the web row keeps the stale path)
       when it holds a ready transcript;
    3. pinned path itself when ready;
    4. newest non-empty .jsonl under the scope dir (pinned parent, falling
       back to the row's parent), then one level of branch/action
       subdirectories (the child rescopes the path but the registry/web row
       still point at the branch-less parent — the minted transcript lands
       one level down), created after the run row — persisted on that row
       so future runs reuse the full path.
    Returns "" when nothing resumable is found.
    """
    from pathlib import Path as _Path
    session_file = _stored_transcript(session_file)
    if _transcript_ready(session_file):
        return session_file
    try:
        from . import store_sqlite as _sq
        from datetime import datetime
        db = _sq.db_path()
        row_id = 0
        try:
            row_id = int(run_id)
        except (TypeError, ValueError):
            row_id = 0
        db_file = ""
        created = ""
        hname = ""
        row = None
        if row_id and db.exists():
            try:
                row = _sq.get_run(db, row_id, include_deleted_worktrees=True)
                db_file = _stored_transcript(str((row or {}).get("file_path") or ""))
                created = str((row or {}).get("created_at") or "")
                hname = str((row or {}).get("harness_name") or "")
            except Exception:
                db_file = ""
                created = ""
                hname = ""
        if db_file and db_file != session_file and _transcript_ready(db_file):
            return db_file
        if not hname:
            try:
                from . import store as _store
                hname = _store.default_harness()
            except Exception:
                hname = ""
        if hname == "agy" and row:
            try:
                from . import agy_harness as _agy
                meta = row.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}
                cid = str(meta.get("conversation_id") or "")
                worktree = ""
                ref = str(row.get("worktree_ref") or "")
                if ref:
                    try:
                        from . import store as _store
                        links = _store.load_links()
                        if ref in links and isinstance(links[ref], dict):
                            worktree = str(links[ref].get("worktree") or "")
                    except Exception:
                        pass
                if not worktree:
                    args = row.get("args") if isinstance(row.get("args"), list) else []
                    cmd = str(row.get("command") or "")
                    if cmd in ("start", "review"):
                        worktree = _worktree_for_target(
                            f"{cmd}:{_first_positional(list(args), VAL_FLAGS.get(cmd, ()))})") or ""
                if worktree:
                    dest = session_file or db_file
                    if not dest:
                        dest = str(_sq.session_file_path(_sq.gen_session_id(), "agy", session_type=row.get("session_type")))
                    created_ts = 0.0
                    if created:
                        try:
                            created_ts = datetime.fromisoformat(created).timestamp()
                        except Exception:
                            created_ts = 0.0
                    adopted_cid, adopted_path = _agy.adopt_from_workspace(worktree, created_ts, dest)
                    if adopted_cid:
                        meta["conversation_id"] = adopted_cid
                        if adopted_path and _transcript_ready(adopted_path):
                            if row_id and db.exists():
                                _sq.set_run_file_path(db, row_id, adopted_path)
                                _sq.set_run_metadata(db, row_id, meta)
                            return adopted_path
                        else:
                            if row_id and db.exists():
                                _sq.set_run_metadata(db, row_id, meta)
            except Exception:
                pass
        # Stem fallback: the CLI child recorded its own row under the same
        # stem after rescoping to the branch dir; point the web row at it.
        stem = _Path(session_file).stem if session_file else ""
        child_file = _child_session_file(db, stem) if db.exists() else ""
        if child_file:
            if row_id and db.exists():
                try:
                    _sq.set_run_file_path(db, row_id, child_file)
                except Exception:
                    pass
            return child_file
        scope = _Path(session_file).parent if session_file else None
        if (scope is None or not scope.is_dir()) and db_file:
            scope = _Path(db_file).parent
        if scope is not None and not scope.is_dir():
            # Legacy tree: the payload directory moved with the transcripts —
            # search the mirrored one rather than giving up on a missing dir.
            mirrored = _stored_transcript(str(scope))
            scope = _Path(mirrored) if mirrored else scope
        if scope is None or not scope.is_dir():
            return ""
        try:
            created_ts = datetime.fromisoformat(created).timestamp() if created else 0.0
        except Exception:
            created_ts = 0.0
        best = _newest_under(scope, created_ts)
        if not best:
            # Rescoped layout: the child moves the path from
            # runs/omp/<action>/<stem>.jsonl to
            # runs/omp/<branch-slug>/<action>/<stem>.jsonl — the branch
            # dir is a SIBLING of the pinned parent, not a child. Search the
            # grandparent tree for the same stem first, then any newest .jsonl.
            stem = _Path(session_file).stem if session_file else ""
            roots = [scope]
            try:
                if scope.parent != scope:
                    roots.append(scope.parent)
            except Exception as e:
                import sys; print("AGY ADOPT EXC:", type(e), e, file=sys.stderr)
                import traceback; traceback.print_exc()
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
        if best and row_id and db.exists():
            try:
                _sq.set_run_file_path(db, row_id, best)
            except Exception as e:
                import sys; print("AGY ADOPT EXC:", type(e), e, file=sys.stderr)
                import traceback; traceback.print_exc()
        return best
    except Exception:
        return ""


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
                         pid: int | None = None, overwrite: bool = False,
                         harness_name: str = "") -> None:
    """Claim the terminal harness lock; 409 when a live harness holds it.

    *overwrite* replaces this key's own pre-claim with the spawned terminal
    pid (the spawner claims a slot before spawning, then records the real
    pid so `/api/terminal/stop` kills the terminal, not the server).
    """
    from . import store as _store
    key = ref_key or _ref_key_for_worktree(worktree) or worktree
    hname = harness_name or _store.default_harness()
    blocker = _store.record_harness_run(key, hname, worktree,
                                        origin="terminal", pid=pid,
                                        overwrite=overwrite)
    if blocker is not None:
        raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)


def _open_terminal(worktree: str, session_file: str,
                   env_file: str | None = None,
                   harness_name: str = "",
                   metadata: dict | None = None) -> int | None:
    from . import webapp as _w
    try:
        return _w._open_terminal(worktree, session_file, env_file,
                                 harness_name=harness_name, metadata=metadata)
    except TypeError:
        return _w._open_terminal(worktree, session_file, env_file)


def _open_terminal_watch(worktree: str, session_file: str, run_id: int | None,
                         scope_dir: str, before: str,
                         env_file: str | None = None,
                         harness_name: str = "") -> int | None:
    """Spawn the OS terminal on a bare session file via the watch shim.

    A bare terminal launch has no prompt/command for the harness: build the
    bare harness command through Harness.bare_terminal_argv and spawn the
    terminal on it through `cli_harness.spawn_in_terminal` with the
    `watch-and-exec` watcher encoded in the shell command.
    Without a row (no DB) the plain command is spawned: nothing to record.
    Best effort on the watch encoding — the spawn must succeed even when
    the shim argv cannot be built.
    """
    import shlex as _shlex
    from pathlib import Path as _Path
    from . import backend as _backend
    from . import env as _env
    from . import store as _store
    from .cli_harness import spawn_in_terminal as _spawn_term
    hname = harness_name or _store.default_harness()
    scope = scope_dir or (str(_Path(session_file).parent) if session_file else "")
    harness_argv = _backend.get_harness(hname).bare_terminal_argv(scope)
    harness_cmd = " ".join(_shlex.quote(a) for a in harness_argv)
    full_cmd = _env.wrap_command(f"cd {_shlex.quote(worktree)} && {harness_cmd}",
                                 _env.resolve_env_file(env_file))
    if not run_id:
        return _spawn_term(full_cmd, worktree)
    shim_argv = [sys.executable, "-m", "workagent", "watch-and-exec",
                 "--run-id", str(run_id),
                 "--harness", hname,
                 "--workdir", worktree,
                 "--session-file", session_file or "",
                 "--scope-dir", scope or worktree,
                 "--before", before or "",
                 "--", "bash", "-lc", full_cmd]
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
        # Confirmed web runs are non-interactive children: the CLI's TTY
        # confirms would exit 2 without --yes. release joins the list
        # because `release`/`release fix-default` hard-fail non-TTY
        # without --yes (issue #68); --dry-run previews need no confirm.
        # sync spells --yes also as -y/--force (same Typer flag), so any
        # of the three suppresses the append.
        _sync_yes = {"--yes", "-y", "--force"}
        _has_yes = ("--yes" in body.args or (body.command == "sync"
                                             and not _sync_yes.isdisjoint(body.args)))
        if body.confirm and "--dry-run" not in body.args and not _has_yes \
                and (body.command in ("start", "review", "cleanup", "sync",
                                      "release") or (body.command == "message"
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
            # fixed_from_run_id); --use-review-session reuses the latest
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
                    latest = _sq.latest_review_run(db, resolved) if db.exists() else None
                    if latest and latest.get("file_path"):
                        # Keep the ROW's path verbatim: a restored/legacy row
                        # may still say sessions/… while the transcript lives
                        # under runs/…. The child CLI adopts such a path
                        # itself (cli_review._adopt_fix_reuse re-resolves by
                        # stem), so pre-resolving here would make the
                        # precreated fix row shadow the review row and break
                        # the fixed_at stamp lookup.
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
        """Open the OS default terminal resumed on this run's transcript.

        Non-destructive (same class as `open`): no confirm needed. 404 when
        the run names no transcript or the transcript is missing. Persisted
        runs (db-<id>) resolve from the runs table since the registry is
        empty after a restart.
        """
        from . import store as _store
        try:
            run = registry.get(run_id)
        except ApiError:
            row = _persisted_row(run_id)
            if row is None:
                raise
            args = row.get("args") if isinstance(row.get("args"), list) else []
            session_file = str(row.get("file_path") or "") or _session_file_arg(
                str(row.get("command") or ""), list(args))
            session_file = _resolve_session_file(session_file, row.get("id") or "")
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
            hname = str(row.get("harness_name") or "") or _harness_arg(list(args)) or _store.default_harness()
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else None
            try:
                pid = _open_terminal(worktree, session_file,
                                     _env_file_arg(list(args)) or None,
                                     harness_name=hname, metadata=meta)
            except TypeError:
                pid = _open_terminal(worktree, session_file,
                                     _env_file_arg(list(args)) or None)
            _claim_terminal_lock(worktree, ref, pid, harness_name=hname)
            return {"id": run_id, "session_file": session_file,
                    "worktree": worktree}
        session_file = run.session_file \
            or _session_file_arg(run.command, run.args)
        if not session_file:
            raise ApiError("no_session",
                           f"run {run_id} executed no harness transcript", 404)
        arg_file = _session_file_arg(run.command, list(run.args) if isinstance(run.args, list) else [])
        resolved = ""
        for cand in (session_file, arg_file or ""):
            if not cand:
                continue
            resolved = _resolve_session_file(cand, run.db_run_id)
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
        hname = run.harness_name or _harness_arg(run.args) or _store.default_harness()
        try:
            pid = _open_terminal(worktree, session_file,
                                 _session_file_env(run),
                                 harness_name=hname)
        except TypeError:
            pid = _open_terminal(worktree, session_file,
                                 _session_file_env(run))
        _claim_terminal_lock(worktree, ref, pid, harness_name=hname)
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
        session_file = ""
        db_run_id = None
        hname = ""
        try:
            live = registry.get(run_id)
            worktree = live.worktree or _worktree_for_target(live.target)
            if not worktree and live.command in ("start", "review"):
                worktree = _worktree_for_target(
                    f"{live.command}:{_first_positional(live.args, VAL_FLAGS[live.command])}")
            ref_key = _target_ref_key(live)
            session_file = live.session_file or ""
            db_run_id = live.db_run_id
            hname = live.harness_name or _harness_arg(live.args)
        except ApiError:
            row = _persisted_row(run_id)
            if row is not None:
                ref_key = str(row.get("worktree_ref") or "")
                worktree = _persisted_summary(row).get("worktree") or ""
                session_file = str(row.get("file_path") or "")
                db_run_id = row.get("id")
                hname = str(row.get("harness_name") or "")
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
        from . import store as _store
        hname = hname or _store.default_harness()
        if db_run_id and worktree and session_file:
            import shlex as _shlex
            from pathlib import Path as _Path
            scope = str(_Path(session_file).parent) if session_file else worktree
            shim_argv = [sys.executable, "-m", "workagent", "watch-and-exec",
                         "--run-id", str(db_run_id),
                         "--harness", hname,
                         "--workdir", worktree,
                         "--session-file", session_file,
                         "--scope-dir", scope,
                         "--before", "",
                         "--", "bash", "-lc", cmd]
            cmd = " ".join(_shlex.quote(a) for a in shim_argv)
        pid = _w._open_terminal_command(cmd, worktree)
        _claim_terminal_lock(worktree, ref_key, pid, harness_name=hname)
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

        Body: {"mode": "harness" | "prodtest"}. Records ONE preview run row
        with the pinned transcript path (session_type from mode), spawns the
        terminal on it, and claims the terminal lock — so the StatusTable
        overlay re-run path finds it like other harness modes.
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
        raw_harness = (body or {}).get("harness")
        if raw_harness is not None and str(raw_harness):
            harness_name = str(raw_harness)
            try:
                from . import backend as _backend
                _backend.get_harness(harness_name)
            except Exception as exc:
                raise ApiError("bad_arg", str(exc), 400)
        else:
            harness_name = _store.default_harness()
        branch = str(entry.get("branch") or "")
        db = _sq.db_path()
        transcript_id = _sq.gen_session_id()
        if mode == "prodtest":
            command, stype = "prodtest run", "prodtest_run"
        else:
            command, stype = "harness", None
        try:
            session_path = str(_sq.session_file_path(transcript_id, harness_name,
                                                     branch=branch or None,
                                                     session_type=stype))
        except Exception:
            session_path = ""
        if (blocker := _live_terminal_blocker(worktree, key)) is not None:
            raise ApiError("conflict", _conflict_msg(worktree, blocker), 409)
        # Claim the slot BEFORE the spawn: two concurrent POSTs must not
        # both pass the blocker check above. Re-claimed with the real pid
        # after a successful spawn (record_harness_run overwrites own slot).
        _claim_terminal_lock(worktree, key, None, harness_name=harness_name)
        row_id: int | None = None
        try:
            if db.exists() and key in _store.load_links(include_inactive=True, include_deleted=True):
                row_id = _sq.insert_run(db, command, [key], 0,
                                        worktree_ref=key, harness_name=harness_name,
                                        initiator_command=command, prompt="",
                                        file_path=session_path, session_type=stype,
                                        metadata={"origin": "terminal-worktree"},
                                        state="preview")
        except Exception:
            row_id = None
        scope_dir = str(Path(session_path).parent) if session_path else worktree
        try:
            before = _sq.newest_transcript(Path(scope_dir)) if scope_dir else None
        except Exception:
            before = None
        try:
            pid = _open_terminal_watch(worktree, session_path, row_id, scope_dir,
                                       str(before) if before else "",
                                       harness_name=harness_name)
        except Exception as exc:
            from . import store as _store2
            try:
                _store2.clear_harness_run(key)
            except Exception:
                pass
            if row_id:
                # No terminal to spawn: the preview row must not survive as a
                # permanently running row pointing at a transcript that will
                # never be minted (frontend would offer Resume/Cancel on it).
                _sq.update_run(db, row_id, exit_code=1,
                               output=[f"terminal spawn failed: {exc}"])
                _sq.finish_run(db, row_id, "failed")
            raise
        _claim_terminal_lock(worktree, key, pid, overwrite=True, harness_name=harness_name)
        if row_id:
            _sq.update_run(db, row_id, output=[f"terminal pid: {pid}"])
        return {"id": row_id, "session_file": session_path, "worktree": worktree, "mode": mode}

