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
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, backend, store, worktrees

logger = logging.getLogger(__name__)



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
    _needs_confirm,
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
    _extract_harness_command,
    _open_terminal,
    _open_terminal_command,
    _resume_shell_command,
)

def run_startup_maintenance() -> dict:
    """Blocking maintenance previously done inline in run_server/startup.

    Keeps DB migrations/sync (init_db runs the schema synchronously; the
    release backfill stays on its background thread started by run_server),
    then runs the other slow non-essential jobs: soft-delete GC (git
    teardown), the orphan sweep (records whose worktree row is gone, plus
    their transcripts), and run-dir prune. Never raises;
    {"purged": [...], "orphans": {...}, "pruned": n}.
    """
    from .store_sqlite import db_path, init_db
    try:
        init_db(db_path())
    except Exception as e:
        print(f"warning: startup DB migration failed: {e}", file=sys.stderr)
    purged: list = []
    try:
        from .cli_cleanup import gc_soft_deleted
        purged = gc_soft_deleted()
        if purged:
            print(f"gc: purged {len(purged)} soft-deleted worktree(s) "
                  f"older than 7 days", file=sys.stderr)
    except Exception as e:
        print(f"warning: soft-delete GC failed: {e}", file=sys.stderr)
    orphans: dict = {}
    try:
        from .cli_cleanup import purge_orphans
        orphans = purge_orphans()
        if any(orphans.get(k) for k in
               ("runs", "flows", "messages", "parent_links")):
            print(f"gc: removed orphaned records "
                  f"(runs={orphans.get('runs', 0)} flows={orphans.get('flows', 0)}"
                  f" messages={orphans.get('messages', 0)}"
                  f" parents={orphans.get('parent_links', 0)}"
                  f" transcripts={len(orphans.get('transcripts') or [])})",
                  file=sys.stderr)
    except Exception as e:
        print(f"warning: orphan sweep failed: {e}", file=sys.stderr)
    try:
        from . import run_store as _rs
        pruned = _rs.prune_finished()
    except Exception:
        pruned = 0
    return {"purged": purged, "orphans": orphans, "pruned": pruned}

def _run_maintenance_logged() -> None:
    """run_startup_maintenance wrapper surfacing outcome on stderr."""
    try:
        out = run_startup_maintenance()
    except Exception as e:  # defensive: contract is never-raise
        print(f"warning: startup maintenance failed: {e}", file=sys.stderr)
        return
    print(f"startup maintenance done: purged {len(out['purged'])} "
          f"worktree(s), pruned {out['pruned']} run(s)", file=sys.stderr)

def start_background_maintenance() -> "threading.Thread":
    """Fire-and-forget run_startup_maintenance (daemon; serve never waits)."""
    t = threading.Thread(target=_run_maintenance_logged, name="workagent-startup",
                         daemon=True)
    t.start()
    return t


class WatcherOverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_fix_ci: bool | None = None
    auto_fix_reviews: bool | None = None


def _schedule_dispatcher(registry: Registry):  # type: ignore[valid-type]
    """Build a scheduler dispatcher that launches runs through the run registry.

    Unlike scheduler._default_dispatcher (detached Popen used by the CLI),
    serve-loop dispatches create a registry Run so they appear in /api/runs
    with logs and failure state. Returns run.db_run_id (None when the row
    insert was skipped).
    """
    def _dispatch(schedule: dict, argv: list[str]) -> int | None:
        from . import scheduler as _sched
        from .web_runs import _spawn as _spawn_run
        cmd, rest = argv[0], argv[1:]
        if cmd not in SPECS:
            return _sched._default_dispatcher(schedule, argv)
        _validate_args(cmd, rest)
        target = _target_for(cmd, rest)
        run = registry.create(cmd, rest, target)
        full_argv = _build_argv(cmd, rest)
        if "--dry-run" not in rest:
            _sync_yes = {"--yes", "-y", "--force"}
            _has_yes = ("--yes" in rest or (cmd == "sync"
                                            and not _sync_yes.isdisjoint(rest)))
            if not _has_yes and cmd in ("start", "review", "cleanup", "sync",
                                        "release"):
                full_argv = [*full_argv, "--yes"]
        run.argv = full_argv
        if cmd in ("start", "review", "sync"):
            run.worktree = _worktree_for_target(target) or ""
        _spawn_run(run, registry)
        return run.db_run_id or None
    return _dispatch


def create_app(static_dir: Path, host: str, port: int,
               allowed_hosts: list[str],
               enable_watcher: bool | None = None,
               watch_interval: float = 60.0) -> FastAPI:
    from . import swr as _swr
    _swr.set_background_enabled(True)
    from .cdc import CdcHub
    from . import watcher as _watcher
    from .scheduler import SchedulerLoop

    cdc_hub = CdcHub()
    if enable_watcher is None:
        watch_enabled = os.environ.get("WORKAGENT_WATCH", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
    else:
        watch_enabled = bool(enable_watcher)
    scheduler_loop = SchedulerLoop()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        cdc_hub.start()
        scheduler_loop.start()
        watcher_stop: asyncio.Event | None = None
        watcher_task: asyncio.Task | None = None
        if watch_enabled:
            watcher_stop = asyncio.Event()
            watcher_task = asyncio.create_task(
                _watcher.watch_loop_async(watcher_stop, interval=watch_interval)
            )
            _app.state.watcher_task = watcher_task
        try:
            yield
        finally:
            scheduler_loop.stop()
            if watcher_stop is not None:
                watcher_stop.set()
            if watcher_task is not None:
                watcher_task.cancel()
                try:
                    await watcher_task
                except (asyncio.CancelledError, Exception):
                    pass
            cdc_hub.stop()

    app = FastAPI(title="workagent", docs_url=None, redoc_url=None, lifespan=_lifespan)
    registry = Registry()
    scheduler_loop._dispatcher = _schedule_dispatcher(registry)
    try:
        from .web_runs import rehydrate as _rehydrate
        _rehydrate(registry)
    except Exception as e:  # never fail startup: log and continue empty
        print(f"[web] run rehydrate failed: {e}", file=sys.stderr, flush=True)
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

    def _runs_and_flows_by_worktree() -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        runs_by_key: dict[str, list[dict]] = {}
        seen_latest: set[str] = set()
        try:
            from datetime import datetime
            from . import store_sqlite as _sq
            from .web_runs import _finish_state, _pending_question_from_texts

            with registry.lock:
                live_runs = list(registry.runs.values())
            items: list[dict] = []
            seen_db_ids: set[int] = set()
            for r in live_runs:
                if r.db_run_id:
                    seen_db_ids.add(r.db_run_id)
                wref = str(r.db_worktree_ref or "").strip()
                if not wref:
                    continue
                pending = None
                if r.state == "running":
                    with r.lock:
                        src = [txt for _, txt in r.lines]
                    pending = _pending_question_from_texts(src)
                items.append({
                    "worktree_ref": wref,
                    "state": r.state,
                    "command": r.command,
                    "pending_question": pending,
                    "_sort_ts": float(r.created or 0.0),
                    "_sort_id": int(r.db_run_id or 0),
                })
            if store._sqlite_path() is not None:
                for row in _sq.list_runs(_sq.db_path(), include_output=False):
                    rid = int(row.get("id") or 0)
                    if rid in seen_db_ids:
                        continue
                    wref = str(row.get("worktree_ref") or "").strip()
                    if not wref:
                        continue
                    exit_code = row.get("exit_code")
                    stored = str(row.get("state") or "")
                    if exit_code is None:
                        state = "failed" if stored == "failed" else (
                            "succeeded" if stored in ("finished", "preview") else "running"
                        )
                    else:
                        state = _finish_state(exit_code)
                    try:
                        ts = datetime.fromisoformat(str(row.get("created_at") or "")).timestamp()
                    except Exception:
                        ts = 0.0
                    items.append({
                        "worktree_ref": wref,
                        "state": state,
                        "command": str(row.get("command") or ""),
                        "pending_question": None,
                        "_sort_ts": ts,
                        "_sort_id": rid,
                    })
            items.sort(key=lambda r: (r["_sort_ts"], r["_sort_id"]), reverse=True)
            for r in items:
                wref = str(r.get("worktree_ref") or "").strip()
                if not wref:
                    continue
                is_latest = wref not in seen_latest
                seen_latest.add(wref)
                state = str(r.get("state") or "").strip().lower()
                if state in ("running", "needs_input") or r.get("pending_question"):
                    runs_by_key.setdefault(wref, []).append(r)
                elif is_latest and state == "failed":
                    runs_by_key.setdefault(wref, []).append(r)
        except Exception as exc:
            logger.warning("Failed to list runs for status enrichment: %s", exc)
        flows_by_key: dict[str, list[dict]] = {}
        try:
            if store._sqlite_path() is not None:
                for fl in store.list_flows():
                    wref = str(fl.get("worktree_ref") or fl.get("worktree") or "").strip()
                    if not wref:
                        continue
                    flows_by_key.setdefault(wref, []).append(fl)
        except Exception as exc:
            logger.warning("Failed to list flows for status enrichment: %s", exc)
        return runs_by_key, flows_by_key

    @app.get("/api/status")
    def status(ref: str | None = None, refresh: bool = False, include_inactive: bool = False,
               include_deleted: bool = False):
        links = store.load_links(include_inactive=include_inactive, include_deleted=include_deleted)
        runs_by_key, flows_by_key = _runs_and_flows_by_worktree()
        if not ref:
            _fetch_origins(links, refresh)
            return {
                k: _enrich_entry(
                    k,
                    v,
                    refresh,
                    active_runs=runs_by_key.get(k, []),
                    active_flows=flows_by_key.get(k, []),
                )
                for k, v in links.items()
            }
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            raise HTTPException(404, f"no linked state for {ref}")
        key = worktrees.pick_worktree(ref, resolved, links)
        _fetch_origins({key: links[key]}, refresh)
        return _session_detail(
            key,
            links[key],
            refresh=refresh,
            active_runs=runs_by_key.get(key, []),
            active_flows=flows_by_key.get(key, []),
        )

    @app.get("/api/worktrees/{ref:path}/timeline")
    def worktree_timeline(ref: str, limit: int = 100, fetch_logs: bool = False):
        links = store.load_links(include_inactive=True, include_deleted=True)
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            raise HTTPException(404, f"no linked state for {ref}")
        key = worktrees.pick_worktree(ref, resolved, links)
        checks = store.list_pr_checks(key)
        if fetch_logs:
            from . import refs as _refs, refs_ci as _refs_ci, store_timeline as _tl
            entry = links[key]
            branch = str(entry.get("branch") or "")
            repo = str(entry.get("repo") or "")
            cwd = repo or str(entry.get("worktree") or "") or None
            cached = store.lookup_pr_cache(branch, repo) if branch else None
            pr_url = str(entry.get("pr_url") or "").strip()
            if not pr_url.startswith("http"):
                pr_url = str((cached or {}).get("url") or (cached or {}).get("pr_url") or "").strip()
            tool = _refs_ci.infer_pr_tool(pr_url, cached)
            changed = False
            failing_now = [c for c in checks if _tl.is_failing_check(c)]
            ci_url = str((cached or {}).get("ci_url") or "")
            if not failing_now and pr_url and tool:
                ci_state, fetched_ci_url, fresh_checks = _refs.fetch_ci_details(tool, pr_url, cwd)
                if ci_state is not None:
                    tip = str((cached or {}).get("branch_tip") or "")
                    for c in fresh_checks:
                        if not c.get("commit_sha") and tip:
                            c["commit_sha"] = tip
                    checks = fresh_checks
                    ci_url = fetched_ci_url or ci_url
                    changed = True
                    if branch:
                        store.cache_ci_status(
                            branch,
                            ci_state,
                            sha=tip,
                            ci_url=ci_url,
                            repo=repo,
                        )
            if checks:
                before = [
                    (str(c.get("url") or ""), str(c.get("log_tail") or ""))
                    for c in checks
                ]
                _refs_ci.populate_failing_check_logs(
                    tool, checks, cwd=cwd, fallback_url=ci_url
                )
                after = [
                    (str(c.get("url") or ""), str(c.get("log_tail") or ""))
                    for c in checks
                ]
                if after != before:
                    changed = True
            if changed:
                def _normalize_fallback_checks(raw_checks: list[dict]) -> list[dict]:
                    now_iso = _tl._now_iso()
                    return [
                        {
                            "id": int(c.get("id") or (idx + 1)),
                            "worktree_ref": str(c.get("worktree_ref") or key),
                            "name": str(c.get("name") or ""),
                            "commit_sha": str(c.get("commit_sha") or ""),
                            "status": str(c.get("status") or ""),
                            "conclusion": str(c.get("conclusion") or ""),
                            "url": str(c.get("url") or ""),
                            "log_tail": str(c.get("log_tail") or ""),
                            "updated_at": str(c.get("updated_at") or now_iso),
                        }
                        for idx, c in enumerate(raw_checks)
                        if isinstance(c, dict)
                    ]

                has_named_checks = any(
                    isinstance(c, dict) and str(c.get("name") or "").strip()
                    for c in checks
                )
                try:
                    written = store.replace_pr_checks(key, checks)
                    if written or not has_named_checks:
                        checks = store.list_pr_checks(key)
                    else:
                        logger.warning(
                            "Failed to persist pr_checks for %s (replace_pr_checks returned 0)",
                            key,
                        )
                        checks = _normalize_fallback_checks(checks)
                except Exception as exc:
                    logger.warning("Failed to persist pr_checks for %s: %s", key, exc)
                    checks = _normalize_fallback_checks(checks)
        threads = store.list_pr_review_threads(key)
        events = store.list_worktree_events(key, limit=limit)
        from . import store_timeline
        failing_checks = store_timeline.failing_check_names(checks)
        unresolved_threads = sum(1 for t in threads if not t.get("resolved"))
        return {
            "ref": key,
            "checks": checks,
            "threads": threads,
            "events": events,
            "summary": {
                "failing_checks": failing_checks,
                "unresolved_threads": unresolved_threads,
            },
        }

    @app.get("/api/info")
    def info() -> dict:
        return {"version": __version__, "host": host, "port": port,
                "network_exposed": network_exposed}

    @app.get("/api/config")
    def get_config() -> dict:
        backend._load_harnesses()
        harnesses_list = [
            {
                "name": name,
                "label": h.label or name,
                "binary": h.binary,
                "available": shutil.which(h.binary) is not None,
            }
            for name, h in sorted(backend.HARNESSES.items())
        ]
        return {
            "default_harness": store.default_harness(),
            "default_model": store.default_model(),
            "default_effort": store.default_effort(),
            "supported_harnesses": sorted(backend.HARNESSES),
            "harnesses": harnesses_list,
        }

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

    @app.get("/api/worktrees/{ref:path}/watcher")
    def worktree_watcher_get(ref: str) -> dict:
        """Read per-worktree watcher overrides and effective values."""
        links = store.load_links(include_inactive=True)
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            raise HTTPException(404, f"no linked state for {ref}")
        key = worktrees.pick_worktree(ref, resolved, links)
        entry = links[key]
        _, repo_entry = _watcher._repo_for_entry(entry, store.load_repos())
        eff_ci, eff_rev = _watcher.effective_auto_fix(entry, repo_entry)
        return {
            "key": key,
            "auto_fix_ci": entry.get("auto_fix_ci") if isinstance(entry.get("auto_fix_ci"), bool) else None,
            "auto_fix_reviews": entry.get("auto_fix_reviews") if isinstance(entry.get("auto_fix_reviews"), bool) else None,
            "effective_auto_fix_ci": eff_ci,
            "effective_auto_fix_reviews": eff_rev,
            "last_nudge_signature": str(entry.get("last_nudge_signature") or ""),
        }

    @app.post("/api/worktrees/{ref:path}/watcher")
    def worktree_watcher_set(ref: str, body: WatcherOverrideIn) -> dict:
        """Set or clear per-worktree watcher overrides (null = inherit from repo)."""
        links = store.load_links(include_inactive=True)
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            raise HTTPException(404, f"no linked state for {ref}")
        key = worktrees.pick_worktree(ref, resolved, links)
        fields_set = body.model_fields_set
        kwargs: dict[str, Any] = {}
        if "auto_fix_ci" in fields_set:
            kwargs["auto_fix_ci"] = body.auto_fix_ci
        if "auto_fix_reviews" in fields_set:
            kwargs["auto_fix_reviews"] = body.auto_fix_reviews
        entry = _watcher.update_worktree_watcher_fields(key, **kwargs)
        _, repo_entry = _watcher._repo_for_entry(entry, store.load_repos())
        eff_ci, eff_rev = _watcher.effective_auto_fix(entry, repo_entry)
        return {
            "key": key,
            "auto_fix_ci": entry.get("auto_fix_ci") if isinstance(entry.get("auto_fix_ci"), bool) else None,
            "auto_fix_reviews": entry.get("auto_fix_reviews") if isinstance(entry.get("auto_fix_reviews"), bool) else None,
            "effective_auto_fix_ci": eff_ci,
            "effective_auto_fix_reviews": eff_rev,
            "last_nudge_signature": str(entry.get("last_nudge_signature") or ""),
        }

    @app.get("/api/repos")
    def repos_list() -> list[dict]:
        return [{"name": n, "path": v.get("path", ""),
                 "tracker": v.get("tracker", ""),
                 "trackers": v.get("trackers", [v.get("tracker", "")] if v.get("tracker") else []),
                 **{k: val for k, val in v.items() if k not in ("path", "tracker", "trackers")}}
                for n, v in store.load_repos().items()]

    @app.get("/api/repos/detect")
    def repos_detect(path: str = "") -> dict:
        """Auto-detect the pre-release gate for a local path (read-only)."""
        from . import repos as _repos
        try:
            cmd = _repos.detect_prerelease_command(path)
        except Exception:
            cmd = None
        return {"pre-release": cmd}


    @app.get("/api/trackers")
    def trackers_list() -> dict:
        """Issue trackers with linked-repo counts (CRUD page source)."""
        from . import store_sqlite as _sq
        db = _sq.db_path()
        if db.exists():
            return {"trackers": _sq.load_tracker_rows(db)}
        return {"trackers": [
            {"key": t, "vendor": "", "remote_url": "",
             "repos": len((v or {}).get("repos", [])),
             "is_enabled": bool((v or {}).get("is_enabled", True))}
            for t, v in store.load_trackers().items()]}

    @app.get("/api/actions")
    def actions_list() -> dict:
        """Custom actions (Actions page source). Always 200, [] when none."""
        return {"actions": store.list_actions()}

    @app.get("/api/actions/{label}")
    def action_detail(label: str) -> dict:
        """One custom action by label; 404 when unknown."""
        row = store.get_action(label)
        if row is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404,
                                detail=f"unknown action label: {label!r}")
        return row

    @app.get("/api/messages")
    def messages_list(thread: str = "") -> dict:
        """Registered chat messages (Messages page source). Always 200."""
        return {"messages": store.list_messages(thread)}


    @app.get("/api/prodtests")
    def prodtests_list() -> dict:
        """All prodtest items, newest first (Prodtests page source). Always 200."""
        return {"prodtests": store.list_prodtests()}


    @app.get("/api/prodtests/{issue_key}/plan")
    def prodtest_plan_view(issue_key: str) -> dict:
        """Read-only prodtest YAML plan content + parsed cases/metadata."""
        from . import prodtest_plan
        try:
            return prodtest_plan.read_prodtest_plan(issue_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except prodtest_plan.PlanPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/prodtests/{ref}")
    def prodtest_detail(ref: str) -> dict:
        """One prodtest item by issue key; 404 when unknown."""
        from .cli_prodtest import _prodtest_key_for_status
        try:
            key = _prodtest_key_for_status(ref)
        except Exception:
            key = ref
        item = store.get_prodtest(key)
        if item is None:
            raise HTTPException(status_code=404,
                                detail=f"unknown prodtest item: {ref!r}")
        return item


    @app.get("/api/messages/{ref}")
    def message_detail(ref: str) -> dict:
        """One registered message by id/message_id/thread; 404 when unknown."""
        from .errors import HarnessError
        try:
            return store.resolve_message(ref)
        except HarnessError:
            from fastapi import HTTPException
            raise HTTPException(status_code=404,
                                detail=f"unknown message: {ref!r}")

    @app.get("/api/flow-templates")
    def flow_templates_list() -> dict:
        """Flow templates (Flow page source). Always 200, [] when none."""
        return {"templates": store.list_templates()}

    @app.get("/api/flow-templates/{label}")
    def flow_template_detail(label: str) -> dict:
        """One flow template by label; 404 when unknown."""
        row = store.get_template(label)
        if row is None:
            raise HTTPException(status_code=404,
                                detail=f"unknown flow template: {label!r}")
        return row

    @app.get("/api/flows")
    def flows_list(worktree: str = "") -> dict:
        """Flow runs, optionally scoped to one worktree. Always 200."""
        return {"flows": store.list_flows(worktree=worktree)}

    @app.get("/api/schedules")
    def schedules_list() -> dict:
        """All recurring schedules plus built-in starter templates. Always 200."""
        from . import scheduler as _sched
        from . import store_sqlite as _sq
        return {
            "schedules": _sq.list_schedules(_sq.db_path()),
            "starters": _sched.STARTER_TEMPLATES,
        }

    @app.post("/api/schedules/preview")
    def schedules_preview(payload: dict) -> dict:
        """Preview the next N run timestamps for a cron/RRULE expression."""
        from . import scheduler as _sched
        expr = str(payload.get("cron_expr") or "").strip() if isinstance(payload, dict) else ""
        tz_name = str(payload.get("timezone") or "UTC").strip() if isinstance(payload, dict) else "UTC"
        count = int(payload.get("count") or 5) if isinstance(payload, dict) else 5
        try:
            runs = _sched.preview_next_runs(expr, tz_name, count=count)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"cron_expr": expr, "timezone": tz_name, "next_runs": runs}

    @app.get("/api/schedules/{schedule_id}")
    def schedule_detail(schedule_id: str) -> dict:
        """One schedule by id or name; 404 when unknown."""
        from . import store_sqlite as _sq
        row = _sq.get_schedule(_sq.db_path(), schedule_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown schedule: {schedule_id!r}")
        return row

    @app.post("/api/schedules")
    def schedule_create(payload: dict) -> dict:
        """Create a new recurring schedule."""
        from . import scheduler as _sched
        from . import store_sqlite as _sq
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be an object")
        name = str(payload.get("name") or "").strip()
        cron_expr = str(payload.get("cron_expr") or "").strip()
        tz_name = str(payload.get("timezone") or "UTC").strip() or "UTC"
        target_kind = str(payload.get("target_kind") or "").strip()
        target_label = str(payload.get("target_label") or "").strip()
        args_json = payload.get("args_json", "{}")
        enabled = bool(payload.get("enabled", True))
        try:
            nxt = _sched.next_run_after(cron_expr, tz_name).isoformat()
            row = _sq.add_schedule(
                _sq.db_path(),
                name=name,
                cron_expr=cron_expr,
                timezone_name=tz_name,
                target_kind=target_kind,
                target_label=target_label,
                args_json=args_json,
                enabled=enabled,
                next_run_at=nxt,
            )
        except (ValueError, json.JSONDecodeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return row

    @app.patch("/api/schedules/{schedule_id}")
    def schedule_patch(schedule_id: str, payload: dict) -> dict:
        """Update an existing schedule."""
        from . import scheduler as _sched
        from . import store_sqlite as _sq
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be an object")
        db = _sq.db_path()
        existing = _sq.get_schedule(db, schedule_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"unknown schedule: {schedule_id!r}")
        eff_cron = str(payload["cron_expr"]).strip() if "cron_expr" in payload and payload["cron_expr"] is not None else existing["cron_expr"]
        eff_tz = str(payload["timezone"]).strip() if "timezone" in payload and payload["timezone"] is not None else existing["timezone"]
        next_run_at: str | None = None
        if "cron_expr" in payload or "timezone" in payload or (payload.get("enabled") is True and not existing.get("next_run_at")):
            try:
                next_run_at = _sched.next_run_after(eff_cron, eff_tz).isoformat()
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        try:
            row = _sq.update_schedule(
                db,
                existing["id"],
                name=payload.get("name"),
                cron_expr=payload.get("cron_expr"),
                timezone=payload.get("timezone"),
                target_kind=payload.get("target_kind"),
                target_label=payload.get("target_label"),
                args_json=payload.get("args_json"),
                enabled=payload.get("enabled"),
                next_run_at=next_run_at,
            )
        except (ValueError, json.JSONDecodeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return row

    @app.delete("/api/schedules/{schedule_id}")
    def schedule_delete(schedule_id: str) -> dict:
        """Delete a schedule by id or name; 404 when unknown."""
        from . import store_sqlite as _sq
        if not _sq.remove_schedule(_sq.db_path(), schedule_id):
            raise HTTPException(status_code=404, detail=f"unknown schedule: {schedule_id!r}")
        return {"removed": True, "id": schedule_id}

    @app.post("/api/schedules/{schedule_id}/run")
    def schedule_run_now(schedule_id: str, payload: dict | None = None) -> dict:
        """Trigger a schedule immediately via the run registry.

        Destructive targets (cleanup --merged, review --all --fix, sync,
        start, release, ...) require {"confirm": true} — the same gate as
        POST /api/runs — so a stored schedule cannot be fired by a bodyless
        CSRF-able POST.
        """
        from datetime import datetime, timezone
        from . import scheduler as _sched
        from . import store_sqlite as _sq
        from .web_runs import _spawn as _spawn_run
        db = _sq.db_path()
        row = _sq.get_schedule(db, schedule_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown schedule: {schedule_id!r}")
        argv = _sched.build_schedule_argv(row)
        if not argv:
            raise HTTPException(status_code=400, detail="schedule produced empty command")
        cmd, rest = argv[0], argv[1:]
        confirmed = bool(isinstance(payload, dict) and payload.get("confirm"))
        if cmd in SPECS and _needs_confirm(cmd, rest) and not confirmed:
            raise HTTPException(
                status_code=400,
                detail=f"{cmd} is destructive \u2014 pass confirm: true")
        now_dt = datetime.now(timezone.utc)
        run_id: int | None = None
        if cmd in SPECS:
            _validate_args(cmd, rest)
            target = _target_for(cmd, rest)
            run = registry.create(cmd, rest, target)
            full_argv = _build_argv(cmd, rest)
            _sync_yes = {"--yes", "-y", "--force"}
            _has_yes = ("--yes" in rest or (cmd == "sync"
                                            and not _sync_yes.isdisjoint(rest)))
            if confirmed and "--dry-run" not in rest and not _has_yes \
                    and cmd in ("start", "review", "cleanup", "sync", "release"):
                full_argv = [*full_argv, "--yes"]
            run.argv = full_argv
            if cmd in ("start", "review", "sync"):
                run.worktree = _worktree_for_target(target) or ""
            _spawn_run(run, registry)
            run_id = run.db_run_id or None
        else:
            _sched._default_dispatcher(row, argv)
        nxt = _sched.next_run_after(row["cron_expr"], row["timezone"], after=now_dt).isoformat()
        updated = _sq.record_schedule_run(
            db,
            row["id"],
            last_run_at=now_dt.isoformat(),
            next_run_at=nxt,
            last_run_id=run_id,
        )
        return {"schedule": updated, "argv": argv, "run_id": run_id}

    @app.get("/api/tracker-domains")
    def tracker_domains() -> dict:
        """Disabled tracker domains (domain-level disable list)."""
        return {"disabled_domains": store.load_disabled_tracker_domains()}

    @app.post("/api/tracker-domains")
    def tracker_domains_set(payload: dict) -> dict:
        """Replace the disabled-tracker domain list (normalized, sorted)."""
        domains = payload.get("domains", []) if isinstance(payload, dict) else []
        if not isinstance(domains, list) or any(not isinstance(d, str) for d in domains):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="domains must be a list of strings")
        return {"disabled_domains": store.save_disabled_tracker_domains(domains)}

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

    @app.get("/api/review-session")
    def review_session(ref: str) -> dict:
        """Latest non-running review run for *ref* (fix-continue target).

        Read-only, always 200: resolves the ref to its worktree key with the
        same fuzzy resolver as cleanup/open, then returns the newest
        finished/failed review run's row id + transcript path — nulls when
        none exists."""
        from . import store_sqlite as _sq
        links = store.load_links()
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            return {"ref": ref, "run_id": None, "file_path": None}
        key = worktrees.pick_worktree(ref, resolved, links)
        db = _sq.db_path()
        latest = _sq.latest_review_run(db, key) if db.exists() else None
        if latest is None:
            return {"ref": ref, "run_id": None, "file_path": None}
        file_path = str(latest.get("file_path") or "")
        resolved_path = _sq.resolve_transcript_path(file_path) if file_path else None
        return {"ref": ref, "run_id": latest["id"],
                "file_path": str(resolved_path) if resolved_path else None}

    @app.get("/api/doctor")
    def doctor() -> dict:
        from . import doctor as doctor_mod
        return doctor_mod.check(json_output=True)

    @app.get("/api/service")
    def service_status() -> dict:
        """User systemd unit state for `serve` (read-only, always 200).

        `active`: is-active exit 0. `installed`: unit file present.
        Never raises — unknown state surfaces as strings, not errors."""
        from . import service as service_mod
        installed = service_mod.unit_path().exists()
        try:
            cp = service_mod._run_systemctl("is-active", "workagent")
            state = (cp.stdout or "").strip() or "unknown"
            return {"installed": installed, "active": cp.returncode == 0,
                    "state": state}
        except Exception as e:  # systemctl missing/bus down
            return {"installed": installed, "active": False,
                    "state": f"unknown: {e}"}

    @app.get("/api/issues")
    def my_issues(force: bool = False, days: int = 90, mine: bool = True) -> dict:
        """Issues for the user's (window, scope) selection (mine =
        assigned/created by me; 200 + warning even when the tracker CLIs
        are missing — read-only, never raises)."""
        days = max(1, min(365, int(days or 90)))
        warnings: list[str] = []
        return {"issues": trackers_mod.list_my_issues(warnings, force=force,
                                                      days=days, mine=mine),
                "warning": "; ".join(warnings) or None}

    @app.get("/api/candidates")
    def candidates(force: bool = False, days: int = 90, mine: bool = True) -> dict:
        """Unlinked open PR/MRs + issues for the user's (window, scope)
        selection + unregistered on-disk worktrees; read-only."""
        days = max(1, min(365, int(days or 90)))
        out = _candidates(force=force, days=days, mine=mine)
        return {"prs": out["prs"], "issues": out["issues"],
                "worktrees": out["worktrees"], "warnings": out["warnings"]}

    @app.get("/api/candidates/prs")
    def candidates_prs(force: bool = False) -> dict:
        """Unlinked open PR/MRs: fresh cache serves instantly; expired cache
        serves stale rows (`stale: True`) while one background thread
        revalidates; empty cache or `force=true` fetches live. Read-only."""
        from . import cli_candidates as _cand
        prs, warnings, stale = _cand._candidate_prs_cached(force=force)
        return {"prs": prs, "warnings": warnings, "stale": stale}

    @app.get("/api/candidates/issues")
    def candidates_issues(force: bool = False, days: int = 90, mine: bool = True) -> dict:
        """Issues for the user's (window, scope) selection (served by
        variant cache key; out-of-superset selections requery). Read-only."""
        days = max(1, min(365, int(days or 90)))
        from . import cli_candidates as _cand
        issues, warnings = _cand._candidate_issues(force=force, days=days, mine=mine)
        return {"issues": issues, "warnings": warnings}

    @app.get("/api/candidates/worktrees")
    def candidates_worktrees() -> dict:
        """Unregistered on-disk worktrees (fs scan). Read-only."""
        from . import cli_candidates as _cand
        return {"worktrees": _cand._scan_worktrees(), "warnings": []}

    @app.get("/api/release/status")
    def release_status(name: str = "") -> dict:
        """Releasability for one repo; always serves the cached row instantly
        and revalidates the diff in the background (never blocks)."""
        from . import repos as _repos
        return _repos.release_status(name, background=True)

    @app.get("/api/events")
    async def events_stream(request: Request, last_event_id: int | None = None):
        """Global SSE invalidation stream backed by SQLite change_log (CDC)."""
        header_id = request.headers.get("last-event-id")
        replay_after: int | None = last_event_id
        if replay_after is None and header_id:
            try:
                replay_after = int(header_id.strip())
            except ValueError:
                replay_after = None

        loop = asyncio.get_running_loop()
        q = cdc_hub.subscribe(loop)

        async def _gen():
            last_sent = (
                replay_after
                if replay_after is not None
                else await asyncio.to_thread(cdc_hub.current_seq)
            )
            try:
                if replay_after is not None:
                    for ev in await asyncio.to_thread(cdc_hub.replay, replay_after):
                        seq = int(ev["seq"])
                        if seq <= last_sent:
                            continue
                        last_sent = seq
                        yield f"id: {seq}\nevent: change\ndata: {json.dumps(ev)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=cdc_hub.heartbeat_interval)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if ev is None:
                        break
                    seq = int(ev["seq"])
                    if seq <= last_sent:
                        continue
                    last_sent = seq
                    yield f"id: {seq}\nevent: change\ndata: {json.dumps(ev)}\n\n"
            finally:
                cdc_hub.unsubscribe(loop, q)

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
    app.state.cdc_hub = cdc_hub
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


def _shutdown_leave_detached_running(server: Any) -> None:
    # Detached runs survive restarts by design (run_supervised detaches
    # into their own sessions; rehydrate() re-adopts them on startup).
    # Killing them here defeated that: any `service restart`/reinstall
    # SIGTERM'd live flows mid-step (flow #47: exit -15 in fix_comments).
    # Explicit user cancel stays via POST /api/runs/{id}/cancel.
    app = getattr(getattr(server, "config", None), "app", None)
    hub = getattr(getattr(app, "state", None), "cdc_hub", None)
    if hub is not None:
        try:
            hub.stop()
        except Exception:
            pass
    server.should_exit = True


def run_server(host: str, port: int, static_dir: Path,
               allowed_hosts: list[str]) -> None:
    # Legacy workagent.service units (installed before WORKAGENT_SERVICE was
    # added) export NO_COLOR=1 without WORKAGENT_SERVICE=1, and `install.sh`
    # only restarts the unit. Mark WORKAGENT_SERVICE=1 when started by systemd
    # (INVOCATION_ID present) so spawned interactive terminals drop NO_COLOR.
    if os.environ.get("INVOCATION_ID"):
        from .service import SERVICE_MARKER as _SERVICE_MARKER
        os.environ.setdefault(_SERVICE_MARKER, "1")
    try:
        from .env import apply_env_file as _apply_env
        _apply_env()
    except Exception:
        pass
    from .store_sqlite import db_path, init_db
    init_db(db_path(), defer_backfill=True)
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
    start_background_maintenance()
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    def handle_exit(sig: int, frame: Any) -> None:
        _shutdown_leave_detached_running(server)
    import types
    server.handle_exit = types.MethodType(
        lambda self, sig, frame: handle_exit(sig, frame), server)
    server.run()
