"""Cleanup independence: parent teardown never touches child worktrees."""
from __future__ import annotations

from tests.cli_helpers import runner
from workagent import cli, store


def _seeded(tmp_path, monkeypatch, parent="jira:IPG-1@main", child="jira:IPG-1@other"):
    parent_wt = tmp_path / "wt-main"
    parent_wt.mkdir(exist_ok=True)
    child_wt = tmp_path / "wt-other"
    child_wt.mkdir(exist_ok=True)
    store.record_link(parent, {"issue": "IPG-1", "worktree": str(parent_wt),
                               "branch": "feat/x", "repo": str(tmp_path / "main"),
                               "related_repos": [str(tmp_path / "other")]})
    store.record_link(child, {"issue": "IPG-1", "worktree": str(child_wt),
                              "branch": "feat/x", "repo": str(tmp_path / "other"),
                              "parent_key_ref": parent})
    monkeypatch.setattr(cli.cli_cleanup, "_pr_conflicted", lambda fresh: False)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "closed"}})
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"status": "removed"})
    monkeypatch.setattr(cli, "run_cmd", lambda *a, **k: "")
    from workagent import worktrees as _wt
    monkeypatch.setattr(_wt, "is_valid_worktree", lambda wt: True)
    return parent_wt, child_wt


def test_cleanup_parent_leaves_children(isolated_config, tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--yes",
                                "--json", "--hard-delete"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert "children" not in out
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" not in links
    assert "jira:IPG-1@other" in links


def test_cleanup_merged_cleans_parent_and_child_independently(
        isolated_config, tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "--merged", "--yes", "--json",
                                "--hard-delete"])
    assert r.exit_code == 0, r.output
    import json
    rows = {row["key"]: row for row in json.loads(r.stdout)["results"]}
    assert rows["jira:IPG-1@main"]["status"] == "cleaned"
    assert rows["jira:IPG-1@other"]["status"] == "cleaned"
    assert "skipped:cleaned-with-parent" not in {
        row["status"] for row in rows.values()}


def test_cleanup_child_direct_leaves_parent(isolated_config, tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@other", "--yes",
                                "--json", "--hard-delete"])
    assert r.exit_code == 0, r.output
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" in links
    assert "jira:IPG-1@other" not in links


def test_cleanup_dry_run_does_not_list_children(isolated_config, tmp_path,
                                                monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert "children" not in out
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" in links and "jira:IPG-1@other" in links


def test_cleanup_soft_delete_marks_parent_only(isolated_config, tmp_path,
                                               monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--yes", "--json"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert "children" not in out
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert links["jira:IPG-1@main"]["deleted_at"]
    assert not links["jira:IPG-1@other"].get("deleted_at")


def test_cleanup_parent_with_live_harness_on_child(isolated_config, tmp_path,
                                                   monkeypatch):
    _seeded(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 1}
                        if key == "jira:IPG-1@other" else None)
    import json
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--yes",
                                "--json", "--hard-delete"])
    assert r.exit_code == 0, r.output
    assert "children" not in json.loads(r.stdout)
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" not in links
    assert "jira:IPG-1@other" in links
    r = runner.invoke(cli.app, ["cleanup", "--merged", "--yes", "--json",
                                "--hard-delete"])
    assert r.exit_code == 0, r.output
    rows = {row["key"]: row for row in json.loads(r.stdout)["results"]}
    assert rows["jira:IPG-1@other"]["status"] == "skipped:live-harness"
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@other" in links
