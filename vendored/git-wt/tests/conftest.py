"""Shared pytest fixtures for git-wt tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Isolate ~/dev/worktrees/ per test so worktree tests don't collide."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _git(*args: str, cwd: str | Path | None = None) -> None:
    """Run a git command, raise on failure."""
    kwargs = {}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                   **kwargs)


@pytest.fixture(scope="function")
def tmp_repo() -> Path:
    """Create a temporary bare remote and a clone to use as a local repo.

    The repo has a 'main' branch with one commit.  Returns the path to the
    local clone (NOT bare).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="git-wt-test-"))
    remote_dir = tmp_dir / "remote.git"
    clone_dir = tmp_dir / "repo"

    remote_dir.mkdir(parents=True)
    _git("init", "--bare", str(remote_dir))

    # Clone, make an initial commit, push
    _git("clone", str(remote_dir), str(clone_dir))
    _git("config", "user.email", "test@test", cwd=clone_dir)
    _git("config", "user.name", "Test", cwd=clone_dir)
    (clone_dir / "README.md").write_text("# Test\n")
    _git("add", ".", cwd=clone_dir)
    _git("commit", "-m", "initial commit", cwd=clone_dir)
    _git("branch", "-M", "main", cwd=clone_dir)
    _git("push", "-u", "origin", "main", cwd=clone_dir)

    return clone_dir


@pytest.fixture(scope="function")
def tmp_repo_with_feature(tmp_repo: Path) -> Path:
    """Create a repo with an additional feature branch."""
    _git("checkout", "-b", "feat/42-add-auth", cwd=tmp_repo)
    (tmp_repo / "auth.py").write_text("# auth module\n")
    _git("add", ".", cwd=tmp_repo)
    _git("commit", "-m", "add auth feature", cwd=tmp_repo)
    _git("push", "-u", "origin", "feat/42-add-auth", cwd=tmp_repo)
    _git("checkout", "main", cwd=tmp_repo)
    return tmp_repo