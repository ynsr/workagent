"""Stale-while-revalidate primitive for web read endpoints.

The Dashboard StatusTable and the Candidates Unlinked PR/MRs tab block the
user whenever the backend cache is expired: the API response waits for live
host-CLI/network fetches (gh/glab/jira) that take seconds. The fix is
one shared rule — serve the stale rows now, revalidate in the background:

- `serve_stale_or_refresh(stale, is_fresh, refresh)`: return stale when it
  exists and is not fresh (background thread revalidates once per key);
  run `refresh` inline only when no stale rows exist at all (first load,
  nothing to show yet). Inline refresh exceptions propagate; background
  refresh exceptions are swallowed (next request retries,.refresh errors
  surface as warnings in the revalidated payload).
- One in-flight refresh per key (module-level guard): concurrent requests
  for the same key share a single background thread, never stampede the
  host CLIs.
- Threads are daemon + fire-and-forget: the API response never waits for
  them. SQLite writes use the 30s busy_timeout path, so background writes
  never raise `database is locked`.
"""
from __future__ import annotations

import os

#: Floor for stale-while-revalidate windows: expired rows stay servable
#: for at least a day while a background refresh runs (Dashboard tables
#: render instantly). Every cache below derives its SWR window as
#: ``max(ttl * 10, _SWR_FLOOR_SECONDS)`` unless overridden per-cache.
#:
#: Per-cache overrides (all optional, seconds, invalid → default):
#: - WORKAGENT_STATUS_TTL_SECONDS / WORKAGENT_STATUS_SWR_SECONDS
#:   (status-table linked-PR cells: per-repo open/merged PR lookup;
#:   defaults 259200 / 2592000)
#: - WORKAGENT_NEGATIVE_TTL_SECONDS / WORKAGENT_NEGATIVE_SWR_SECONDS
#:   (cached "no PR for this repo" answers, avoids re-querying empty repos;
#:   defaults 1800 / 86400)
#: - WORKAGENT_CI_TTL_SECONDS / WORKAGENT_CI_SWR_SECONDS
#:   (CI pipeline + review stats + issue status cells; defaults 600 / 86400)
#: - WORKAGENT_ISSUES_TTL_SECONDS / WORKAGENT_ISSUES_SWR_SECONDS
#:   (tracker issue rows: gh/glab/jira issue-list queries; defaults 3600 / 86400)
#: - WORKAGENT_PRS_CACHE_TTL_SECONDS / WORKAGENT_PRS_CACHE_SWR_SECONDS
#:   (unlinked PR/MR candidates tab: open PRs/MRs not linked to a repo;
#:   defaults 600 / 86400)
#: - WORKAGENT_RELEASE_DIFF_TTL_SECONDS / WORKAGENT_RELEASE_DIFF_SWR_SECONDS
#:   (release ahead/behind diff count: commits default..release branch;
#:   defaults 3600 / 86400)
#: - WORKAGENT_RELEASE_CAPS_TTL_SECONDS
#:   (merge-capability memo: per-repo merge strategy ff-push / mr-ff / merge-commit;
#:   default 600)
#: - WORKAGENT_STATUS_TIMEOUT (host-CLI per-call timeout; default 12)
_SWR_FLOOR_SECONDS = 24 * 3600


def _env_seconds(name: str, default: int) -> int:
    """Env override for *name* in seconds; invalid/unset → *default*."""
    raw = os.environ.get(name, "")
    if not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return value if value >= 0 else default


def cache_ttl(env_name: str, default: int) -> int:
    """Fresh TTL for one cache, overridable via ``env_name``."""
    return _env_seconds(env_name, default)


def cache_swr(ttl_value: int, swr_env_name: str) -> int:
    """SWR window for one cache: explicit env wins, else max(ttl*10, 1 day)."""
    raw = os.environ.get(swr_env_name, "")
    if str(raw).strip():
        try:
            value = int(str(raw).strip())
        except ValueError:
            pass
        else:
            if value >= 0:
                return value
    return max(ttl_value * 10, _SWR_FLOOR_SECONDS)


import threading
from collections.abc import Callable
from typing import Any

_IN_FLIGHT: set[str] = set()
_LOCK = threading.Lock()


def _background(key: str, refresh: Callable[[], Any]) -> None:
    """Run `refresh` off-thread; exactly-once per key (guarded)."""
    with _LOCK:
        if key in _IN_FLIGHT:
            return
        _IN_FLIGHT.add(key)

    def _run() -> None:
        try:
            refresh()
        except Exception:
            pass
        finally:
            with _LOCK:
                _IN_FLIGHT.discard(key)

    threading.Thread(target=_run, name=f"swr-{key}", daemon=True).start()


def serve_stale_or_refresh(stale: Any, is_fresh: bool,
                           refresh: Callable[[], Any],
                           key: str) -> tuple[Any, bool]:
    """(payload, served_stale): stale rows now + background refresh, or a
    live refresh when nothing stale exists.

    - `stale` is not None and not fresh → return it immediately, kick one
      background `refresh` (exactly-once per key). Caller marks the
      response stale (the frontend shows its overlay badge).
    - Otherwise → run `refresh()` inline and return its result (first load
      or an explicit force-refresh has nothing to show yet / was asked to
      wait).
    """
    if stale is not None and not is_fresh:
        _background(key, refresh)
        return stale, True
    return refresh(), False
