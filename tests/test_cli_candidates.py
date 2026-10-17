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
    assert "Recent issues (assigned/created by me, last 90 days)" in r.stdout
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
    assert "Recent issues (assigned/created by me, last 90 days)" in r.stdout
    assert "jira:IPG-981" in r.stdout
    # Titles stay raw for machine-readable output (no Rich escaping).
    assert "Add [beta] flag" in r.stdout
    assert "\\[beta]" not in r.stdout

def test_candidates_issues_scope_flags(isolated_config, tmp_path, monkeypatch):
    """--issues-days/--issues-scope requery with the selection; bad scope is a usage error."""
    from workagent import trackers as tr
    seen = {}

    def fake(warnings=None, force=False, days=90, mine=True):
        seen.update(force=force, days=days, mine=mine)
        return [{"key": "jira:IPG-1", "title": "T", "url": "u",
                 "status": "Done", "created": recent_created(200)}]

    monkeypatch.setattr(tr, "list_my_issues", fake)
    from workagent import cli_candidates as _cc
    monkeypatch.setattr(_cc, "_candidate_prs", lambda: ([], []))
    monkeypatch.setattr(_cc, "_scan_worktrees", lambda: [])
    r = _invoke("candidates", "--json", "--issues-days", "365", "--issues-scope", "all")
    assert r.exit_code == 0, r.output
    assert seen == {"force": False, "days": 365, "mine": False}
    # A 200-day-old Done issue is now served (was: 30-day cutoff dropped it).
    assert json.loads(r.stdout)["issues"][0]["key"] == "jira:IPG-1"
    r = _invoke("candidates", "--issues-scope", "bogus")
    assert r.exit_code == 2


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
    assert all(r["mtime"] for r in found)
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


def test_pr_candidates_serve_stale_without_scan(isolated_config, tmp_path, monkeypatch):
    """Expired PR cache serves stale rows instantly with a background
    rescan — the Unlinked PR/MRs tab never blocks on gh/glab."""
    import time
    from datetime import datetime, timedelta, timezone
    from workagent import cli_candidates as cc
    from workagent import store_sqlite as sq
    monkeypatch.setattr(sq, "db_path", lambda: tmp_path / "state.db")
    sq.set_pr_candidates_cache(sq.db_path(), [{"key": "github:o/r#1"}])
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with sq.connect(sq.db_path()) as conn:
        conn.execute("UPDATE pr_candidates_cache SET fetched_at = ? WHERE source = ?",
                     (old, "prs"))
    def boom(tool, cwd=None):
        time.sleep(0.2)
        return []
    monkeypatch.setattr(cc.refs, "fetch_open_prs", boom)
    t0 = time.monotonic()
    prs, warnings, stale = cc._candidate_prs_cached()
    assert time.monotonic() - t0 < 0.2  # instant, no wait for the rescan
    assert stale is True and prs == [{"key": "github:o/r#1"}]
    assert any("background" in w for w in warnings)


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


def test_candidate_prs_drops_linked_variants_and_release_mrs(isolated_config, tmp_path, monkeypatch):
    """Slash-variant linked URLs, linked worktree paths, and default->release
    MRs are dropped; surviving rows carry the registered repo_name."""
    from workagent import cli_candidates as cc
    from tests.cli_helpers import _init_repo
    repo = _init_repo(tmp_path / "proj")
    wt = repo.parent / "wt-feat"
    import subprocess as _sp
    _sp.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b",
             "feat/b", str(wt)], check=True, capture_output=True)
    cfg = store.load_config()
    cfg["repos"] = {"proj": {"path": str(repo), "default_branch": "main",
                             "release_branch": "release"}}
    empty = tmp_path / "empty-scan"
    empty.mkdir()
    cfg["scan_root"] = str(empty)
    store.save_config(cfg)
    monkeypatch.setattr(cli, "_repo_tool", lambda path: "gh")
    monkeypatch.setattr(cli.refs, "fetch_open_prs", lambda tool, cwd: [
        {"number": 1, "title": "linked slash", "branch": "feat/a",
         "updated": "2026-09-20T10:00:00Z",
         "url": "https://github.com/o/r/pull/1", "state": "OPEN"},
        {"number": 2, "title": "linked path", "branch": "feat/b",
         "updated": "2026-09-21T10:00:00Z",
         "url": "https://github.com/o/r/pull/2", "state": "OPEN"},
        {"number": 3, "title": "release MR", "branch": "main",
         "updated": "2026-09-22T10:00:00Z",
         "url": "https://github.com/o/r/pull/3", "state": "OPEN",
         "target_branch": "release"},
        {"number": 4, "title": "real one", "branch": "feat/c",
         "updated": "2026-09-23T10:00:00Z",
         "url": "https://github.com/o/r/pull/4", "state": "OPEN"},
    ])
    store.record_link("github:o/r#1",
                      {"pr_url": "https://github.com/o/r/pull/1/"})
    # Path-only link (no branch recorded): consulting linked_paths means
    # resolving the worktree's live branch, so the feat/b PR is dropped.
    store.record_link("branch:feat/b", {"worktree": str(wt)})
    prs, _warnings = cc._candidate_prs()
    assert [p["number"] for p in prs] == [4], (
        "slash-variant URLs, linked paths, and default->release MRs must be filtered")
    assert prs[0].get("repo_name") == "proj"
