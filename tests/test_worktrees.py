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
