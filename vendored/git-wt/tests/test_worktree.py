"""Tests for git_wt.worktree — start, finish, cleanup, branch naming."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git_wt.worktree import (
    build_branch_name,
    cleanup_task,
    start_task,
)


def _git(*args: str, cwd: str | Path | None = None) -> str:
    kwargs = {}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


# ── build_branch_name ───────────────────────────────────────────────


def test_branch_name_explicit():
    assert build_branch_name(branch="my/custom-branch") == "my/custom-branch"


def test_branch_name_type_only():
    assert build_branch_name(type_="feat") == "feat"


def test_branch_name_type_issue():
    assert build_branch_name(type_="feat", issue="123") == "feat/123"


def test_branch_name_type_issue_slug():
    assert build_branch_name(type_="fix", issue="456", slug="npe-on-refund") == "fix/456--npe-on-refund"


def test_branch_name_slug_only():
    assert build_branch_name(slug="my-branch") == "my-branch"


def test_branch_name_nothing():
    assert build_branch_name() == ""


def test_branch_name_sanitize_colon():
    """Slug with colon and spaces gets sanitized to dashes."""
    result = build_branch_name(type_="fix", issue="IPG-930", slug="feature-flag: accept terminal UUID as entityRef in NATS APIs")
    assert result == "fix/IPG-930--feature-flag-accept-terminal-UUID-as-entityRef-in-NATS-APIs"


def test_branch_name_sanitize_invalid_chars():
    """Slug with multiple invalid chars all replaced."""
    result = build_branch_name(type_="fix", issue="123", slug="bad;chars!here?and:there")
    assert result == "fix/123--bad-chars-here-and-there"


def test_branch_name_sanitize_leading_trailing():
    """Leading/trailing dashes and dots stripped."""
    result = build_branch_name(slug=".leading-dash-.trailing.")
    assert result == "leading-dash-.trailing"


def test_branch_name_sanitize_collapse():
    """Consecutive invalid chars collapse to one dash."""
    result = build_branch_name(slug="a???b!!!c")
    assert result == "a-b-c"


def test_branch_name_sanitize_only_invalid():
    """All-invalid slug falls back to 'unnamed'."""
    result = build_branch_name(type_="fix", slug=":::???")
    assert result == "fix--unnamed"


def test_branch_name_explicit_untouched():
    """Explicit --branch bypass is NOT sanitized (user's responsibility)."""
    result = build_branch_name(branch="fix/IPG-930--bad:char")
    assert result == "fix/IPG-930--bad:char"


# ── start_task ──────────────────────────────────────────────────────


class TestStartTask:
    def test_start_new_branch(self, tmp_repo):
        """Create a new branch + worktree."""
        result = start_task(
            repo_path=tmp_repo,
            branch="feat/100-test",
            base="main",
            on_dirty="ignore",
        )
        assert result["action"] == "created"
        assert result["branch"] == "feat/100-test"
        wt_path = result["worktree_path"]
        assert wt_path.exists()
        # Verify it's a real worktree
        assert _git("rev-parse", "--git-dir", cwd=wt_path)
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == "feat/100-test"

    def test_start_resume_existing_worktree(self, tmp_repo):
        """Resume a branch that already has a worktree."""
        # Create worktree first
        r1 = start_task(repo_path=tmp_repo, branch="feat/200-resume", base="main", on_dirty="ignore")
        wt = r1["worktree_path"]

        # Resume
        r2 = start_task(repo_path=tmp_repo, resume="feat/200-resume")
        assert r2["action"] == "resumed"
        assert r2["worktree_path"] == wt

    def test_start_ephemeral(self, tmp_repo):
        """Ephemeral worktree uses a temp dir."""
        result = start_task(
            repo_path=tmp_repo,
            branch="feat/300-ephemeral",
            base="main",
            ephemeral=True,
            on_dirty="ignore",
        )
        assert result["action"] == "created"
        wt_path = str(result["worktree_path"])
        # Should NOT be under ~/dev/worktrees/
        assert "dev/worktrees" not in wt_path
        assert wt_path.startswith("/tmp/") or "/tmp" in wt_path

    def test_start_branch_builder(self, tmp_repo):
        """Use --type/--issue/--slug to build branch name."""
        result = start_task(
            repo_path=tmp_repo,
            type_="feat",
            issue="42",
            slug="add-login",
            base="main",
            on_dirty="ignore",
        )
        assert result["branch"] == "feat/42--add-login"
        assert result["action"] == "created"

    def test_start_no_base_raises(self, tmp_repo, tmp_path):
        """Without --base and no remote default, should raise."""
        from git_wt.git_utils import GitWtError
        # Use a fresh git init with no remote (no origin/HEAD)
        bare_dir = tmp_path / "no-remote.git"
        bare_dir.mkdir()
        _git("init", "-q", str(bare_dir))
        with pytest.raises(GitWtError):
            start_task(
                repo_path=bare_dir,
                branch="feat/400-nobase",
                on_dirty="ignore",
            )

    def test_start_branch_already_exists_local(self, tmp_repo):
        """Starting a branch that already exists locally should raise."""
        from git_wt.git_utils import GitWtError

        # Create it first
        start_task(repo_path=tmp_repo, branch="feat/500-dup", base="main", on_dirty="ignore")

        with pytest.raises(GitWtError, match="already exists"):
            start_task(repo_path=tmp_repo, branch="feat/500-dup", base="main", on_dirty="ignore")

    def test_start_with_link_derives_components(self, tmp_repo, monkeypatch):
        """--link populates type_/issue/slug from fetched title."""
        from git_wt.link_utils import (
            detect_tool_from_link,
            extract_issue_id,
            fetch_issue_title,
            parse_title_to_branch_components,
        )

        def mock_fetch_title(_link, _tool):
            return "[PPG] Improve login timeout"

        def mock_parse(title, issue_id):
            return {"type_": "feat", "issue": issue_id, "slug": "improve-login-timeout"}

        monkeypatch.setattr("git_wt.worktree.fetch_issue_title", mock_fetch_title)
        monkeypatch.setattr("git_wt.worktree.parse_title_to_branch_components", mock_parse)

        result = start_task(
            repo_path=tmp_repo,
            link="https://tribe.jibit.cloud/browse/IPG-999",
            base="main",
            on_dirty="ignore",
        )
        assert result["action"] == "created"
        assert result["branch"] == "feat/IPG-999--improve-login-timeout"
        assert result["worktree_path"].exists()

    def test_start_with_link_and_explicit_override(self, tmp_repo, monkeypatch):
        """Explicit --type overrides link-derived values."""
        def mock_fetch_title(_link, _tool):
            return "chore: clean up"
        def mock_parse(title, issue_id):
            return {"type_": "chor", "issue": issue_id, "slug": "clean-up"}
        monkeypatch.setattr("git_wt.worktree.fetch_issue_title", mock_fetch_title)
        monkeypatch.setattr("git_wt.worktree.parse_title_to_branch_components", mock_parse)

        result = start_task(
            repo_path=tmp_repo,
            link="https://github.com/owner/repo/issues/42",
            type_="fix",
            base="main",
            on_dirty="ignore",
        )
        assert result["branch"] == "fix/42--clean-up"
        assert result["action"] == "created"


# ── cleanup_task ─────────────────────────────────────────────────────


class TestCleanupTask:
    def test_cleanup_worktree(self, tmp_repo):
        """Remove a worktree with --force (no gh/glab to check PR state)."""
        result = start_task(
            repo_path=tmp_repo, branch="feat/600-cleanup", base="main", on_dirty="ignore",
        )

        # Without gh/glab, PR check returns None → cleanup proceeds without state guard.
        # Use --force to be explicit.
        r2 = cleanup_task(repo_path=tmp_repo, branch="feat/600-cleanup", force=True)
        assert any("removed worktree" in a for a in r2["actions"])

    def test_cleanup_with_delete_branch(self, tmp_repo):
        """Cleanup and delete branch."""
        result = start_task(
            repo_path=tmp_repo, branch="feat/700-delbranch", base="main", on_dirty="ignore",
        )
        r2 = cleanup_task(
            repo_path=tmp_repo, branch="feat/700-delbranch", force=True, delete_branch=True,
        )
        actions = " ".join(r2["actions"])
        assert "removed worktree" in actions
        assert "deleted local branch" in actions