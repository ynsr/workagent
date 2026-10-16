"""Detached worktree lifecycle for the release local gate."""
from __future__ import annotations

import shutil
from pathlib import Path

from .errors import HarnessError, run_cmd


def release_path(base: str, repo_name: str) -> Path:
    return Path(base) / repo_name / "release"


def has_submodules(repo_path: str) -> bool:
    return (Path(repo_path) / ".gitmodules").is_file()


def _reclaim(repo_path: str, dest: Path) -> None:
    try:
        run_cmd("git", "-C", repo_path, "worktree", "remove", "--force", str(dest), echo=False)
    except HarnessError:
        pass
    if dest.exists() or dest.is_symlink():
        shutil.rmtree(dest, ignore_errors=True)
    try:
        run_cmd("git", "-C", repo_path, "worktree", "prune", echo=False)
    except HarnessError:
        pass


def prepare(repo_path: str, dest: Path, sha: str) -> Path:
    _reclaim(repo_path, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_cmd("git", "-C", repo_path, "worktree", "add", "--detach", str(dest), sha)
    run_cmd("git", "-C", str(dest), "clean", "-ffdx")
    if has_submodules(repo_path):
        run_cmd("git", "-C", str(dest), "submodule", "update", "--init", "--recursive")
    try:
        run_cmd("git", "-C", str(dest), "lfs", "pull", echo=False)
    except HarnessError:
        pass  # LFS not in use
    return dest


def finish(dest: Path, ok: bool) -> str | None:
    if ok:
        try:
            repo = run_cmd("git", "-C", str(dest), "rev-parse", "--git-common-dir", echo=False)
        except HarnessError:
            repo = ""
        shutil.rmtree(dest, ignore_errors=True)
        if repo:
            try:
                run_cmd("git", "-C", repo.strip(), "worktree", "prune", echo=False)
            except HarnessError:
                pass
        return None
    return str(dest)
