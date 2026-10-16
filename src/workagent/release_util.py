"""Small release helpers: durations, worktree base, name uniqueness."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import HarnessError

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smh]?)\s*$", re.IGNORECASE)


def parse_duration(s: str) -> int:
    m = _DURATION_RE.match(s or "")
    if not m:
        raise HarnessError(f"bad duration {s!r}: use like 10m, 90s, 2h", exit_code=2)
    mult = {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2).lower()]
    return int(m.group(1)) * mult


def worktree_base() -> Path:
    return Path(os.environ.get("WORKTREES_DIR", "") or (Path.home() / "dev" / "worktrees"))


def validate_unique_repo_names(registry: dict, name: str) -> dict:
    hits = [n for n in registry if n == name]
    if len(hits) != 1:
        raise HarnessError(f"unknown or ambiguous repo: {name}", exit_code=2)
    return registry[name]
