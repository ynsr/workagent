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
def test_fetch_ci_status_branch_param():
    import inspect
    from workagent import refs as _refs
    assert "branch" in inspect.signature(_refs.fetch_ci_status).parameters
def test_fetch_branch_ci_parses_remote(monkeypatch):
    from workagent import refs_ci as _ci
    calls = []
    def _fake(*a, **k):
        calls.append(a)
        return '[{"status":"success","id":7,"web_url":"https://h/-/pipelines/7"}]'
    monkeypatch.setattr(_ci, "run_cmd", _fake)
    st, url = _ci.fetch_branch_ci("glab", "https://git.jibit.cloud/server/projectx.git",
                                  "master")
    assert st == "success"
    assert any("projects/server%2Fprojectx/pipelines" in " ".join(c) for c in calls)
    assert any("ref=master" in " ".join(c) for c in calls)


def test_fetch_branch_ci_gh_remote(monkeypatch):
    from workagent import refs_ci as _ci
    seen = {}
    def _fake(*a, **k):
        seen["argv"] = a
        return '[{"conclusion":"SUCCESS","status":"COMPLETED","url":"https://gh/run/1"}]'
    monkeypatch.setattr(_ci, "run_cmd", _fake)
    st, url = _ci.fetch_branch_ci("gh", "git@github.com:o/r.git", "master")
    assert st == "success"
    assert "--repo" in seen["argv"] and "o/r" in seen["argv"]
def test_repo_update_roundtrip(isolated_config, tmp_path):
    from typer.testing import CliRunner
    from workagent import cli as _cli
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = CliRunner().invoke(_cli.app, ["repo", "add", "--name", "proj",
                                      "--path", str(repo_dir), "--tracker", "IPG",
                                      "--release-branch", "main"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(_cli.app, ["repo", "list", "--json"])
    import json
    rows = json.loads(r.stdout)
    assert rows[0]["release_branch"] == "main"
    r = CliRunner().invoke(_cli.app, ["repo", "update", "proj",
                                      "--release-branch", "", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["release_branch"] == ""


def test_release_status_same_branch_guard(monkeypatch):
    from workagent import repos as _repos
    monkeypatch.setattr(_repos.store, "load_repos", lambda: {
        "rx": {"path": "/r", "default_branch": "main", "release_branch": "main",
               "tool": "", "remote": ""}})
    st = _repos.release_status("rx")
    assert st["releasable"] is False
    assert "same" in st["reason"]
