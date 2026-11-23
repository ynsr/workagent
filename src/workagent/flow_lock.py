"""Blocking per-repo flock for sequential flow steps.

Contiguous sequential steps (e.g. ``sync`` → ``cleanup`` → ``release``) hold
the lock continuously so another flow on the same repo cannot interleave a
PR merge between ``sync`` and ``cleanup``. The lock is released as soon as
the next step is ``parallel`` or the flow exits/pauses/fails.
"""
from __future__ import annotations

import fcntl
import re
from pathlib import Path
from typing import Callable

from . import store


def _slug(scope: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (scope or "").strip()).strip("-")
    return slug or "global"


def lock_path(scope: str, *, base: Path | None = None) -> Path:
    """Lock file for one repo scope: ``<config_dir>/locks/flow-seq.<slug>.lock``."""
    root = base if base is not None else (store.config_dir() / "locks")
    return root / f"flow-seq.{_slug(scope)}.lock"


class SequentialStepLock:
    """Re-entrant-by-scope blocking flock held across contiguous sequential steps."""

    def __init__(self, *, base: Path | None = None) -> None:
        self._base = base
        self._fh = None
        self._scope: str | None = None

    @property
    def scope(self) -> str | None:
        return self._scope

    def acquire(
        self,
        scope: str,
        *,
        on_wait: Callable[[str], None] | None = None,
        on_acquired: Callable[[str], None] | None = None,
    ) -> None:
        """Acquire the blocking flock for *scope* (no-op if already held)."""
        norm = _slug(scope)
        if self._fh is not None and self._scope == norm:
            return
        if self._fh is not None:
            self.release()
        path = lock_path(norm, base=self._base)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
        try:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if on_wait is not None:
                    on_wait(norm)
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                if on_acquired is not None:
                    on_acquired(norm)
        except BaseException:
            fh.close()
            raise
        self._fh = fh
        self._scope = norm

    def release(self) -> None:
        """Release the currently held flock, if any."""
        fh = self._fh
        self._fh = None
        self._scope = None
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()

    def __enter__(self) -> SequentialStepLock:
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
