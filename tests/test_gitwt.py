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
    assert "not found locally or as origin/nope/x" in str(excinfo.value)


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


def test_start_worktree_skips_branch_ensure_when_asked(tmp_path, monkeypatch):
    """Fan-out: a branch fresh in the main repo need not exist in the sibling.

    Regression: `_fan_out_related` passes the just-created feature branch,
    which cannot exist in the related repo yet — `_ensure_local_branch`
    errored "not found locally or as origin/<branch>" instead of letting
    `git-wt start --branch` create it via `worktree add -b`.
    """
    from unittest.mock import patch

    main = _origin_clone(tmp_path)
    sibling = tmp_path / "sibling"
    import subprocess as _sp
    _sp.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(sibling)], check=True)
    _git("checkout", "-qb", "feat/fresh--x", cwd=main)
    (main / "n.txt").write_text("n\n", encoding="utf-8")
    _git("add", "-A", cwd=main)
    _git("commit", "-q", "-m", "feat", cwd=main)
    assert not _has_branch(sibling, "feat/fresh--x")

    # Default still guards checkout-existing flows (PR source, --base mode).
    import pytest as _pytest
    from workagent.errors import HarnessError as _HE
    with _pytest.raises(_HE, match="not found locally"):
        gitwt._ensure_local_branch(sibling, "feat/fresh--x")

    # ... but start_worktree(ensure_branch=False) skips that check and
    # defers to git-wt, which creates the missing branch itself.
    seen = {}

    def fake_run_start(args, repo):
        seen["args"] = args
        assert "--branch" in args and "feat/fresh--x" in args
        wt = tmp_path / "wt-fresh"
        import subprocess as _sp2
        # A real git worktree so _repair_path_mismatch short-circuits
        # (it only recreates via --resume when the path isn't one).
        _sp2.run(["git", "worktree", "add", "-b", "feat/fresh--x",
                  str(wt), "main"], cwd=str(sibling), check=True,
                 capture_output=True)
        return {"worktree_path": str(wt), "branch": "feat/fresh--x"}

    monkeypatch.setattr(gitwt, "_run_start", fake_run_start)
    monkeypatch.setattr(gitwt, "_fetch_base", lambda *a, **k: None)
    monkeypatch.setattr(gitwt, "_ensure_upstream", lambda *a, **k: None)
    out = gitwt.start_worktree(sibling, branch="feat/fresh--x", base="main",
                               ensure_branch=False)
    assert out["branch"] == "feat/fresh--x"
    assert "--base" in seen["args"] and "main" in seen["args"]

def test_ensure_upstream_drops_legacy_fake_ref(tmp_path):
    """A local-only update-ref origin/<branch> is dropped, not kept.

    Regression: old git-wt creates fabricated refs/remotes/origin/<branch>;
    keeping it shadows the real remote and breaks git's tracking check
    ("starting point ... is not a branch"). Plain config states the intent.
    """
    repo = _origin_clone(tmp_path)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-b", "feat/fake", str(wt), "main", cwd=repo)
    # Simulate the legacy fake: local-only remote-tracking ref + upstream.
    _git("update-ref", "refs/remotes/origin/feat/fake", "HEAD", cwd=wt)
    _git("config", "branch.feat/fake.remote", "origin", cwd=wt)
    _git("config", "branch.feat/fake.merge", "refs/heads/feat/fake", cwd=wt)

    result = {"worktree_path": str(wt), "branch": "feat/fake"}
    gitwt._ensure_upstream(result, "main")

    assert result.get("upstream_fixed") is True
    assert gitwt._read_upstream(str(wt), "feat/fake") == "origin/feat/fake"
    assert subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet",
         "refs/remotes/origin/feat/fake"],
        cwd=str(wt), capture_output=True, check=False).returncode != 0
