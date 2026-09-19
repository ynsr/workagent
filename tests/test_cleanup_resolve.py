"""Fuzzy cleanup ref resolution (TDD RED)."""

from __future__ import annotations

from harness import cli


def _links():
    return {
        "pr:https://git.jibit.cloud/server/projectx/-/merge_requests/1701": {
            "worktree": "/home/bs/dev/worktrees/projectx/chore/IPG-978--cicd",
            "branch": "chore/IPG-978--cicd-auto-versioning",
            "repo": "/home/bs/projects/jibit/cloud/projectx",
        },
        "jira:IPG-981": {
            "worktree": "/home/bs/dev/worktrees/ynsr-skills/feat/IPG-981--x",
            "branch": "feat/IPG-981--x",
            "repo": "/home/bs/projects/personal/ynsr-skills",
        },
    }


def test_exact_key_resolves():
    assert cli._resolve_session_key("jira:IPG-981", _links()) == "jira:IPG-981"


def test_bare_number_matches_issue_number():
    assert cli._resolve_session_key("IPG-978", _links()) == (
        "pr:https://git.jibit.cloud/server/projectx/-/merge_requests/1701"
    )


def test_branch_substring_resolves():
    assert cli._resolve_session_key("chore/IPG-978--cicd-auto-versioning", _links()) == (
        "pr:https://git.jibit.cloud/server/projectx/-/merge_requests/1701"
    )


def test_worktree_path_resolves():
    assert (
        cli._resolve_session_key(
            "/home/bs/dev/worktrees/projectx/chore/IPG-978--cicd-auto-versioning",
            _links(),
        )
        == "pr:https://git.jibit.cloud/server/projectx/-/merge_requests/1701"
    )


def test_no_match_returns_none():
    assert cli._resolve_session_key("NOPE-1", _links()) is None


def test_multiple_matches_returns_list():
    links = dict(_links())
    links["jira:IPG-978"] = {
        "worktree": "/wt/other",
        "branch": "feat/IPG-978--other",
        "repo": "/r",
    }
    matches = cli._resolve_session_key("IPG-978", links)
    assert isinstance(matches, list) and len(matches) == 2
