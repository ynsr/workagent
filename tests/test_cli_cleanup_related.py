"""Cleanup cascade: parent teardown also cleans its child worktrees."""
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


def test_cleanup_parent_cascades_to_children(isolated_config, tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--yes",
                                "--json", "--hard-delete"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert sorted(out["children"]) == ["jira:IPG-1@other"]
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" not in links
    assert "jira:IPG-1@other" not in links


def test_cleanup_child_direct_leaves_parent(isolated_config, tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@other", "--yes",
                                "--json", "--hard-delete"])
    assert r.exit_code == 0, r.output
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" in links
    assert "jira:IPG-1@other" not in links


def test_cleanup_dry_run_lists_children_without_teardown(isolated_config, tmp_path,
                                                         monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert out["children"] == ["jira:IPG-1@other"]
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" in links and "jira:IPG-1@other" in links


def test_cleanup_soft_delete_marks_children_deleted(isolated_config, tmp_path,
                                                    monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--yes", "--json"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert out["children"] == ["jira:IPG-1@other"]
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert links["jira:IPG-1@main"]["deleted_at"]
    assert links["jira:IPG-1@other"]["deleted_at"]


def test_cleanup_merged_skips_children_of_cleaned_parents(isolated_config, tmp_path,
                                                          monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "--merged", "--yes", "--json",
                                "--hard-delete"])
    assert r.exit_code == 0, r.output
    import json
    rows = {row["key"]: row for row in json.loads(r.stdout)["results"]}
    assert rows["jira:IPG-1@main"]["status"] == "cleaned"
    assert rows["jira:IPG-1@other"]["status"] == "skipped:cleaned-with-parent"
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" not in links
    assert "jira:IPG-1@other" not in links
