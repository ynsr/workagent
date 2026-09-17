"""Tests for git_wt.git_utils."""

from __future__ import annotations

import pytest

from git_wt.git_utils import GitRepo, _parse_issue_id, has_command


class TestGitRepo:
    def test_root(self, tmp_repo):
        repo = GitRepo(tmp_repo)
        assert repo.root == tmp_repo.resolve()

    def test_root_not_a_repo(self, tmp_path):
        repo = GitRepo(tmp_path / "nonexistent")
        with pytest.raises(Exception):
            _ = repo.root

    def test_current_branch(self, tmp_repo):
        repo = GitRepo(tmp_repo)
        assert repo.current_branch() == "main"

    def test_name(self, tmp_repo):
        repo = GitRepo(tmp_repo)
        assert repo.name == tmp_repo.name

    def test_is_protected(self, tmp_repo):
        repo = GitRepo(tmp_repo)
        assert repo.is_protected("main")
        assert repo.is_protected("develop")
        assert repo.is_protected("master")
        assert not repo.is_protected("feat/123")

    def test_branch_exists_local(self, tmp_repo_with_feature):
        repo = GitRepo(tmp_repo_with_feature)
        assert repo.branch_exists("main")
        assert repo.branch_exists("feat/42-add-auth")

    def test_branch_not_exists(self, tmp_repo):
        repo = GitRepo(tmp_repo)
        assert not repo.branch_exists("nonexistent")


class TestGitUtils:
    def test_has_command(self):
        assert has_command("git")
        assert not has_command("this-command-should-not-exist-xyz")

    def test_parse_issue_id(self):
        assert _parse_issue_id("feat/123-add-auth") == "123"
        assert _parse_issue_id("fix-4567") == "4567"
        assert _parse_issue_id("chore/deps") is None
        assert _parse_issue_id("") is None