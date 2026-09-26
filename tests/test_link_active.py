"""link deactivate/reactivate/remove + deactivated bulk-skip tests."""

from __future__ import annotations

from workagent import store

from tests.cli_helpers import _invoke, _link_session


def test_link_deactivate_reactivate_round_trip(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    _link_session(repo_dir, wt_dir, monkeypatch)
    r = _invoke("link", "deactivate", "jira:IPG-929")
    assert r.exit_code == 0, r.output
    assert "jira:IPG-929" not in store.load_links()
    assert store.load_links(include_inactive=True)["jira:IPG-929"]["active"] == 0
    r = _invoke("link", "reactivate", "jira:IPG-929")
    assert r.exit_code == 0, r.output
    assert store.load_links()["jira:IPG-929"]["active"] == 1


def test_link_deactivate_resolves_branch(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    _link_session(repo_dir, wt_dir, monkeypatch)
    r = _invoke("link", "deactivate", "feat/IPG-929--x")
    assert r.exit_code == 0, r.output
    assert "jira:IPG-929" not in store.load_links()


def test_link_remove_drops_row_only(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    _link_session(repo_dir, wt_dir, monkeypatch)
    r = _invoke("link", "remove", "jira:IPG-929")
    assert r.exit_code == 0, r.output
    assert wt_dir.exists()
    assert "jira:IPG-929" not in store.load_links(include_inactive=True)


def test_sync_all_skips_deactivated(isolated_config, tmp_path, monkeypatch):
    from workagent import cli
    repo_dir = tmp_path / "proj"
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    _link_session(repo_dir, wt_dir, monkeypatch)
    import json as _j0
    _pre = _invoke("link", "deactivate", "jira:IPG-929")
    assert _pre.exit_code == 0, _pre.output
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    r = _invoke("sync", "--all", "--yes", "--json")
    assert r.exit_code == 0, r.output
    import json as _j
    end = r.output.rindex(chr(10)+']') + 2
    assert _j.loads(r.output[:end])[0]["status"] == "skipped:deactivated"
