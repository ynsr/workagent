"""CLI prodtest plan tests (Task 4)."""
from __future__ import annotations

import json

from workagent import cli

from tests.cli_helpers import runner, _invoke


def test_plan_dry_run_no_harness(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    links = {"jira:IPG-1": {"issue": "IPG-1", "branch": "feat/IPG-1-x",
                             "repo": str(repo_dir), "worktree": str(wt_dir),
                             "issue_url": "https://tribe.jibit.cloud/browse/IPG-1"}}
    monkeypatch.setattr(cli.store, "load_links", lambda **k: dict(links))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    r = runner.invoke(cli.app, ["prodtest", "plan", "IPG-1", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert "plan" in json.dumps(out).lower()


def test_plan_dry_run_update_detected(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    plan = wt_dir / ".workagent" / "prodtest" / "plans" / "IPG-1.yaml"
    plan.parent.mkdir(parents=True)
    plan.write_text("plan_version: 1\n")
    links = {"jira:IPG-1": {"issue": "IPG-1", "branch": "feat/IPG-1-x",
                             "repo": str(repo_dir), "worktree": str(wt_dir),
                             "issue_url": "https://tribe.jibit.cloud/browse/IPG-1"}}
    monkeypatch.setattr(cli.store, "load_links", lambda **k: dict(links))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    r = runner.invoke(cli.app, ["prodtest", "plan", "IPG-1", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["update"] is True
