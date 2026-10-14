import pytest

from workagent import cli_release as _rel
from workagent import store as _store


def _registry(monkeypatch):
    monkeypatch.setattr(_store, "load_repos", lambda: {"rx": {"path": "/r"}})


def _status(**kw):
    base = {"repo": "rx", "path": "/r", "default_branch": "main",
            "release_branch": "master", "diff_count": 3, "releasable": True,
            "reason": "", "stale": False}
    base.update(kw)
    return base


def test_release_no_branch_gate_exits_2(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.setattr(_rel.repos, "release_status",
                        lambda name, force=False: _status(release_branch="", releasable=False,
                                                          reason="no release branch"))
    with pytest.raises(Exception) as ei:
        _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    assert getattr(ei.value, "exit_code", 2) == 2
def test_release_same_branch_exits_2(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.setattr(_rel.repos, "release_status",
                        lambda name, force=False: _status(default_branch="main",
                                                          release_branch="main"))
    with pytest.raises(Exception) as ei:
        _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    assert getattr(ei.value, "exit_code", 2) == 2


def test_release_ff_argv_per_host(monkeypatch):
    _registry(monkeypatch)
    calls = []
    monkeypatch.setattr(_rel, "run_cmd", lambda *a, **k: calls.append(a) or "https://h/mr/1")
    monkeypatch.setattr(_rel.repos, "release_status",
                        lambda name, force=False: _status())
    monkeypatch.setattr(_rel, "_host_tool", lambda path: "glab")
    monkeypatch.setattr(_rel, "_existing_open_mr", lambda tool, path, d, r: "")
    monkeypatch.setattr(_rel, "_merge_state", lambda url, path: {"state": "merged"})
    _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    flat = " ".join(" ".join(c) for c in calls)
    assert "--ff-only" in flat
    assert "--squash" not in flat
    assert "--auto-merge" in flat


def test_release_dry_run_runs_nothing(monkeypatch):
    _registry(monkeypatch)
    def _boom(*a, **k):
        raise AssertionError("must not run")
    monkeypatch.setattr(_rel, "run_cmd", _boom)
    monkeypatch.setattr(_rel.repos, "release_status",
                        lambda name, force=False: _status(diff_count=2))
    _rel.run_release("rx", yes=True, dry_run=True, json_output=False)
