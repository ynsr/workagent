"""Merge capability cache tests (Task 3 RED)."""
from __future__ import annotations

import pytest

@pytest.fixture(autouse=True)
def _clear_caps_cache():
    from workagent import release_caps as _rc
    _rc._CACHE.clear()
    yield
    _rc._CACHE.clear()


def test_gitlab_ff_strategy(monkeypatch):
    from workagent import release_caps
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: "ff")
    caps = release_caps.get_caps("glab", "/r", "main")
    assert caps["strategy"] == ["gitlab-mr-ff"]


def test_gitlab_nonff_strategy(monkeypatch):
    from workagent import release_caps
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: "merge")
    assert release_caps.get_caps("glab", "/r", "main")["strategy"] == ["ff-push"]


def test_github_strategy_order(monkeypatch):
    from workagent import release_caps
    monkeypatch.setattr(release_caps, "_gh_caps", lambda *a: {"allow_merge_commit": True})
    assert release_caps.get_caps("gh", "/r", "main")["strategy"] == ["ff-push", "github-pr-merge-commit"]


def test_second_call_cached(monkeypatch):
    from workagent import release_caps
    calls = []
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: calls.append(1) or "ff")
    release_caps.get_caps("glab", "/r", "main")
    release_caps.get_caps("glab", "/r", "main")
    assert calls == [1]


def test_invalidate_forces_refetch(monkeypatch):
    from workagent import release_caps
    calls = []
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: calls.append(1) or "ff")
    release_caps.get_caps("glab", "/r", "main")
    release_caps.invalidate("glab", "/r", "main")
    release_caps.get_caps("glab", "/r", "main")
    assert calls == [1, 1]


def test_auth_failure_not_cached(monkeypatch):
    from workagent import release_caps
    from workagent.errors import HarnessError

    def boom(*a):
        raise HarnessError("401 Unauthorized")

    monkeypatch.setattr(release_caps, "_glab_merge_method", boom)
    with pytest.raises(HarnessError):
        release_caps.get_caps("glab", "/r", "main")
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: "ff")
    assert release_caps.get_caps("glab", "/r", "main")["strategy"] == ["gitlab-mr-ff"]
