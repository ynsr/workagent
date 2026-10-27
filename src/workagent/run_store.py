"""Per-run state dirs for detached runs (survive serve restarts)."""

from __future__ import annotations

import contextlib
import fcntl
import json
import shutil
from pathlib import Path


def runs_root() -> Path:
    from . import store as _store
    p = _store.config_dir() / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_run_dir(run_id: str) -> Path:
    d = runs_root() / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "output.log").touch(exist_ok=True)
    return d


def write_meta(d: Path, meta: dict) -> None:
    (d / "meta.json").write_text(json.dumps(meta, indent=1))


def read_meta(d: Path) -> dict:
    try:
        return json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        return {}


def write_pid(d: Path, pid: int, ticks: int | None) -> None:
    (d / "pid").write_text(f"{pid} {ticks if ticks is not None else -1}\n")


def read_pid(d: Path) -> tuple[int | None, int | None]:
    try:
        pid_s, ticks_s = (d / "pid").read_text().split()
        ticks = int(ticks_s)
        return int(pid_s), (ticks if ticks >= 0 else None)
    except (OSError, ValueError):
        return None, None


def write_exit(d: Path, code: int) -> None:
    (d / "exit_code").write_text(f"{code}\n")


def read_exit(d: Path) -> int | None:
    try:
        return int((d / "exit_code").read_text().strip())
    except (OSError, ValueError):
        return None


def iter_run_dirs() -> list[Path]:
    root = runs_root()
    ds = [p for p in root.iterdir() if p.is_dir()]
    ds.sort(key=lambda p: p.stat().st_mtime)
    return ds


def prune_finished(keep: int = 100) -> int:
    finished = [d for d in iter_run_dirs() if read_exit(d) is not None]
    pruned = 0
    for d in finished[: max(0, len(finished) - keep)]:
        shutil.rmtree(d, ignore_errors=True)
        pruned += 1
    return pruned


@contextlib.contextmanager
def lock(d: Path):
    with open(d / ".lock", "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
