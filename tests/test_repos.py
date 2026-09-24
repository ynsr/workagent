"""repos.py git-linked helpers (main-repo resolution, worktree detection)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from workagent import repos


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


def _bare_origin(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)],
                   check=True)
    return path


def test_ahead_behind_counts(tmp_path):
    origin = _bare_origin(tmp_path / "origin.git")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True,
                   capture_output=True)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    _git("commit", "-q", "--allow-empty", "-m", "ahead", cwd=work)
    # Remote gains a commit the clone lacks (pushed via a sibling clone):
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    _git("config", "user.email", "t@t", cwd=other)
    _git("config", "user.name", "t", cwd=other)
    _git("commit", "-q", "--allow-empty", "-m", "remote", cwd=other)
    _git("push", "-q", "origin", "main", cwd=other)
    _git("fetch", "-q", "origin", cwd=work)
    ab = repos.ahead_behind(work, "main")
    assert ab == {"behind": 1, "ahead": 1}



def test_ahead_behind_no_remote_returns_none(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "i", cwd=repo)
    assert repos.ahead_behind(repo, "main") is None
