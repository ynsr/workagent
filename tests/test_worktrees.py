"""Triple-key worktree resolution."""
from harness import worktrees

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

def test_invalid_path_is_false(tmp_path):
    assert worktrees.is_valid_worktree(str(tmp_path / "gone")) is False
