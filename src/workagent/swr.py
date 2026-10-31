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
