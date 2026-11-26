"""Repo/tool display helpers shared by status/cleanup/sync (from cli.py)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import repos, store
from .errors import HarnessError

from .swr import cache_swr as _cache_swr
from .swr import cache_ttl as _cache_ttl

_PR_STYLES = {"open": "bright_green", "merged": "bright_magenta", "closed": "bright_red"}
_CI_SYMBOLS = {"success": "✓", "failure": "✗", "running": "●"}
_CI_STYLES = {"success": "bright_green", "failure": "bright_red", "running": "cyan"}
_DB_LOCK = threading.Lock()
_DB_POS_TTL_S = 600.0
_DB_NEG_TTL_S = 60.0
_DB_CACHE: dict[str, tuple[float, str]] = {}
# Fresh TTLs (seconds), each overridable via its WORKAGENT_* env var
# (unset/invalid → default). SWR windows: explicit WORKAGENT_*_SWR_SECONDS
# wins, else max(ttl * 10, 1 day) — cached rows stay servable while a
# background refetch refreshes them (Dashboard tables render instantly).
def _status_ttl() -> int:
    """Fresh TTL for terminal (merged/closed) PR cells (default 3d)."""
    return _cache_ttl("WORKAGENT_STATUS_TTL_SECONDS", 3 * 24 * 3600)


def _status_open_ttl() -> int:
    """Fresh TTL for open PR cells (default 10m; falls back to explicit WORKAGENT_STATUS_TTL_SECONDS)."""
    import os
    if str(os.environ.get("WORKAGENT_STATUS_OPEN_TTL_SECONDS", "")).strip():
        return _cache_ttl("WORKAGENT_STATUS_OPEN_TTL_SECONDS", 600)
    if str(os.environ.get("WORKAGENT_STATUS_TTL_SECONDS", "")).strip():
        return _cache_ttl("WORKAGENT_STATUS_TTL_SECONDS", 600)
    return 600


def _negative_ttl() -> int:
    """Fresh TTL for cached no-PR results (default 30m)."""
    return _cache_ttl("WORKAGENT_NEGATIVE_TTL_SECONDS", 30 * 60)


def _ci_ttl() -> int:
    """Fresh TTL for CI/review/issue cells (default 10m)."""
    return _cache_ttl("WORKAGENT_CI_TTL_SECONDS", 600)


def _status_swr() -> int:
    """SWR window for positive PR cells (env or max(ttl*10, 1d))."""
    return _cache_swr(_status_ttl(), "WORKAGENT_STATUS_SWR_SECONDS")


def _negative_swr() -> int:
    """SWR window for cached no-PR results (env or max(ttl*10, 1d))."""
    return _cache_swr(_negative_ttl(), "WORKAGENT_NEGATIVE_SWR_SECONDS")


def _ci_swr() -> int:
    """SWR window for CI/review/issue cells (env or max(ttl*10, 1d))."""
    return _cache_swr(_ci_ttl(), "WORKAGENT_CI_SWR_SECONDS")


def _repo_default_branch(repo: str) -> str | None:
    now = time.monotonic()
    with _DB_LOCK:
        hit = _DB_CACHE.get(repo)
        if hit is not None:
            ts, val = hit
            ttl = _DB_POS_TTL_S if val else _DB_NEG_TTL_S
            if now - ts < ttl:
                return val or None
            _DB_CACHE.pop(repo, None)
    try:
        val = repos.default_branch(Path(repo))
    except HarnessError:
        val = ""
    with _DB_LOCK:
        _DB_CACHE[repo] = (time.monotonic(), val)
    return val or None


def _repo_tool(repo: str) -> str | None:
    """Host CLI (gh/glab) for *repo*, persisted in the repo registry.

    A stored tool is reused while the registered remote URL still matches
    the repo's current origin; a changed/missing remote re-detects and
    updates the entry. Unregistered repos are detected fresh (no store).
    """
    registry = store.load_repos()
    hit = next(((n, e) for n, e in registry.items() if e.get("path") == repo), (None, None))
    entry = hit[1]
    remote = repos.remote_url(Path(repo))
    if (entry and entry.get("tool") in ("gh", "glab")
            and entry.get("remote") == remote):
        return entry["tool"]
    tool = (repos._detect_host_cli(Path(repo))
            if Path(repo).exists() else None)
    if entry is not None and (tool != entry.get("tool")
                              or remote != entry.get("remote")):
        from . import store_sqlite as sq
        try:
            if store._sqlite_path() is not None:
                row = sq.load_repos(sq.db_path()).get(hit[0], {})
                tids = row.get("trackers", []) or ([row["tracker"]] if row.get("tracker") else [])
                if tids:
                    sq.register_repo_row(sq.db_path(), hit[0], repo,
                                         tids[0], remote=remote, tool=tool)
                else:
                    # Repo survived a `tracker remove` cascade unlinked: refresh
                    # tool/remote only (derivation may need network), never crash
                    # on the mandatory-tracker guard.
                    cur = row.get("remote", "")
                    sq.register_repo_row_unlinked(sq.db_path(), hit[0], repo,
                                                  remote=remote or cur, tool=tool)
            else:
                cfg = store.load_config()
                cfg["repos"][hit[0]]["tool"] = tool
                cfg["repos"][hit[0]]["remote"] = remote
                store.save_config(cfg)
        except Exception as e:
            # Read path (status/serve): a stale registry row (e.g. pytest tmp
            # repo leaked into the live DB) must not 500 the whole query.
            from .cli_core import eprint as _eprint
            _eprint(f"warning: repo tool refresh failed for {repo}: {e}")
    return tool
