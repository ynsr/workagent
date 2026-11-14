"""Issue #60: live PR lookup retry (transient gh/proxy blip swallowed → bogus "no recorded PR/MR")."""
from workagent import refs, store, worktrees
from workagent.errors import HarnessError


def test_live_lookup_retries_transient_failure(monkeypatch, tmp_path):
    """Failing-before: one transient gh failure → None (review then fails
    "no recorded PR/MR" even though the PR exists). Passing-after: retry
    succeeds and stamps pr_url."""
    entry = {"repo": str(tmp_path), "branch": "feat/x", "pr_url": ""}
    calls = []

    def flaky(tool, branch, cwd=None, timeout=None):
        calls.append(branch)
        if len(calls) == 1:
            raise HarnessError("command timed out: gh pr list")
        return [{"number": 56, "state": "open",
                 "url": "https://github.com/o/r/pull/56"}]
    monkeypatch.setattr(refs, "fetch_pr_list_for_branch", flaky)
    recorded = {}
    monkeypatch.setattr(worktrees, "_repo_tool_for_pr_lookup", lambda repo: "gh")
    monkeypatch.setattr(store, "record_link",
                        lambda k, rec: recorded.update({k: rec}))
    url = worktrees.worktree_pr_url("k", entry)
    assert url == "https://github.com/o/r/pull/56"
    assert len(calls) == 2
    assert recorded["k"]["pr_url"] == "https://github.com/o/r/pull/56"


def test_live_lookup_persistent_failure_returns_none(monkeypatch, tmp_path):
    """Genuinely PR-less worktrees still resolve to None (no infinite retry)."""
    entry = {"repo": str(tmp_path), "branch": "feat/x", "pr_url": ""}

    def always_fail(tool, branch, cwd=None, timeout=None):
        raise HarnessError("gh pr list failed: auth")

    monkeypatch.setattr(refs, "fetch_pr_list_for_branch", always_fail)
    monkeypatch.setattr(worktrees, "_repo_tool_for_pr_lookup", lambda repo: "gh")
    assert worktrees.worktree_pr_url("k", entry) is None
