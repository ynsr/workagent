"""repos.py git-linked helpers (main-repo resolution, worktree detection)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness import repos


def _git(*args, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _repo_with_worktree(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "i", cwd=repo)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-b", "feat/x", str(wt), "main", cwd=repo)
    return repo, wt, "feat/x"


def test_main_repo_root_resolves_linked_worktree(tmp_path):
    repo, wt, _ = _repo_with_worktree(tmp_path)
    assert repos.main_repo_root(wt) == repo
    assert repos.main_repo_root(repo) == repo


def test_worktree_branch_only_inside_linked_worktree(tmp_path):
    repo, wt, branch = _repo_with_worktree(tmp_path)
    assert repos.worktree_branch(wt) == branch
    assert repos.worktree_branch(repo) is None  # main checkout: never continues
