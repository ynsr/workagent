"""CLI candidates tests (split from test_cli.py)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import recent_created, runner, _invoke, _candidates_env

def test_candidates_cli_json(isolated_config, tmp_path, monkeypatch):
    _candidates_env(monkeypatch, tmp_path, issues=[
        {"key": "jira:IPG-981", "title": "T", "url": "u",
         "status": "To Do", "created": recent_created(1)}])
    r = _invoke("candidates", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert set(out) == {"prs", "issues", "worktrees"}
    assert out["prs"][0]["key"] == "github:o/r#1"
    assert out["prs"][0]["repo"] == "o/r"
    assert out["issues"][0]["key"] == "jira:IPG-981"
    assert out["worktrees"] == []


def test_candidates_cli_tables(isolated_config, tmp_path, monkeypatch):
    _candidates_env(monkeypatch, tmp_path, issues=[
        {"key": "jira:IPG-981", "title": "T", "url": "u",
         "status": "To Do", "created": recent_created(1)}])
    r = _invoke("candidates")
    assert r.exit_code == 0, r.output
    assert "Unlinked PR/MRs" in r.stdout
    assert "Recent issues (reported by me, last 7 days)" in r.stdout
    assert "(no unregistered worktrees)" in r.stdout
    # Title user content is Rich-escaped in the table path: the literal
    # "[beta]" must survive rendering (unescaped markup would be eaten).
    assert "Add [beta] flag" in r.stdout


def test_candidates_cli_csv_pulls_only(isolated_config, tmp_path, monkeypatch):
    _candidates_env(monkeypatch, tmp_path, issues=[
        {"key": "jira:IPG-981", "title": "T", "url": "u",
         "status": "To Do", "created": recent_created(1)}])
    r = _invoke("candidates", "--csv")
    assert r.exit_code == 0, r.output
    assert r.stdout.splitlines()[0] == "url,title,repo,updated"
    # CSV applies to the PR/MR table; the issues table stays a Rich table.
    assert "Recent issues (reported by me, last 7 days)" in r.stdout
    assert "jira:IPG-981" in r.stdout
    # Titles stay raw for machine-readable output (no Rich escaping).
    assert "Add [beta] flag" in r.stdout
    assert "\\[beta]" not in r.stdout


def test_candidates_cli_linked_pr_excluded(isolated_config, tmp_path,
                                           monkeypatch):
    _candidates_env(monkeypatch, tmp_path)
    store.record_link("github:o/r#1",
                      {"pr_url": "https://github.com/o/r/pull/1"})
    r = _invoke("candidates", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["prs"] == []


def test_candidates_cli_branch_matched_pr_excluded(isolated_config, tmp_path,
                                                   monkeypatch):
    _candidates_env(monkeypatch, tmp_path)
    store.record_link("branch:feat/1", {"branch": "feat/1",
                                        "worktree": str(tmp_path / "wt")})
    r = _invoke("candidates", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["prs"] == []


def test_candidates_cli_linked_issue_excluded(isolated_config, tmp_path,
                                              monkeypatch):
    _candidates_env(monkeypatch, tmp_path, issues=[
        {"key": "jira:IPG-981", "title": "T",
         "url": "https://jira.example/browse/IPG-981",
         "status": "To Do", "created": recent_created(1)}])
    store.record_link("jira:IPG-981",
                      {"branch": "IPG-981-slug",
                       "worktree": str(tmp_path / "wt"),
                       "issue_url": "https://jira.example/browse/IPG-981"})
    r = _invoke("candidates", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["issues"] == []


def test_candidates_cli_issue_carries_repo_hint(isolated_config, tmp_path,
                                                monkeypatch):
    _candidates_env(monkeypatch, tmp_path, issues=[
        {"key": "jira:IPG-981", "title": "T", "url": "u",
         "status": "To Do", "created": recent_created(1)}])
    repo = tmp_path / "proj"
    store.record_link("jira:IPG-1", {"worktree": str(tmp_path / "wt"),
                                     "branch": "IPG-1-x",
                                     "repo": str(repo)})
    monkeypatch.setattr(
        cli.trackers, "default_repo_for_ref",
        lambda ref: str(repo) if ref == "jira:IPG-981" else "")
    r = _invoke("candidates", "--json")
    assert r.exit_code == 0, r.output
    issues = json.loads(r.stdout)["issues"]
    assert issues[0]["repo_hint"] == str(repo)


def test_candidates_cli_warning_to_stderr(isolated_config, tmp_path,
                                          monkeypatch):
    repo = tmp_path / "proj"
    repo.mkdir()
    cfg = store.load_config()
    cfg["repos"] = {"proj": {"path": str(repo)}}
    empty = tmp_path / "empty-scan"
    empty.mkdir()
    store.save_config(cfg)
    monkeypatch.setattr(cli, "_repo_tool", lambda path: "gh")

    def boom(tool, cwd):
        raise HarnessError("gh pr list failed: no auth")

    monkeypatch.setattr(cli.refs, "fetch_open_prs", boom)
    monkeypatch.setattr(cli.trackers, "list_my_issues", lambda *a, **k: [])
    r = _invoke("candidates")
    assert r.exit_code == 0, r.output
    assert "warning:" in r.stderr and "proj" in r.stderr


def test_scan_worktrees_both_patterns(isolated_config, tmp_path, monkeypatch):
    """Pattern 1 <repo>/<branch> and pattern 2 <repo>/<type>/<branch> are
    both detected; plain dirs and linked paths are excluded."""
    root = tmp_path / "worktrees"
    flat = root / "proj" / "feat-IPG-1--x"
    nested = root / "proj" / "feat" / "IPG-2--y"
    plain = root / "proj" / "notes"
    for d in (flat, nested, plain):
        d.mkdir(parents=True)
    branches = {str(flat): "feat/IPG-1--x", str(nested): "feat/IPG-2--y"}
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree",
                        lambda p: str(p) in branches)
    monkeypatch.setattr(cli, "run_cmd",
                        lambda *a, **k: branches.get(a[2], ""))
    cfg = store.load_config()
    cfg["scan_root"] = str(root)
    store.save_config(cfg)
    found = cli._scan_worktrees()
    assert [(r["branch"], r["repo"]) for r in found] == [
        ("feat/IPG-1--x", "proj"), ("feat/IPG-2--y", "proj")]
    assert found[0]["key_guess"] == "jira:IPG-1--x"
    # Linked paths are excluded on the next scan.
    store.record_link("jira:IPG-1", {"worktree": str(flat),
                                     "branch": "feat/IPG-1--x"})
    assert [r["branch"] for r in cli._scan_worktrees()] == ["feat/IPG-2--y"]


def test_scan_worktrees_missing_root(isolated_config, tmp_path):
    cfg = store.load_config()
    cfg["scan_root"] = str(tmp_path / "nope")
    store.save_config(cfg)
    assert cli._scan_worktrees() == []


def test_candidates_reset_cache_clears(isolated_config, monkeypatch):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira", [{"key": "IPG-1"}])
    monkeypatch.setattr(cli.trackers, "list_my_issues", lambda *a, **k: [])
    monkeypatch.setattr(cli.refs, "fetch_open_prs", lambda *a, **k: [])
    r = _invoke("candidates", "--reset-cache", "--json")
    assert sq.get_issue_cache(db, "jira") == ([], None)


def test_fetch_disabled_repo_skips_pr_scan(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    repo = tmp_path / "proj"
    repo.mkdir()
    sq.register_repo_row(db, "proj", str(repo), "github:o/r",
                         remote="https://github.com/o/r.git", tool="gh",
                         fetch_enabled=False)
    def _boom(tool, cwd):
        raise AssertionError("fetch-disabled repo must not be scanned")
    monkeypatch.setattr(cli, "_repo_tool", lambda path: "gh")
    monkeypatch.setattr(cli.refs, "fetch_open_prs", _boom)
    from workagent import cli_candidates as cc
    prs, _w = cc._candidate_prs()
    assert prs == []


def test_fetch_disabled_tracker_skips_github_issues(isolated_config, monkeypatch):
    from workagent import store_sqlite as sq
    from workagent import trackers as tr
    db = sq.db_path()
    sq.init_db(db)
    sq.upsert_tracker(db, "github:o/r", vendor="github",
                      remote_url="https://github.com/o/r", fetch_enabled=False)
    sq.upsert_tracker(db, "jira:IPG", vendor="jira",
                      remote_url="https://example.test/browse/IPG")
    monkeypatch.setattr(tr, "_jira_my_issues", lambda warn: [])
    def _boom(warn):
        raise AssertionError("fetch-disabled tracker must not be fetched")
    monkeypatch.setattr(tr, "_gh_my_issues", _boom)
    assert tr.list_my_issues([]) == []


def test_fetch_migration_defaults_github_off(isolated_config):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    with sq.connect(db) as conn:
        conn.executescript("CREATE TABLE trackers (key_ref TEXT PRIMARY KEY, vendor TEXT NOT NULL DEFAULT '', remote_url TEXT NOT NULL DEFAULT '');"
                           "CREATE TABLE repos (key_ref TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', remote TEXT NOT NULL DEFAULT '', tool TEXT);"
                           "INSERT INTO trackers (key_ref, vendor) VALUES ('github:o/r', 'github'), ('jira:IPG', 'jira');"
                           "INSERT INTO repos (key_ref, path, tool) VALUES ('a', '/a', 'gh'), ('b', '/b', 'glab');")
    sq.init_db(db)
    assert sq.load_trackers(db)["github:o/r"]["fetch_enabled"] is False
    assert sq.load_trackers(db)["jira:IPG"]["fetch_enabled"] is True
    assert sq.load_repos(db)["a"]["fetch_enabled"] is False
    assert sq.load_repos(db)["b"]["fetch_enabled"] is True
