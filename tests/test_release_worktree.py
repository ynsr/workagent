"""Worktree lifecycle tests (Task 2 RED)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git_repo(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)
    (d / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(d), "add", "."], check=True)
    subprocess.run(["git", "-C", str(d), "commit", "-qm", "init"], check=True)
    return d


def _head(d: Path) -> str:
    return subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _branch(d: Path) -> str:
    r = subprocess.run(["git", "-C", str(d), "symbolic-ref", "--short", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def test_prepare_detached_at_sha(tmp_path):
    from workagent.release_worktree import prepare, release_path
    src = _git_repo(tmp_path / "src")
    sha = _head(src)
    dest = release_path(str(tmp_path / "wt"), "proj")
    prepare(str(src), dest, sha)
    assert _head(dest) == sha
    assert _branch(dest) == ""  # detached, no branch checked out


def test_prepare_reclaims_stale(tmp_path):
    from workagent.release_worktree import prepare
    src = _git_repo(tmp_path / "src")
    sha = _head(src)
    dest = tmp_path / "wt" / "proj" / "release"
    prepare(str(src), dest, sha)
    prepare(str(src), dest, sha)  # second run reclaims the first
    assert _head(dest) == sha


def test_finish_removes_on_success_keeps_on_failure(tmp_path):
    from workagent.release_worktree import prepare, finish
    src = _git_repo(tmp_path / "src")
    sha = _head(src)
    d1 = tmp_path / "w1" / "proj" / "release"
    prepare(str(src), d1, sha)
    assert finish(d1, True) is None
    assert not d1.exists()
    d2 = tmp_path / "w2" / "proj" / "release"
    prepare(str(src), d2, sha)
    kept = finish(d2, False)
    assert kept == str(d2) and d2.exists()


def test_user_clone_untouched(tmp_path):
    # uncommitted changes in src do not affect the detached worktree
    from workagent.release_worktree import prepare
    src = _git_repo(tmp_path / "src")
    (src / "dirty.txt").write_text("uncommitted")
    sha = _head(src)
    dest = tmp_path / "wt" / "proj" / "release"
    prepare(str(src), dest, sha)
    assert not (dest / "dirty.txt").exists()
