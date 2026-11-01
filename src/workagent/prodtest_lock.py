"""Per-issue flock guard for `prodtest run` (one lock per issue key)."""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path

from .errors import HarnessError
from .release_lock import held_lock, locked


def _slug(key: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (key or "").strip()).strip("-")
    return slug or "unknown"


def lock_path(base: str, key: str) -> Path:
    """Lock file for one issue: ``<base>/prodtest.<slug>.lock``."""
    return Path(base) / f"prodtest.{_slug(key)}.lock"


def is_locked(base: str, key: str) -> bool:
    """True when another process holds the per-issue lock (never raises)."""
    return locked(lock_path(base, key))


@contextmanager
def held_issue_lock(base: str, key: str):
    """Hold the per-issue lock; re-raise contention naming the issue key."""
    path = lock_path(base, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with held_lock(path):
            yield path
    except HarnessError as e:
        raise HarnessError(
            f"prodtest run already in progress for {key}", exit_code=1
        ) from e
