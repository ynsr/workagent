"""SQLite change_log CDC poller and pub/sub hub for GET /api/events SSE."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import store_sqlite as _sq

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 0.25
DEFAULT_PRUNE_INTERVAL = 60.0
DEFAULT_HEARTBEAT_INTERVAL = 15.0
SUBSCRIBER_QUEUE_MAX = 1000


class CdcHub:
    """Tails SQLite ``change_log`` on a background thread and fans out events
    to async SSE subscribers.

    Lifecycle:
    - ``start()`` launches the daemon poller thread (idempotent).
    - ``stop()`` signals the poller thread to exit, wakes every open SSE
      subscriber with a ``None`` sentinel, and joins the thread briefly.
    """

    def __init__(
        self,
        db_path_fn: Callable[[], Path] | None = None,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        prune_interval: float = DEFAULT_PRUNE_INTERVAL,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        max_rows: int = _sq.CHANGE_LOG_MAX_ROWS,
    ) -> None:
        self._db_path_fn = db_path_fn or _sq.db_path
        self.poll_interval = max(0.01, float(poll_interval))
        self.prune_interval = max(1.0, float(prune_interval))
        self.heartbeat_interval = max(0.1, float(heartbeat_interval))
        self.max_rows = max(1, int(max_rows))

        self._lock = threading.Lock()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any] | None]]] = set()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._cursor: int = 0
        self._cursor_initialized: bool = False
        self._last_prune: float = 0.0

    def _db_path(self) -> Path:
        return self._db_path_fn()

    def current_seq(self) -> int:
        db = self._db_path()
        if not db.exists():
            return 0
        try:
            return _sq.latest_change_seq(db)
        except Exception as exc:
            logger.debug("cdc current_seq failed: %s", exc)
            return 0

    def replay(self, after_seq: int, limit: int = 500) -> list[dict[str, Any]]:
        db = self._db_path()
        if not db.exists():
            return []
        try:
            return _sq.read_change_log(db, after_seq=after_seq, limit=limit)
        except Exception as exc:
            logger.debug("cdc replay failed: %s", exc)
            return []

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[dict[str, Any] | None]:
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            if self._stop.is_set():
                q.put_nowait(None)
                return q
            self._subscribers.add((loop, q))
        self._wake.set()
        return q

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, q: asyncio.Queue[dict[str, Any] | None]) -> None:
        with self._lock:
            self._subscribers.discard((loop, q))

    def _broadcast(self, event: dict[str, Any] | None) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for loop, q in subs:
            if loop.is_closed():
                self.unsubscribe(loop, q)
                continue
            try:
                loop.call_soon_threadsafe(self._enqueue, q, event)
            except RuntimeError:
                self.unsubscribe(loop, q)

    @staticmethod
    def _enqueue(q: asyncio.Queue[dict[str, Any] | None], event: dict[str, Any] | None) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest item to keep slow clients moving; if event is None
            # (shutdown sentinel), make room for it unconditionally.
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass

    def poll_once(self) -> list[dict[str, Any]]:
        """Read any new change_log rows since the last cursor and broadcast them."""
        db = self._db_path()
        if not db.exists():
            return []
        try:
            if not self._cursor_initialized:
                self._cursor = _sq.latest_change_seq(db)
                self._cursor_initialized = True
                self._last_prune = time.monotonic()
                return []
            rows = _sq.read_change_log(db, after_seq=self._cursor, limit=500)
            if rows:
                self._cursor = int(rows[-1]["seq"])
                for row in rows:
                    self._broadcast(row)
            now = time.monotonic()
            if now - self._last_prune >= self.prune_interval:
                self._last_prune = now
                try:
                    _sq.prune_change_log(db, max_rows=self.max_rows)
                except Exception as exc:
                    logger.debug("cdc prune failed: %s", exc)
            return rows
        except Exception as exc:
            logger.debug("cdc poll_once failed: %s", exc)
            return []

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                has_subs = bool(self._subscribers)
            if has_subs or not self._cursor_initialized:
                self.poll_once()
            else:
                now = time.monotonic()
                if now - self._last_prune >= self.prune_interval:
                    self._last_prune = now
                    db = self._db_path()
                    if db.exists():
                        try:
                            _sq.prune_change_log(db, max_rows=self.max_rows)
                        except Exception as exc:
                            logger.debug("cdc prune failed: %s", exc)
            wait_for = self.poll_interval if has_subs else self.prune_interval
            self._wake.wait(wait_for)
            self._wake.clear()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.clear()
            self._cursor_initialized = False
            self._thread = threading.Thread(
                target=self._run_loop,
                name="workagent-cdc",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        self._broadcast(None)
        with self._lock:
            t = self._thread
            self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()
