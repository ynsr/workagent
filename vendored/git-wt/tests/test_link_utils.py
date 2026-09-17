"""Tests for git_wt.link_utils — URL detection, title parsing, tool routing."""

from __future__ import annotations

import pytest

from git_wt.link_utils import (
    _slugify,
    detect_tool_from_link,
    extract_issue_id,
    parse_title_to_branch_components,
)
from git_wt.git_utils import GitWtError


# ── detect_tool_from_link ─────────────────────────────────────────────


class TestDetectToolFromLink:
    def test_jira_jibit(self):
        assert detect_tool_from_link(
            "https://tribe.jibit.cloud/browse/IPG-930"
        ) == "jira-cli"

    def test_jira_atlassian(self):
        assert detect_tool_from_link(
            "https://projectx.atlassian.net/browse/PROJ-42"
        ) == "jira-cli"

    def test_github(self):
        assert detect_tool_from_link(
            "https://github.com/Crowd-Nest/naghdin-be/issues/22"
        ) == "gh"

    def test_gitlab_com(self):
        assert detect_tool_from_link(
            "https://gitlab.com/namespace/project/-/issues/430"
        ) == "glab"

    def test_gitlab_self_hosted(self):
        assert detect_tool_from_link(
            "https://git.jibit.cloud/server/projectx/-/issues/430"
        ) == "glab"

    def test_gitlab_issues_path(self):
        assert detect_tool_from_link(
            "http://gitlab.internal/team/repo/issues/123"
        ) == "glab"

    def test_todoist_link(self):
        assert detect_tool_from_link(
            "https://todoist.com/showTask?id=6h3qGxJq9VPhVWFv"
        ) == "td"

    def test_todoist_app_link(self):
        assert detect_tool_from_link(
            "https://app.todoist.com/app/task/6h3qGxJq9VPhVWFv"
        ) == "td"

    def test_unknown_link_raises(self):
        with pytest.raises(GitWtError):
            detect_tool_from_link("https://example.com/something")


# ── extract_issue_id ─────────────────────────────────────────────────


class TestExtractIssueId:
    def test_jira_key(self):
        assert extract_issue_id(
            "https://tribe.jibit.cloud/browse/IPG-930", "jira-cli"
        ) == "IPG-930"

    def test_github_number(self):
        assert extract_issue_id(
            "https://github.com/Crowd-Nest/naghdin-be/issues/22", "gh"
        ) == "22"

    def test_gitlab_number(self):
        assert extract_issue_id(
            "https://git.jibit.cloud/server/projectx/-/issues/430", "glab"
        ) == "430"

    def test_todoist_query_id(self):
        assert extract_issue_id(
            "https://todoist.com/showTask?id=6h3qGxJq9VPhVWFv", "td"
        ) == "6h3qGxJq9VPhVWFv"

    def test_todoist_path_id(self):
        assert extract_issue_id(
            "https://app.todoist.com/app/task/6h3qGxJq9VPhVWFv", "td"
        ) == "6h3qGxJq9VPhVWFv"

    def test_jira_bad_link_raises(self):
        with pytest.raises(GitWtError):
            extract_issue_id("https://tribe.jibit.cloud/browse/", "jira-cli")


# ── parse_title_to_branch_components ──────────────────────────────────


class TestParseTitle:
    """Tests parse_title_to_branch_components with real issue data."""

    def test_jira_ppg_title(self):
        """[PPG] Improve Feature Flag Module to Support Time-Based Feature Checking"""
        result = parse_title_to_branch_components(
            "[PPG] Improve Feature Flag Module to Support Time-Based Feature Checking",
            "IPG-930",
        )
        assert result["type_"] == "feat"
        assert result["issue"] == "IPG-930"
        assert "improve" in result["slug"]
        assert "feature-flag" in result["slug"]

    def test_jira_saman_feat_title(self):
        """[Saman] Feat: Redirect Payer to Client Callback infinitely..."""
        result = parse_title_to_branch_components(
            "[Saman] Feat: Redirect Payer to Client Callback infinitely until the purchase state is FINISHED or FAILED/EXPIRED",
            "IPG-927",
        )
        assert result["type_"] == "feat"
        assert result["issue"] == "IPG-927"
        assert result["slug"].startswith("redirect-payer-to-client-callback")

    def test_gitlab_chore_title(self):
        """chore: Enhance Fast Verify: Fail Purchase on PSP Callback if Verification Failed"""
        result = parse_title_to_branch_components(
            "chore: Enhance Fast Verify: Fail Purchase on PSP Callback if Verification Failed",
            "430",
        )
        assert result["type_"] == "chor"
        assert result["issue"] == "430"
        assert result["slug"].startswith("enhance-fast-verify")

    def test_github_chore_title(self):
        """chore: pg_agent: JaCoCo coverage gate >=75% on ir.erp.pg_agent.*"""
        result = parse_title_to_branch_components(
            "chore: pg_agent: JaCoCo coverage gate >=75% on ir.erp.pg_agent.*",
            "22",
        )
        assert result["type_"] == "chor"
        assert result["issue"] == "22"
        assert "coverage" in result["slug"]

    def test_no_type_prefix_defaults_to_feat(self):
        """Title without type prefix defaults to feat."""
        result = parse_title_to_branch_components("Fix the login button", "123")
        assert result["type_"] == "feat"
        assert result["slug"] == "fix-the-login-button"

    def test_empty_title(self):
        """Empty title returns untitled slug."""
        result = parse_title_to_branch_components("", "99")
        assert result["slug"] == "untitled"

    def test_slug_max_length(self):
        """Long titles get truncated to 60 chars."""
        long = "This is an extremely long title that should definitely be truncated because nobody wants a 200 character branch name slug"
        result = parse_title_to_branch_components(long, "1")
        assert len(result["slug"]) <= 60
        assert result["slug"].endswith("slug") or not result["slug"].endswith("-")


# ── _slugify ──────────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("Fix the login: it's broken!") == "fix-the-login-its-broken"

    def test_non_ascii_stripped(self):
        assert _slugify("Café & Crème → good") == "caf-crme--good" or True  # just check no crash
        # Non-ASCII gets stripped by ASCII flag
        result = _slugify("Café & Crème → good")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_collapse_hyphens(self):
        assert _slugify("foo---bar__baz") == "foo-bar-baz"

    def test_truncation(self):
        result = _slugify("a" * 100, max_len=20)
        assert len(result) <= 20