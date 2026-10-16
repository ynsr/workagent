"""flock guard for releases: <worktrees-base>/<repo-name>/.release.lock."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

from .errors import HarnessError


def lock_path(base: str, repo_name: str) -> Path:
    return Path(base) / repo_name / ".release.lock"


@contextmanager
def held_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = path.parent.name
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise HarnessError(f"release already running for {name}", exit_code=1)
        try:
            yield path
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
