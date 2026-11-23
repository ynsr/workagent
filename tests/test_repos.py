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


def test_ahead_behind_uses_recorded_branch_not_head(tmp_path):
    """Behind|Ahead follows the recorded branch tip, not the worktree HEAD."""
    origin = _bare_origin(tmp_path / "origin.git")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True,
                   capture_output=True)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=work)
    _git("push", "-q", "origin", "HEAD:main", cwd=work)
    _git("checkout", "-q", "-b", "feat", cwd=work)
    _git("commit", "-q", "--allow-empty", "-m", "one", cwd=work)
    _git("commit", "-q", "--allow-empty", "-m", "two", cwd=work)
    _git("checkout", "-q", "main", cwd=work)
    # HEAD is main (0|0 vs origin/main) but the recorded branch is ahead by 2.
    assert repos.ahead_behind(work, "main") == {"behind": 0, "ahead": 0}
    assert repos.ahead_behind(work, "main", "feat") == {"behind": 0, "ahead": 2}
    # Missing branch ref falls back to HEAD instead of failing.
    assert repos.ahead_behind(work, "main", "nope") == {"behind": 0, "ahead": 0}



def test_ahead_behind_no_remote_returns_none(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "i", cwd=repo)
    assert repos.ahead_behind(repo, "main") is None


def test_release_status_dirty_and_pull_repo(tmp_path, isolated_config):
    import pytest
    from workagent import repos_release, store_sqlite as sq
    from workagent.errors import HarnessError

    sq.init_db(sq.db_path())

    origin = _bare_origin(tmp_path / "origin.git")
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    _git("checkout", "-q", "-b", "main", cwd=seed)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    _git("push", "-q", "origin", "main:release", cwd=seed)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(work)], check=True, capture_output=True)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)

    repos.register_repo("proj", work, tracker_key="jira:PROJ", default_branch_name="main", release_branch="release")

    # Clean repo: dirty=False, dirty_files=[]
    st_clean = repos_release.release_status("proj")
    assert st_clean["dirty"] is False
    assert st_clean["dirty_files"] == []

    # Advance origin/main from seed, then pull into work
    (seed / "new.txt").write_text("hi\n")
    _git("add", "new.txt", cwd=seed)
    _git("commit", "-q", "-m", "remote commit", cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)

    pulled = repos_release.pull_repo("proj")
    assert pulled["pulled"] == "proj"
    assert (work / "new.txt").read_text() == "hi\n"

    # Dirty repo: release_status reports dirty_files and pull_repo refuses
    (work / "dirty.txt").write_text("uncommitted\n")
    st_dirty = repos_release.release_status("proj")
    assert st_dirty["dirty"] is True
    assert any("dirty.txt" in line for line in st_dirty["dirty_files"])
    with pytest.raises(HarnessError, match="uncommitted changes"):
        repos_release.pull_repo("proj")

    # Detached HEAD: pull_repo refuses with clear message
    (work / "dirty.txt").unlink()
    _git("checkout", "-q", "--detach", "HEAD", cwd=work)
    with pytest.raises(HarnessError, match="detached HEAD"):
        repos_release.pull_repo("proj")

