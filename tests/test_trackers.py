"""Trackers tests: PR-aware default repo resolution for Review prefill."""

from __future__ import annotations



from workagent import refs, store, trackers


def test_default_repo_for_pr_url_resolves_head_worktree(isolated_config, tmp_path, monkeypatch):
    """Branch name without the PR number: only a head_ref fetch can find it."""
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("branch:feat/add-login",
                      {"branch": "feat/add-login", "worktree": str(wt),
                       "repo": "/repo/proj"})
    monkeypatch.setattr(refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/add-login"})
    assert trackers.default_repo_for_ref("https://github.com/o/r/pull/33") == "/repo/proj"


def test_default_repo_for_pr_url_fetch_failure_falls_through(isolated_config, monkeypatch):
    def _boom(parsed, cwd=None):
        raise Exception("no auth")
    monkeypatch.setattr(refs, "fetch_pr_info", _boom)
    assert trackers.default_repo_for_ref("https://github.com/o/r/pull/33") == ""
