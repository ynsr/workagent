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
    monkeypatch.setattr(worktrees, "_sleep_for_pr_retry", lambda secs: None)
    monkeypatch.setattr(store, "record_link",
                        lambda k, rec: recorded.update({k: rec}))
    url = worktrees.worktree_pr_url("k", entry)
    assert url == "https://github.com/o/r/pull/56"
    assert len(calls) == 2
    assert recorded["k"]["pr_url"] == "https://github.com/o/r/pull/56"


def test_live_lookup_persistent_transient_failure_stops_after_three(
        monkeypatch, tmp_path, capsys):
    """Persistent transient failure: exactly 3 attempts (no infinite retry),
    then None with the stderr note."""
    entry = {"repo": str(tmp_path), "branch": "feat/x", "pr_url": ""}
    calls = []

    def always_transient(tool, branch, cwd=None, timeout=None):
        calls.append(branch)
        raise HarnessError("command timed out: gh pr list")

    monkeypatch.setattr(refs, "fetch_pr_list_for_branch", always_transient)
    monkeypatch.setattr(worktrees, "_repo_tool_for_pr_lookup", lambda repo: "gh")
    monkeypatch.setattr(worktrees, "_sleep_for_pr_retry", lambda secs: None)
    assert worktrees.worktree_pr_url("k", entry) is None
    assert len(calls) == 3
    assert "live PR lookup" in capsys.readouterr().err


def test_live_lookup_permanent_failure_no_retry(monkeypatch, tmp_path, capsys):
    """Permanent failure (bad auth): one attempt, no retry, then None."""
    entry = {"repo": str(tmp_path), "branch": "feat/x", "pr_url": ""}
    calls = []

    def always_permanent(tool, branch, cwd=None, timeout=None):
        calls.append(branch)
        raise HarnessError("gh pr list failed: auth")

    monkeypatch.setattr(refs, "fetch_pr_list_for_branch", always_permanent)
    monkeypatch.setattr(worktrees, "_repo_tool_for_pr_lookup", lambda repo: "gh")
    monkeypatch.setattr(worktrees, "_sleep_for_pr_retry", lambda secs: None)
    assert worktrees.worktree_pr_url("k", entry) is None
    assert len(calls) == 1
    assert "live PR lookup" in capsys.readouterr().err


def test_live_lookup_unexpected_crash_returns_none(monkeypatch, tmp_path, capsys):
    """Non-HarnessError crash (malformed payload): None, not propagated —
    review --all skips the worktree instead of crashing."""
    entry = {"repo": str(tmp_path), "branch": "feat/x", "pr_url": ""}

    def boom(tool, branch, cwd=None, timeout=None):
        raise ValueError("malformed gh payload")

    monkeypatch.setattr(refs, "fetch_pr_list_for_branch", boom)
    monkeypatch.setattr(worktrees, "_repo_tool_for_pr_lookup", lambda repo: "gh")
    assert worktrees.worktree_pr_url("k", entry) is None
    assert "live PR lookup" in capsys.readouterr().err
