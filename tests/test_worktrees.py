"""Triple-key worktree resolution."""
from workagent import worktrees

LINKS = {
    "jira:IPG-929": {"branch": "feat/IPG-929--x", "worktree": "/tmp/wt-a",
                     "repo": "/tmp/proj", "pr_url": "https://x/mr/1"},
}

def test_resolve_by_branch():
    assert worktrees.resolve_worktree("feat/IPG-929--x", LINKS) == "jira:IPG-929"

def test_resolve_by_path():
    assert worktrees.resolve_worktree("/tmp/wt-a", LINKS) == "jira:IPG-929"

def test_resolve_by_key():
    assert worktrees.resolve_worktree("jira:IPG-929", LINKS) == "jira:IPG-929"

def test_miss_returns_none():
    assert worktrees.resolve_worktree("jira:NOPE-1", LINKS) is None

def test_recorded_key_ignores_same_branch_in_other_repo(tmp_path):
    """Branch names collide across repos (IPG-1002 in projectx + ipg-commons)."""
    links = {
        "jira:IPG-1002": {"branch": "feat/IPG-1002--x", "worktree": "/tmp/wt-commons",
                          "repo": "/repos/ipg-commons"},
    }
    assert worktrees.recorded_key("wt", "feat/IPG-1002--x", links,
                                  repo="/repos/projectx") is None
    assert worktrees.recorded_key("wt", "feat/IPG-1002--x", links,
                                  repo="/repos/ipg-commons") == "jira:IPG-1002"


def test_recorded_key_legacy_row_without_repo_still_matches():
    links = {"jira:X-1": {"branch": "feat/X-1", "worktree": "/tmp/wt"}}
    assert worktrees.recorded_key("wt", "feat/X-1", links, repo="/repos/anything") == "jira:X-1"


def test_invalid_path_is_false(tmp_path):
    assert worktrees.is_valid_worktree(str(tmp_path / "gone")) is False


def test_recorded_key_path_match_requires_matching_branch():
    links = {
        "feat/old": {"branch": "feat/old", "worktree": "/tmp/wt-shared", "repo": "/tmp/repo"}
    }
    # Different branch on the same path must not match feat/old
    assert worktrees.recorded_key("/tmp/wt-shared", "feat/new", links, repo="/tmp/repo") is None
    # Matching branch on the same path matches
    assert worktrees.recorded_key("/tmp/wt-shared", "feat/old", links, repo="/tmp/repo") == "feat/old"
    # Empty branch matches path fallback
    assert worktrees.recorded_key("/tmp/wt-shared", "", links, repo="/tmp/repo") == "feat/old"


def test_resolve_by_pr_url_matches_branch_keyed_row():
    """Issue #188: review-created row keyed by branch slug resolves by pr_url."""
    mr_url = "https://git.jibit.cloud/server/projectx/-/merge_requests/1824"
    links = {
        "ao/terminal-priority/ipg-1076": {
            "branch": "ao/terminal-priority/ipg-1076",
            "worktree": "/tmp/wt-px6",
            "repo": "/tmp/projectx",
            "pr_url": mr_url,
            "issue_url": "",
        },
        "other-1824-branch": {
            "branch": "feat/1824-unrelated",
            "worktree": "/tmp/wt-other",
            "repo": "/tmp/other",
        },
    }
    assert worktrees.resolve_worktree(mr_url, links) == "ao/terminal-priority/ipg-1076"
    assert worktrees.resolve_worktree(f"{mr_url}/", links) == "ao/terminal-priority/ipg-1076"


def test_resolve_empty_ref_returns_none():
    """Issue #188: empty/whitespace ref must never match every worktree."""
    assert worktrees.resolve_worktree("", LINKS) is None
    assert worktrees.resolve_worktree("   ", LINKS) is None


def test_pick_worktree_rejects_empty_ref():
    """Issue #188: pick_worktree rejects empty ref instead of reporting ambiguous ''."""
    import pytest
    import typer
    with pytest.raises(typer.Exit) as exc:
        worktrees.pick_worktree("", list(LINKS), LINKS)
    assert exc.value.exit_code == 2
