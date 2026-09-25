"""git-wt wrapper helpers exercised against real git (no git-wt binary needed)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from workagent import gitwt
from workagent.errors import HarnessError


def _git(*args, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _origin_clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "proj"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    _git("push", "-q", "-u", "origin", "main", cwd=repo)
    return repo

def _has_branch(repo: Path, name: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=str(repo), capture_output=True, check=False,
    ).returncode == 0


def test_ensure_local_branch_materializes_remote_only(tmp_path):
    repo = _origin_clone(tmp_path)
    _git("branch", "chore/remote-only", cwd=repo)
    _git("push", "-q", "origin", "chore/remote-only", cwd=repo)
    _git("branch", "-D", "chore/remote-only", cwd=repo)  # now remote-only
    assert not _has_branch(repo, "chore/remote-only")

    gitwt._ensure_local_branch(repo, "chore/remote-only")

    assert _has_branch(repo, "chore/remote-only")
    upstream = subprocess.run(["git", "config", "branch.chore/remote-only.merge"],
                              cwd=str(repo), capture_output=True, text=True,
                              check=True).stdout.strip()
    assert upstream == "refs/heads/chore/remote-only"


def test_ensure_local_branch_keeps_existing_local(tmp_path):
    repo = _origin_clone(tmp_path)
    _git("branch", "feat/local", cwd=repo)
    before = subprocess.run(["git", "rev-parse", "refs/heads/feat/local"],
                            cwd=str(repo), capture_output=True, text=True,
                            check=True).stdout

    gitwt._ensure_local_branch(repo, "feat/local")

    after = subprocess.run(["git", "rev-parse", "refs/heads/feat/local"],
                           cwd=str(repo), capture_output=True, text=True,
                           check=True).stdout
    assert before == after


def test_ensure_local_branch_missing_everywhere(tmp_path):
    repo = _origin_clone(tmp_path)
    with pytest.raises(HarnessError) as excinfo:
        gitwt._ensure_local_branch(repo, "nope/x")
    assert "git fetch" in str(excinfo.value)


def test_ensure_local_branch_fetches_stale_mirror(tmp_path):
    """A branch pushed after our last fetch is found via fetch, not an error."""
    repo = _origin_clone(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(other)],
                   check=True)
    _git("config", "user.email", "t@t", cwd=other)
    _git("config", "user.name", "t", cwd=other)
    _git("checkout", "-qb", "feat/stale-mirror", cwd=other)
    (other / "g.txt").write_text("y\n", encoding="utf-8")
    _git("add", "-A", cwd=other)
    _git("commit", "-q", "-m", "feat", cwd=other)
    _git("push", "-q", "-u", "origin", "feat/stale-mirror", cwd=other)
    # repo's remote-tracking mirror predates the push.
    assert subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet",
         "refs/remotes/origin/feat/stale-mirror"],
        cwd=str(repo), capture_output=True, check=False).returncode != 0

    gitwt._ensure_local_branch(repo, "feat/stale-mirror")

    assert _has_branch(repo, "feat/stale-mirror")
