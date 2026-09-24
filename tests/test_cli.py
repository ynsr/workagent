"""CLI-level tests via typer.testing.CliRunner (subprocesses stubbed out)."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from typer.testing import CliRunner

from workagent import cli, store

runner = CliRunner()


def _invoke(*args):
    return runner.invoke(cli.app, list(args))


def test_repo_add_list_remove(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = _invoke("repo", "add", "--name", "proj", "--path", str(repo_dir),
                "--tracker", "IPG", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["registered"] == "proj"
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)[0]["name"] == "proj"
    r = _invoke("repo", "remove", "proj", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout) == {"removed": "proj"}


def test_start_dry_run(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add login", "body": "Details here"})
    r = _invoke("start", "o/r#22", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert out["repo"] == str(repo_dir)
    assert out["issue"] == {"title": "Add login"}
    # Dry run must not record links.
    assert store.load_links() == {}

def test_review_dry_run(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": ""})
    r = _invoke("review", "https://github.com/o/r/pull/33", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["dry_run"] is True and out["pr_url"].endswith("/pull/33")

def test_cleanup_missing_link_errors(isolated_config):
    r = _invoke("cleanup", "o/r#99", "--json")
    assert r.exit_code == 2
    assert "no linked state" in r.output


def test_cleanup_dry_run(isolated_config):
    store.record_link("github:o/r#22", {"branch": "feat/22-x", "repo": "/tmp/proj",
                                        "worktree": "/tmp/wt"})
    r = _invoke("cleanup", "o/r#22", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["dry_run"] is True and out["branch"] == "feat/22-x"


def test_cleanup_resolves_by_branch(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-929", {"branch": "feat/IPG-929--x", "repo": "/tmp/proj",
                                       "worktree": "/tmp/wt"})
    r = runner.invoke(cli.app, ["cleanup", "feat/IPG-929--x", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["branch"] == "feat/IPG-929--x"
    # Wording gate: resolve/pick errors say "worktree", never "session".
    store.record_link("pr:https://git.example.com/x/-/merge_requests/929",
                      {"branch": "feat/IPG-929--pr", "repo": "/tmp/proj",
                       "worktree": "/tmp/wt-pr"})
    r2 = runner.invoke(cli.app, ["cleanup", "IPG-929", "--dry-run", "--json"])
    assert r2.exit_code == 2
    assert "exact worktree key" in r2.output
    assert "session" not in r2.output
    r3 = _invoke("cleanup", "NOPE-404")
    assert r3.exit_code == 2
    assert "linked worktrees" in r3.output


def test_status_empty(isolated_config):
    r = _invoke("status", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout) == {}


def _link_session(repo_dir, wt_dir, monkeypatch):
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    store.record_link("jira:IPG-929", {"issue": "IPG-929", "worktree": str(wt_dir),
                                       "branch": "feat/IPG-929--x", "repo": str(repo_dir)})


def test_status_table_shows_behind_ahead(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind",
                        lambda wt, db: {"behind": 5, "ahead": 8})
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("status", "--json")
    data = json.loads(r.stdout)
    assert data["jira:IPG-929"]["commits"] == "5|8"
    assert data["jira:IPG-929"]["pr"] == "-"


def test_status_pr_from_cache(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 123, "state": "merged",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/123",
                                              "target_branch": "main"})
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    r = _invoke("status", "--json")
    data = json.loads(r.stdout)
    assert data["jira:IPG-929"]["pr"] == "PR #123 (merged)"




def test_status_pr_live_query_then_cache(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    calls = []
    def fake_list(tool, branch, cwd=None):
        calls.append((tool, branch))
        return [{"number": 77, "state": "open", "title": "T", "author": "a",
                 "created_at": "2026-09-15", "url": "https://x/mr/77",
                 "target_branch": "main"}]
    monkeypatch.setattr(cli.refs, "fetch_pr_list_for_branch", fake_list)
    r = _invoke("status", "--json")
    assert json.loads(r.stdout)["jira:IPG-929"]["pr"] == "PR #77 (open)"
    assert calls == [("glab", "feat/IPG-929--x")]
    assert store.get_cached_pr_status("feat/IPG-929--x")["number"] == 77


def test_status_cache_reused_until_tip_or_ttl(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    tips = {"HEAD": "a1", "origin/main": "b1"}
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: tips.get(ref))
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                          tool="glab", base_branch="main",
                          branch_tip="a1", base_tip="b1", behind=5, ahead=1)
    ab_calls = []
    monkeypatch.setattr(cli.repos, "ahead_behind",
                        lambda wt, db: ab_calls.append(db)
                        or {"behind": 9, "ahead": 9})
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:IPG-929"]["commits"] == "5|1"  # served from cache
    assert ab_calls == []
    # base branch gained commits → cache invalid → recompute counts
    tips["origin/main"] = "b2"
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:IPG-929"]["commits"] == "9|9"
    assert ab_calls == ["main"]
    # PR stays cached even on invalidation (lookup unavailable)
    assert data["jira:IPG-929"]["pr"] == "PR #9 (open)"


def test_status_cache_expired_by_ttl(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: "a1" if ref == "HEAD" else None)
    store.cache_pr_status("feat/IPG-929--x", None, tool=None, base_branch="main",
                          branch_tip="a1", base_tip=None, behind=5, ahead=1)
    cache = store.load_pr_cache()
    cache["feat/IPG-929--x"]["checked_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    store.save_pr_cache(cache)
    monkeypatch.setattr(cli.repos, "ahead_behind",
                        lambda wt, db: {"behind": 7, "ahead": 0})
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:IPG-929"]["commits"] == "7|0"


def test_status_refresh_pr_requeries(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    tips = {"HEAD": "a1", "origin/main": "b1"}
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: tips.get(ref))
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                          tool="glab", base_branch="main",
                          branch_tip="a1", base_tip="b1", behind=5, ahead=1)
    monkeypatch.setattr(cli.refs, "fetch_pr_list_for_branch",
                        lambda tool, branch, cwd=None: [
                            {"number": 44, "state": "merged", "title": "N",
                             "author": "a", "created_at": "2026-09-16",
                             "url": "https://x/mr/44", "target_branch": "main"}])
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:IPG-929"]["pr"] == "PR #9 (open)"  # valid cache, no re-query
    data = json.loads(_invoke("status", "--refresh-pr", "--json").stdout)
    assert data["jira:IPG-929"]["pr"] == "PR #44 (merged)"  # forced re-query
    assert store.get_cached_pr_status("feat/IPG-929--x")["number"] == 44
    assert store.get_cached_pr_tool("feat/IPG-929--x") == "glab"


def test_status_missing_worktree_gone(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    _link_session(repo_dir, tmp_path / "missing", monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("status", "--json")
    assert json.loads(r.stdout)["jira:IPG-929"]["commits"] == "gone"


def test_status_cells_recorded_pr_wins(isolated_config, tmp_path):
    # Cached negative (no PR found) must not hide the recorded pr_url.
    store.cache_pr_status("feat/x", None, tool=None, base_branch="main",
                          branch_tip="t", base_tip="b", behind=0, ahead=0)
    entry = {"worktree": str(tmp_path / "gone"), "branch": "feat/x",
             "repo": str(tmp_path / "repo"),
             "pr_url": "https://git.jibit.cloud/g/p/-/merge_requests/1706"}
    cells = cli._status_cells(entry)
    assert cells["pr"] == "MR #1706"
    assert cells["pr_data"]["url"].endswith("/merge_requests/1706")


def test_status_cells_cached_pr_beats_recorded(isolated_config, tmp_path):
    store.cache_pr_status("feat/x", {"number": 5, "state": "open",
                                     "url": "https://x/mr/5"},
                          base_branch="main")
    entry = {"worktree": str(tmp_path / "gone"), "branch": "feat/x",
             "repo": str(tmp_path / "repo"),
             "pr_url": "https://git.jibit.cloud/g/p/-/merge_requests/1706"}
    cells = cli._status_cells(entry)
    assert cells["pr"] == "PR #5 (open)"
    assert cells["pr_data"]["number"] == 5


def test_recorded_pr_parses_github_and_gitlab():
    gh = "https://github.com/o/r/pull/1706"
    gl = "https://git.jibit.cloud/g/p/-/merge_requests/1706"
    assert cli._recorded_pr(gh) == {"number": 1706, "state": "", "url": gh}
    assert cli._recorded_pr(gl) == {"number": 1706, "state": "", "url": gl}
    assert cli._fmt_pr(cli._recorded_pr(gh)) == "PR #1706"
    assert cli._fmt_pr(cli._recorded_pr(gl)) == "MR #1706"
    assert cli._recorded_pr("https://example.com/bogus") is None


def test_negative_cache_short_ttl():
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    cached = {"checked_at": old, "pr": None, "branch_tip": "t", "base_tip": "b"}
    assert cli._cache_fresh(cached) is False          # negative: 30min TTL
    cached_pos = {**cached, "pr": {"number": 1, "state": "OPEN"}}
    assert cli._cache_fresh(cached_pos) is True       # positive: 3d TTL
    day_old = {**cached, "checked_at": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(), "pr": {"number": 1, "state": "OPEN"}}
    assert cli._cache_fresh(day_old) is True          # 6h: expired under 3h, fresh under 3d
    stale_pos = {**cached, "checked_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(), "pr": {"number": 1, "state": "OPEN"}}
    assert cli._cache_fresh(stale_pos) is False       # positive: expired past 3d


def test_status_cells_partial_entry():
    cells = cli._status_cells({"worktree": "", "branch": "", "repo": ""})
    assert cells["pr"] == "-" and cells["commits"] == "-"


def test_status_cells_ci_cache_reuse(isolated_config, tmp_path, monkeypatch):
    wt_dir = tmp_path / "wt"; wt_dir.mkdir()
    entry = {"worktree": str(wt_dir), "branch": "feat/x", "repo": "/repo"}
    store.cache_pr_status("feat/x", {"number": 9, "state": "open",
                                     "url": "https://x/mr/9"},
                          tool="glab", base_branch="main",
                          branch_tip="a1", base_tip="b1", behind=0, ahead=0)
    tips = {"HEAD": "a1", "origin/main": "b1"}
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: tips.get(ref))
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    fetches = []
    monkeypatch.setattr(cli.refs, "fetch_ci_status",
                        lambda tool, url, cwd=None: fetches.append(url)
                        or "success")
    c1 = cli._status_cells(entry)
    assert c1["ci"] == "success"
    assert fetches == ["https://x/mr/9"]
    # same tip, fresh checked_at -> served from cache, no second fetch
    c2 = cli._status_cells(entry)
    assert c2["ci"] == "success"
    assert len(fetches) == 1
    cached = store.load_pr_cache()["feat/x"]
    assert cached["ci"] == "success" and cached["ci_sha"] == "a1"
    assert "ci_checked_at" in cached
    # branch tip moved -> refetch
    tips["HEAD"] = "a2"
    c3 = cli._status_cells(entry)
    assert c3["ci"] == "success"
    assert len(fetches) == 2
    assert store.load_pr_cache()["feat/x"]["ci_sha"] == "a2"
    # --refresh-pr forces a refetch even on an unchanged tip
    tips["HEAD"] = "a1"
    cli._status_cells(entry, refresh_pr=True)
    assert len(fetches) == 3


def test_status_cells_ci_reuse_stale_ttl(isolated_config, tmp_path,
                                         monkeypatch):
    wt_dir = tmp_path / "wt"; wt_dir.mkdir()
    entry = {"worktree": str(wt_dir), "branch": "feat/x", "repo": "/repo"}
    store.cache_pr_status("feat/x", {"number": 9, "state": "open",
                                     "url": "https://x/mr/9"},
                          tool="glab", base_branch="main",
                          branch_tip="a1", base_tip="b1", behind=0, ahead=0)
    store.cache_ci_status("feat/x", "success", "a1")
    cache = store.load_pr_cache()
    cache["feat/x"]["ci_checked_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    store.save_pr_cache(cache)
    monkeypatch.setattr(cli, "_git_tip",
                        lambda wt, ref: "a1" if ref == "HEAD" else None)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    fetches = []
    monkeypatch.setattr(cli.refs, "fetch_ci_status",
                        lambda tool, url, cwd=None: fetches.append(url)
                        or "failure")
    assert cli._status_cells(entry)["ci"] == "failure"
    assert len(fetches) == 1  # stale (11 min > 10 min TTL) -> refetched


def test_status_cells_ci_none_without_pr(isolated_config):
    cells = cli._status_cells({"worktree": "", "branch": "feat/x",
                               "repo": "/repo"})
    assert cells["ci"] is None


def test_status_ci_column_symbols_json_csv(isolated_config, tmp_path,
                                           monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    tips = {"HEAD": "a1", "origin/main": "b1"}
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: tips.get(ref))
    pr9 = [{"number": 9, "state": "open", "title": "T", "author": "a",
            "created_at": "2026-09-15", "url": "https://x/mr/9",
            "target_branch": "main"}]
    monkeypatch.setattr(cli.refs, "fetch_pr_list_for_branch",
                        lambda tool, branch, cwd=None:
                        [] if branch == "no-pr" else pr9)
    monkeypatch.setattr(cli.refs, "fetch_ci_status",
                        lambda tool, url, cwd=None: "failure")
    rows, columns = cli._session_rows(store.load_links(), False, False)
    assert columns.index("ci") == columns.index("pr") + 1
    assert rows[0]["ci"] == "failure"  # raw, machine-readable
    colored = cli._colorize_session(rows[0])
    assert "✗" in colored["ci"] and "bright_red" in colored["ci"]
    assert "failure" not in colored["ci"]
    # cache now has ci; JSON map carries it
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:IPG-929"]["ci"] == "failure"
    r = _invoke("status", "--csv")
    rows_csv = list(csv.reader(r.stdout.splitlines()))
    assert rows_csv[0][columns.index("ci")] == "ci"
    assert rows_csv[1][columns.index("ci")] == "failure"
    # no PR -> ci cell renders '-' in the table, empty in CSV
    links = {"other:x": {"branch": "no-pr", "repo": str(repo_dir),
                         "worktree": str(wt_dir)}}
    rows2, _ = cli._session_rows(links, False, False)
    assert cli._colorize_session(rows2[0])["ci"] == "-"
    assert rows2[0]["ci"] == ""


def test_status_json_detail_carries_ci(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: None)
    monkeypatch.setattr(cli.refs, "fetch_pr_list_for_branch",
                        lambda tool, branch, cwd=None: [
                            {"number": 9, "state": "open", "title": "T",
                             "author": "a", "created_at": "2026-09-15",
                             "url": "https://x/mr/9",
                             "target_branch": "main"}])
    monkeypatch.setattr(cli.refs, "fetch_ci_status",
                        lambda tool, url, cwd=None: "running")
    data = json.loads(_invoke("status", "IPG-929", "--json").stdout)
    assert data["ci"] == "running"
    enriched = json.loads(_invoke("status", "--json").stdout)
    assert enriched["jira:IPG-929"]["ci"] == "running"


def test_status_ref_detail(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind",
                        lambda wt, db: {"behind": 1, "ahead": 2})
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("status", "IPG-929", "--json")
    out = json.loads(r.stdout)
    assert out["commits"] == "1|2"
    assert out["commits_detail"]["ahead"] == 2
    assert out["worktree"] == str(wt_dir)




def test_status_detail_issue_url(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    monkeypatch.setattr(cli.refs, "jira_site", lambda: "https://jira.example.com")
    r = _invoke("status", "IPG-929", "--json")
    out = json.loads(r.stdout)
    assert out["issue_url"] == "https://jira.example.com/browse/IPG-929"
    r = _invoke("status", "IPG-929")
    assert "issue: https://jira.example.com/browse/IPG-929" in r.output


def test_link_list_worktrees_enriched(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("link", "list", "--json")
    data = json.loads(r.stdout)
    assert "sessions" not in data
    assert data["worktrees"]["jira:IPG-929"]["pr"] == "-"


def _sync_mocks(monkeypatch, local_calls=None, rebase_calls=None, pulled=None):
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    if local_calls is not None:
        monkeypatch.setattr(cli.sync_mod, "local_merge",
                            lambda wt, db: (local_calls.append((str(wt), db))
                                            or {"status": "merged", "conflicts": []}))
    if rebase_calls is not None:
        monkeypatch.setattr(cli.sync_mod, "rebase_remote",
                            lambda tool, pr, wt: rebase_calls.append((tool, pr["number"], str(wt))))
    monkeypatch.setattr(cli.sync_mod, "pull_rebased",
                        lambda wt, br: (pulled.append((str(wt), br))
                                        if pulled is not None else None) or "reset")
    monkeypatch.setattr(cli.sync_mod, "push", lambda wt, br: None)


def test_sync_unknown_ref(isolated_config):
    r = _invoke("sync", "nope-1")
    assert r.exit_code == 2


def test_sync_rebase_strategy_with_open_pr(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"})
    rebase, local, pulled = [], [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase,
                pulled=pulled)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out["strategy"] == "remote-rebase" and out["result"] == "rebased"
    assert out["pull"] == "reset"
    assert rebase == [("glab", 9, str(wt_dir))] and local == []
    assert pulled == [(str(wt_dir), "feat/IPG-929--x")]


def test_sync_merge_flag_uses_local_merge(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"})
    rebase, local = [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase)
    r = _invoke("sync", "IPG-929", "--merge", "--yes", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-merge" and out["result"] == "merged"
    assert local == [(str(wt_dir), "main")] and rebase == []


def test_sync_no_pr_falls_back_to_local(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    local = []
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda wt, db: (local.append((str(wt), db))
                                        or {"status": "merged", "conflicts": []}))
    monkeypatch.setattr(cli.sync_mod, "push", lambda wt, br: None)
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["strategy"] == "local-merge"
    assert local == [(str(wt_dir), "main")]


def test_sync_recorded_pr_seed_uses_local_merge(isolated_config, tmp_path,
                                                monkeypatch):
    """A recorded pr_url seed (state unknown) is display-only: sync must
    take the local-merge fallback, never remote-rebase an unverified URL."""
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    links = store.load_links()
    links["jira:IPG-929"]["pr_url"] = \
        "https://git.jibit.cloud/server/projectx/-/merge_requests/1699"
    store.save_links(links)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    rebase, local = [], []
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda wt, db: (local.append((str(wt), db))
                                        or {"status": "merged", "conflicts": []}))
    monkeypatch.setattr(cli.sync_mod, "push", lambda wt, br: None)
    monkeypatch.setattr(cli.sync_mod, "rebase_remote",
                        lambda *a, **k: rebase.append(a) or {"status": "rebased"})
    monkeypatch.setattr(cli.sync_mod, "pull_rebased",
                        lambda *a, **k: {"status": "reset"})
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["strategy"] == "local-merge"
    assert local == [(str(wt_dir), "main")] and rebase == []


def test_sync_dry_run_touches_nothing(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"})
    rebase, local = [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase)
    r = _invoke("sync", "IPG-929", "--dry-run", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out["strategy"] == "remote-rebase" and "would" in out["result"]
    assert rebase == [] and local == []


def test_sync_dirty_worktree_aborts(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    (wt_dir / "dirty.txt").write_text("x\n")
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.sync_mod, "dirty_files", lambda p: ["dirty.txt"])
    r = _invoke("sync", "IPG-929", "--yes")
    assert r.exit_code == 1
    assert "dirty.txt" in r.output


def test_sync_all_missing_worktree_skipped(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    _link_session(repo_dir, tmp_path / "missing", monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("sync", "--all", "--yes", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out[0]["result"] == "missing-worktree"


def test_doctor_missing_and_ok(isolated_config, tmp_path, monkeypatch):
    from workagent import doctor as _doctor
    monkeypatch.setenv("HOME", str(tmp_path))
    r = _invoke("doctor")
    assert r.exit_code == 1
    assert "doctor: missing" in r.output
    receipt = tmp_path / ".local/share/workagent/install-receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"source_hash": _doctor.source_hash()}))
    r = _invoke("doctor")
    assert r.exit_code == 0
    assert "doctor: ok" in r.output


def test_repo_list_csv(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _invoke("repo", "add", "--name", "proj", "--path", str(repo_dir),
            "--tracker", "IPG")
    r = _invoke("repo", "list", "--csv")
    assert r.exit_code == 0
    lines = [l for l in r.output.strip().splitlines() if l]
    assert lines[0] == "name,path,trackers"
    assert len(lines) == 2 and lines[1].startswith("proj,")


def test_link_set_list_remove_roundtrip(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = _invoke("link", "set", "IPG", str(repo_dir), "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["tracker"] == "jira:IPG"
    r = _invoke("link", "list", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["trackers"]["jira:IPG"]["repos"] == [str(repo_dir.resolve())]
    r = _invoke("link", "remove", "jira:IPG", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout) == {"removed": "jira:IPG"}


def test_cleanup_fuzzy_branch_match(isolated_config, monkeypatch):
    store.record_link("jira:IPG-981", {"issue": "IPG-981", "worktree": "/tmp/wt",
                                       "branch": "feat/IPG-981--x", "repo": "/tmp/proj"})
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    r = _invoke("cleanup", "IPG-981", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["key"] == "jira:IPG-981"


def test_cleanup_merged_loop(isolated_config, monkeypatch):
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": "/tmp/wt-1",
                                     "branch": "feat/1", "repo": "/tmp/proj"})
    store.record_link("jira:IPG-2", {"issue": "IPG-2", "worktree": "/tmp/wt-2",
                                     "branch": "feat/2", "repo": "/tmp/proj"})
    monkeypatch.setattr(
        cli, "_status_cells",
        lambda entry, refresh_pr=False: {"pr_data": {"state":
            "MERGED" if entry.get("branch") == "feat/1" else "OPEN"}})
    cleaned = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda repo, branch, **k: cleaned.append(branch) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)
    r = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r.exit_code == 0, r.output
    status = {row["key"]: row["status"] for row in json.loads(r.stdout)["results"]}
    assert status == {"jira:IPG-1": "cleaned", "jira:IPG-2": "skipped:open"}
    assert cleaned == ["feat/1"]
    links = store.load_links()
    assert "jira:IPG-1" not in links and "jira:IPG-2" in links


def test_cleanup_merged_skips_live_harness_and_invalid(isolated_config,
                                                       tmp_path, monkeypatch):
    wt_ok = tmp_path / "wt-ok"; wt_ok.mkdir()
    store.record_link("jira:IPG-3", {"issue": "IPG-3", "worktree": str(wt_ok),
                                     "branch": "feat/3", "repo": "/tmp/proj"})
    store.record_link("jira:IPG-4", {"issue": "IPG-4", "worktree": str(tmp_path / "gone"),
                                     "branch": "feat/4", "repo": "/tmp/proj"})
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "MERGED"}})
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 1}
                        if key == "jira:IPG-3" else None)
    cleaned = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda repo, branch, **k: cleaned.append(branch) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    r = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r.exit_code == 0, r.output
    status = {row["key"]: row["status"] for row in json.loads(r.stdout)["results"]}
    assert status == {"jira:IPG-3": "skipped:live-harness",
                      "jira:IPG-4": "skipped:invalid"}
    assert cleaned == []
    assert set(store.load_links()) == {"jira:IPG-3", "jira:IPG-4"}


def test_cleanup_merged_requires_yes_noninteractive(isolated_config, monkeypatch):
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": "/tmp/wt-1",
                                     "branch": "feat/1", "repo": "/tmp/proj"})
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "MERGED"}})
    r = _invoke("cleanup", "--merged", "--json")
    assert r.exit_code == 2
    assert "--yes" in r.output
    assert "jira:IPG-1" in store.load_links()
    # --dry-run previews without --yes and without acting.
    acted = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: acted.append(1) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)
    r2 = _invoke("cleanup", "--merged", "--dry-run", "--json")
    assert r2.exit_code == 0, r2.output
    assert acted == []
    status = {row["key"]: row["status"] for row in json.loads(r2.stdout)["results"]}
    assert status == {"jira:IPG-1": "dry-run"}
    assert "jira:IPG-1" in store.load_links()


def test_cleanup_merged_usage_errors(isolated_config):
    r = _invoke("cleanup", "IPG-1", "--merged", "--yes")
    assert r.exit_code == 2
    assert "--merged takes no ref" in r.output
    r2 = _invoke("cleanup")
    assert r2.exit_code == 2
    assert "ref required" in r2.output


def test_version(isolated_config):
    r = _invoke("--version")
    assert r.exit_code == 0
    assert "workagent" in r.output


def test_help_shows_examples_and_exit_codes(isolated_config):
    r = _invoke("--help")
    assert r.exit_code == 0
    assert "Exit codes" in r.output
    assert "workagent start" in r.output
def _start_mocks(monkeypatch, repo_dir):
    """Stub repo/issue lookups for `start` (no subprocesses, no network)."""
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add login", "body": "Details here"})

def test_start_passes_github_issue_url_to_git_wt(isolated_config, tmp_path, monkeypatch):
    """Shorthand refs must reach git-wt --link as full issue URLs (issue #22)."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    calls = {}
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: calls.update(kw) or {"worktree_path": str(worktree),
                                                              "branch": "feat/22--add-login"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    for ref in ("o/r#22", "github:o/r#22", "https://github.com/o/r/issues/22"):
        calls.clear()
        store.save_links({})
        r = runner.invoke(cli.app, ["start", ref, "--json"])
        assert r.exit_code == 0, r.output
        assert calls["link"] == "https://github.com/o/r/issues/22"
        assert calls["issue"] == "22"


def test_start_reuses_linked_worktree(isolated_config, tmp_path, monkeypatch):
    """Issue #26 rule 1: `start` for an already-linked issue reuses its worktree."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: (_ for _ in ()).throw(AssertionError("must reuse, not start")))
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    store.record_link("github:o/r#22", {"worktree": str(worktree), "branch": "b",
                                        "repo": str(repo_dir)})
    r = runner.invoke(cli.app, ["start", "o/r#22", "--json"])
    assert r.exit_code == 0, r.output
    assert '"reused": true' in r.output

def test_review_rejects_shorthand_issue_ref(isolated_config, tmp_path, monkeypatch):
    """`review` needs a PR/MR URL — a shorthand issue ref must fail loudly (exit 2)."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    r = runner.invoke(cli.app, ["review", "o/r#22", "--dry-run", "--json"])
    assert r.exit_code == 2
    assert "needs a PR/MR URL" in r.output

def test_start_base_existing_branch_reuses_branch(isolated_config, tmp_path, monkeypatch):
    """--base <non-default> runs on that branch: worktree for it, no new branch."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    calls = {}

    def fake_start_worktree(repo, **kw):
        calls.update(kw)
        calls["repo"] = repo
        return {"worktree_path": str(worktree), "branch": kw["branch"]}

    monkeypatch.setattr(cli.gitwt, "start_worktree", fake_start_worktree)
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    r = runner.invoke(cli.app, ["start", "o/r#22", "--base", "chore/IPG-978--cicd", "--json"])
    assert r.exit_code == 0, r.output
    assert calls["branch"] == "chore/IPG-978--cicd"
    assert calls["base"] == "main"
    assert "link" not in calls and "issue" not in calls and "slug" not in calls
    out = json.loads(r.stdout)
    assert out["branch"] == "chore/IPG-978--cicd" and out["base"] == "main"
    assert store.load_links()["github:o/r#22"]["branch"] == "chore/IPG-978--cicd"


def test_start_from_worktree_continues_on_current_branch(isolated_config, tmp_path, monkeypatch):
    """Running start from an existing worktree on a non-default branch
    continues on that branch — no new issue-named branch is created."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd=None: Path(worktree))
    monkeypatch.setattr(cli.repos, "worktree_branch", lambda path: "feat/IPG-978--auto-versioning")
    calls = {}

    def fake_start_worktree(repo, **kw):
        calls.update(kw)
        return {"worktree_path": str(worktree), "branch": kw["branch"]}

    monkeypatch.setattr(cli.gitwt, "start_worktree", fake_start_worktree)
    prompts = []
    monkeypatch.setattr(cli.backend, "prompt_for_issue",
                        lambda title, body, ref, worktree="", branch="": prompts.append((worktree, branch)))
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    r = runner.invoke(cli.app, ["start", "o/r#22", "--json"])
    assert r.exit_code == 0, r.output
    assert calls["branch"] == "feat/IPG-978--auto-versioning"
    assert "issue" not in calls and "slug" not in calls and "link" not in calls
    out = json.loads(r.stdout)
    assert out["branch"] == "feat/IPG-978--auto-versioning"
    # AI-harness prompt pins the push target to the existing branch.
    assert prompts[-1] == (str(worktree), "feat/IPG-978--auto-versioning")


def test_start_base_default_branch_still_creates_new_branch(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    # Hermetic regardless of where pytest runs: pin the cwd seam so a
    # feature-branch checkout can't flip start into cwd_mode.
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd=None: None)
    monkeypatch.setattr(cli.repos, "worktree_branch", lambda path: None)
    calls = {}

    def fake_start_worktree(repo, **kw):
        calls.update(kw)
        return {"worktree_path": "/tmp/wt", "branch": "feat/22--add-login"}

    monkeypatch.setattr(cli.gitwt, "start_worktree", fake_start_worktree)
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    r = runner.invoke(cli.app, ["start", "o/r#22", "--base", "main", "--json"])
    assert r.exit_code == 0, r.output
    assert calls["base"] == "main"
    assert calls["issue"] == "22" and calls["slug"] == "add-login"
    assert "branch" not in calls


def test_start_no_runtime_prints_command_and_skips_launch(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/22--add-login"})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["start", "o/r#22", "--no-runtime", "--json"])
    assert r.exit_code == 0, r.output
    assert launched == []  # runtime must not run
    out = json.loads(r.stdout)
    assert out["worktree_path"] == str(worktree)
    assert out["runtime_command"].startswith(f"cd {worktree} && omp ")
    assert "runtime command: cd " in r.stderr
    assert str(worktree) in r.stderr


def test_start_no_runtime_tty_lands_shell_in_worktree(isolated_config, tmp_path, monkeypatch, capsys):
    """TTY --no-runtime replaces the process with the user's shell in the worktree."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/22--add-login"})
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.chdir(tmp_path)

    class _Shell(Exception):
        pass

    def fake_execvp(file, argv):
        raise _Shell(file, argv, os.getcwd())

    monkeypatch.setattr(cli.os, "execvp", fake_execvp)
    with pytest.raises(_Shell) as excinfo:
        cli.start(ref="o/r#22", repo=None, depth=7, base=None, harness=None,
                  no_tty=False, no_runtime=True, dry_run=False, yes=False, json_output=False)
    assert excinfo.value.args[0] == "/bin/bash"
    assert excinfo.value.args[2] == str(worktree)
    captured = capsys.readouterr()
    assert "runtime command: cd " in captured.err
    assert f"worktree_path: {worktree}" in captured.out


def test_review_no_runtime_prints_command_and_skips_launch(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["review", "https://github.com/o/r/pull/33",
                                "--no-runtime", "--json"])
    assert r.exit_code == 0, r.output
    assert launched == []  # runtime must not run
    out = json.loads(r.stdout)
    assert out["worktree_path"] == str(worktree)
    assert out["runtime_command"].startswith(f"cd {worktree} && omp ")
    assert "runtime command: cd " in r.stderr
    assert str(worktree) in r.stderr


def test_review_no_runtime_tty_lands_shell_in_worktree(isolated_config, tmp_path, monkeypatch, capsys):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.chdir(tmp_path)

    class _Shell(Exception):
        pass

    def fake_execvp(file, argv):
        raise _Shell(file, argv, os.getcwd())

    monkeypatch.setattr(cli.os, "execvp", fake_execvp)
    with pytest.raises(_Shell) as excinfo:
        cli.review(ref="https://github.com/o/r/pull/33", repo=None, depth=7, harness=None,
                   no_tty=False, no_runtime=True, dry_run=False, yes=False, json_output=False,
                   all_wts=False, sequential=False, fix=False, post_comments=False)
    assert excinfo.value.args[0] == "/bin/zsh"
    assert excinfo.value.args[2] == str(worktree)


def test_no_runtime_shorthand_N_on_start_and_review(isolated_config, tmp_path, monkeypatch):
    """`-N` is accepted as shorthand for --no-runtime on both subcommands."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue", lambda ref: {"title": "t", "body": "b"})
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["start", "o/r#33", "--no-tty", "-N", "--json"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(cli.app, ["review", "https://github.com/o/r/pull/33",
                                 "--no-tty", "-N", "--json"])
    assert r2.exit_code == 0, r2.output
    assert launched == []
    assert json.loads(r.stdout)["runtime_command"].startswith("cd ")
    assert json.loads(r2.stdout)["runtime_command"].startswith("cd ")


def test_base_completion_lists_cwd_git_branches(isolated_config, tmp_path, monkeypatch):
    """--base completion: cwd local branches filtered by prefix; [] outside a repo."""
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True)
    for name in ("feat/login", "chore/cicd"):
        subprocess.run(["git", "branch", name], cwd=repo, check=True)
    callback = cli._complete_branches
    monkeypatch.chdir(repo)
    assert callback(None, "") == ["chore/cicd", "feat/login", "main"]
    assert callback(None, "feat/") == ["feat/login"]
    assert callback(None, "zzz") == []
    monkeypatch.chdir(tmp_path)  # not a git repo
    assert callback(None, "") == []


def test_status_detail_create_hint_github(isolated_config, tmp_path,
                                          monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    subprocess.run(["git", "-C", str(wt_dir), "remote", "add", "origin",
                    "git@github.com:owner/repo.git"], check=True,
                   capture_output=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    monkeypatch.setattr(cli.repos, "remote_url",
                        lambda p: "git@github.com:owner/repo.git")
    r = _invoke("status", "IPG-929", "--json")
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["create_hint"] == \
        "https://github.com/owner/repo/compare/feat/IPG-929--x?expand=1"


def test_status_detail_create_hint_gitlab(isolated_config, tmp_path,
                                          monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    monkeypatch.setattr(cli.repos, "remote_url", lambda p: None)  # no origin
    r = _invoke("status", "IPG-929", "--json")
    data = json.loads(r.stdout)
    assert not data.get("create_hint")
    monkeypatch.setattr(cli.repos, "remote_url",
                        lambda p: "https://git.jibit.cloud/srv/proj.git")
    r = _invoke("status", "IPG-929", "--json")
    data = json.loads(r.stdout)
    assert data["create_hint"] == \
        "glab mr create --repo git.jibit.cloud/srv/proj --source-branch feat/IPG-929--x"


def test_status_detail_no_hint_when_pr_exists(isolated_config, tmp_path,
                                              monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"})
    r = _invoke("status", "IPG-929", "--json")
    data = json.loads(r.stdout)
    assert "create_hint" not in data
    assert data["pr"] == "PR #9 (open)"


def test_status_harness_cell_cross_key_worktree(isolated_config, tmp_path):
    """A harness recorded under another key still shows for this worktree."""
    wt = tmp_path / "wt"; wt.mkdir()
    store.record_link("jira:H-1", {"branch": "feat/h", "worktree": str(wt)})
    # One worktree holds exactly one claim: record under the pr key, and the
    # jira row for the same checkout shows it via the cross-key fallback.
    store.save_harnesses_raw({"pr:https://x/o/r/-/merge_requests/1":
                              {"harness": "omp", "pid": __import__("os").getpid(),
                               "started_at": 1.0, "worktree": str(wt)}})
    rows, _ = cli._session_rows(store.load_links(), False, False)
    row = next(r for r in rows if r["key"] == "jira:H-1")
    assert row["harness"].startswith("omp ")


def test_status_row_shows_live_harness(isolated_config, tmp_path, monkeypatch):
    wt = tmp_path / "wt"; wt.mkdir()
    store.record_link("jira:H-1", {"branch": "feat/h", "worktree": str(wt)})
    store.record_harness_run("jira:H-1", "omp", str(wt))
    rows, columns = cli._session_rows(store.load_links(), False, False)
    assert "harness" in columns
    row = next(r for r in rows if r["key"] == "jira:H-1")
    assert row["harness"].startswith("omp ")
    # --json rows and the detail panel gain the live harness too.
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:H-1"]["harness"].startswith("omp ")
    detail = json.loads(_invoke("status", "H-1", "--json").stdout)
    assert detail["harness"].startswith("omp ")
    # --csv gains the harness column (third after key, branch).
    lines = _invoke("status", "--csv").stdout.strip().splitlines()
    assert lines[0].split(",")[2] == "harness"
    assert lines[1].split(",")[2].startswith("omp")
    # After the run clears, the cell is empty again.
    store.clear_harness_run("jira:H-1")
    rows, _ = cli._session_rows(store.load_links(), False, False)
    assert next(r for r in rows if r["key"] == "jira:H-1")["harness"] == ""


def test_cd_prints_worktree(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    r = _invoke("cd", "IPG-929", "--json")
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data == {"key": "jira:IPG-929", "worktree": str(wt_dir)}
    r = _invoke("cd", "IPG-929")
    assert r.stdout.strip() == str(wt_dir)


def test_cd_missing_worktree_errors(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "gone"
    _link_session(repo_dir, wt_dir, monkeypatch)
    r = _invoke("cd", "IPG-929")
    assert r.exit_code == 1


def test_cd_unknown_ref(isolated_config):
    r = _invoke("cd", "NOPE-1")
    assert r.exit_code == 2


def test_cd_wrapper_snippets():
    from workagent import completions as c
    w = c.cd_wrapper("workagent", "bash")
    assert w.startswith("workagent-cd()") and 'cd "$(command workagent cd' in w
    assert "function workagent-cd" in c.cd_wrapper("workagent", "fish")
    snip = c.install_snippet("workagent", "bash")
    assert "workagent-cd()" in snip


def test_open_resolves_and_opens(isolated_config, tmp_path, monkeypatch):
    # Popen is faked, which also breaks subprocess.run inside
    # is_valid_worktree — stub the validity check instead.
    wt = tmp_path / "wt"
    store.record_link("jira:IPG-929", {"issue": "IPG-929", "worktree": str(wt),
                                       "branch": "feat/IPG-929--x", "repo": str(tmp_path)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)
    opened = []

    class FakePopen:
        def __init__(self, argv, **kw):
            opened.append(argv)
            kwargs.update(kw)

    kwargs = {}
    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = _invoke("open", "IPG-929")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(wt)
    assert opened, "no opener spawned"
    assert opened[0][0] in ("xdg-open", "open", "explorer")
    assert opened[0][1] == str(wt)
    assert kwargs["stdin"] == kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_open_refuses_invalid_worktree(isolated_config, tmp_path):
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": str(tmp_path / "gone"),
                                     "branch": "feat/1", "repo": str(tmp_path)})
    r = _invoke("open", "IPG-1")
    assert r.exit_code == 1
    assert "jira:IPG-1" in r.output


def test_open_refuses_missing_link(isolated_config):
    r = _invoke("open", "NOPE-1")
    assert r.exit_code == 2
    assert "no linked state" in r.output


def test_open_no_opener_available(isolated_config, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": str(wt),
                                     "branch": "feat/1", "repo": str(tmp_path)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)

    def boom(argv, **kw):
        raise FileNotFoundError("xdg-open")

    monkeypatch.setattr(cli.subprocess, "Popen", boom)
    r = _invoke("open", "IPG-1")
    assert r.exit_code == 1
    assert "no opener available" in r.output


def test_sync_rebase_failure_falls_back_to_local_merge(
        isolated_config, tmp_path, monkeypatch):
    from workagent.errors import HarnessError
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"})
    rebase, local = [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase)
    monkeypatch.setattr(cli.sync_mod, "rebase_remote",
                        lambda tool, pr, wt: (_ for _ in ()).throw(
                            HarnessError("glab mr rebase failed: conflict")))
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-merge" and out["fallback"] is True
    assert out["result"] == "merged" and out.get("pushed") is True
    assert local == [(str(wt_dir), "main")] and rebase == []


# ── register: register an existing worktree by path ────────────────────


def _init_repo(path: Path, default: str = "main") -> Path:
    subprocess.run(["git", "init", "-q", "-b", default, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"],
                   check=True, capture_output=True)
    (path / "f.txt").write_text("1\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"],
                   check=True, capture_output=True)
    return path


def _add_wt(repo: Path, name: str, branch: str) -> Path:
    wt = repo.parent / name
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b",
                    branch, str(wt)], check=True, capture_output=True)
    return wt


def test_register_registers_by_path(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "feat/IPG-777--jira-linking")
    r = _invoke("register", str(wt), "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["key"] == "jira:IPG-777"
    assert data["worktree"] == str(wt)
    assert data["branch"] == "feat/IPG-777--jira-linking"
    assert data["repo"] == str(repo)
    entry = store.lookup_link("jira:IPG-777")
    assert entry is not None and entry["worktree"] == str(wt)


def test_register_defaults_to_branch_key(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "plain-named-branch")
    r = _invoke("register", str(wt), "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["key"] == "branch:plain-named-branch"


def test_register_issue_ref_sets_key_and_url(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.refs, "jira_site", lambda: "https://jira.example.com")
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "some-branch")
    r = _invoke("register", str(wt), "--issue", "IPG-555", "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["key"] == "jira:IPG-555"
    assert data["issue_url"] == "https://jira.example.com/browse/IPG-555"
    assert store.lookup_link("jira:IPG-555")["branch"] == "some-branch"


def test_register_persists_tracker_repo_relation(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "b")
    r = _invoke("register", str(wt), "--issue", "owner/repo#12",
                "--yes", "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["key"] == "github:owner/repo#12"
    rels = store.load_config().get("trackers", {})
    assert str(repo) in rels.get("github:owner/repo", {}).get("repos", [])


def test_register_idempotent_same_worktree(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "feat/IPG-777--jira-linking")
    _invoke("register", str(wt), "--json")
    r = _invoke("register", str(wt), "--json")
    assert r.exit_code == 0
    assert "already registered" in r.stderr


def test_register_conflicting_key_needs_force(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt1 = _add_wt(repo, "wt1", "feat/IPG-777--jira-linking")
    wt2 = _add_wt(repo, "wt2", "feat/IPG-778--other")
    assert _invoke("register", str(wt1), "--json").exit_code == 0
    r = _invoke("register", str(wt2), "--key", "jira:IPG-777", "--json")
    assert r.exit_code == 1
    assert "--force" in r.stderr
    r = _invoke("register", str(wt2), "--key", "jira:IPG-777", "--force",
                "--json")
    assert r.exit_code == 0
    assert store.lookup_link("jira:IPG-777")["worktree"] == str(wt2)


def test_register_rejects_main_checkout(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    r = _invoke("register", str(repo), "--json")
    assert r.exit_code == 2
    assert "main checkout" in r.stderr


def test_register_rejects_non_git_dir(isolated_config, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    r = _invoke("register", str(d), "--json")
    assert r.exit_code == 2


def test_register_missing_dir(isolated_config, tmp_path):
    r = _invoke("register", str(tmp_path / "nope"), "--json")
    assert r.exit_code == 2


def test_register_bad_issue_ref(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "b")
    r = _invoke("register", str(wt), "--issue", "not a ref", "--json")
    assert r.exit_code == 2
    assert "cannot parse" in r.stderr


def test_review_reuses_existing_worktree_by_branch(isolated_config, tmp_path, monkeypatch):
    """review with a branch that has a recorded worktree reuses it (issue #2)."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    wt = tmp_path / "wt-existing"
    wt.mkdir()
    store.record_link("jira:IPG-929", {"branch": "feat/IPG-929--x",
                                       "worktree": str(wt),
                                       "repo": str(repo_dir),
                                       "pr_url": "https://git.example.com/g/p/-/merge_requests/1699"})
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    started = []
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: started.append(kw) or {"worktree_path": "SHOULD-NOT-HAPPEN",
                                                                  "branch": kw.get("branch")})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["review", "feat/IPG-929--x", "--no-tty", "--no-runtime", "--json"])
    assert r.exit_code == 0, r.output
    assert started == []  # no new worktree created
    assert json.loads(r.stdout)["worktree_path"] == str(wt)


def test_review_all_spawns_parallel_children(isolated_config, tmp_path, monkeypatch):
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1"},
        "jira:B-2": {"branch": "feat/b", "worktree": str(tmp_path / "b"),
                     "pr_url": "https://github.com/o/r/pull/2"},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    for d in ("a", "b"):
        (tmp_path / d / ".git").mkdir()  # cheap validity stand-in
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    real_load_links = store.load_links
    monkeypatch.setattr(cli.store, "load_links", lambda: real_load_links())
    spawns = []

    class FakePopen:
        def __init__(self, argv, **kw):
            self.argv = argv
            self.pid = 4242
            spawns.append(argv)
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert len(spawns) == 2
    for argv in spawns:
        assert argv[:3] == [cli.sys.executable, "-m", "workagent"]
        assert "--no-tty" in argv and "--post-comments" in argv
    out = json.loads(r.stdout)
    assert sorted(e["key"] for e in out) == ["jira:A-1", "jira:B-2"]
    assert all(e["exit_code"] == 0 for e in out)


def test_review_all_sequential_waits_one_by_one(isolated_config, tmp_path, monkeypatch):
    """--sequential spawns and waits per worktree; --fix reaches the child argv."""
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1"},
        "jira:B-2": {"branch": "feat/b", "worktree": str(tmp_path / "b"),
                     "pr_url": "https://github.com/o/r/pull/2"},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    events = []

    class FakePopen:
        def __init__(self, argv, **kw):
            self.argv = argv
            events.append(("spawn", argv))
        def wait(self, timeout=None):
            events.append(("wait", self.argv))
            return 3

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--sequential", "--fix", "--json"])
    assert r.exit_code == 0, r.output
    assert [e[0] for e in events] == ["spawn", "wait", "spawn", "wait"]
    for kind, argv in events:
        if kind == "spawn":
            assert "--fix" in argv and "--post-comments" in argv and "--no-tty" in argv
    out = json.loads(r.stdout)
    assert all(e["exit_code"] == 3 for e in out)  # child rc surfaced in summary


def test_review_all_skips_live_harness_and_empty(isolated_config, tmp_path, monkeypatch):
    """A live-harness worktree is skipped with a note; zero reviewable → exit 0."""
    wt = tmp_path / "a"
    wt.mkdir()
    store.record_link("jira:A-1", {"branch": "feat/a", "worktree": str(wt),
                                   "pr_url": "https://github.com/o/r/pull/1"})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 99999, "started_at": 1.0})
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []  # live harness worktree skipped, nothing else to do
    assert "harness already live" in r.stderr
    assert "nothing to review" in r.stderr
    assert r.stdout.strip() == ""


def test_review_sequential_without_all_fails():
    r = runner.invoke(cli.app, ["review", "--sequential"])
    assert r.exit_code == 2


def test_review_all_notes_no_pr_skips(isolated_config, tmp_path, monkeypatch):
    """--all must note worktrees with no resolvable PR/MR, not skip silently."""
    wt = tmp_path / "a"
    wt.mkdir()
    store.record_link("jira:A-1", {"branch": "feat/a", "worktree": str(wt)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.worktrees, "worktree_pr_url", lambda k, v: None)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []
    assert "jira:A-1: no PR/MR — skipping" in r.stderr
    assert "nothing to review" in r.stderr


def test_review_without_ref_is_usage_error(isolated_config):
    """Bare `workagent review` (no ref, no --all) is a usage error, not a crash."""
    r = runner.invoke(cli.app, ["review"])
    assert r.exit_code == 2, r.output
    assert "missing PR/MR ref" in r.stderr
    assert "use --all" in r.stderr


def test_review_marks_reviewed_with_tip(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "branch_tip", lambda wt: "abc123")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    url = "https://github.com/o/r/pull/33"
    key = f"pr:{url}"

    # --no-runtime starts nothing: must not mark reviewed (Task 2 precedent).
    r0 = runner.invoke(cli.app, ["review", url, "--no-tty", "--no-runtime", "--json"])
    assert r0.exit_code == 0, r0.output
    assert launched == []
    assert store.load_links()[key].get("reviewed") is None

    # Fresh-worktree launch path: marks reviewed with the worktree tip.
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r.exit_code == 0, r.output
    assert launched != []
    entry = store.load_links()[key]
    assert entry["reviewed"] is True
    assert entry["reviewed_at"] == "abc123"

    # Reuse-worktree launch path: marks reviewed as well (idempotent re-mark).
    r2 = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r2.exit_code == 0, r2.output
    entry2 = store.load_links()[key]
    assert entry2["reviewed"] is True
    assert entry2["reviewed_at"] == "abc123"


def test_review_reuses_existing_worktree_row(isolated_config, tmp_path, monkeypatch):
    """Reviewing an already-tracked worktree stamps pr_url on its own row —
    no second pr:<url> row for the same path/branch (UNIQUE regression on a
    real state.db)."""
    from workagent import store_sqlite as sq
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "branch_tip", lambda wt: "abc123")
    url = "https://git.jibit.cloud/server/projectx/-/merge_requests/1694"
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/IPG-953--x"})

    def _no_start(repo, **kw):
        raise AssertionError("must reuse the recorded worktree, not start one")
    monkeypatch.setattr(cli.gitwt, "start_worktree", _no_start)
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    db = sq.db_path()
    sq.init_db(db)  # real state.db → worktrees.branch UNIQUE is live
    store.record_link("jira:IPG-953", {
        "worktree": str(worktree),
        "branch": "feat/IPG-953--x",
        "repo": str(repo_dir),
    })
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r.exit_code == 0, r.output
    links = store.load_links()
    assert set(links) == {"jira:IPG-953"}  # no pr:<url> alias row
    assert links["jira:IPG-953"]["pr_url"] == url
    assert links["jira:IPG-953"]["reviewed"] is True
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 1

def test_is_reviewed_resets_when_tip_changes(isolated_config, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    key = "pr:https://github.com/o/r/pull/9"
    store.record_link(key, {"reviewed": True, "reviewed_at": "cafe",
                            "worktree": str(wt)})
    entry = store.load_links()[key]
    monkeypatch.setattr(cli.repos, "branch_tip", lambda p: "cafe")
    assert cli._is_reviewed(key, entry) is True
    monkeypatch.setattr(cli.repos, "branch_tip", lambda p: "beef")
    assert cli._is_reviewed(key, entry) is False  # tip moved → reviewed again
    assert cli._is_reviewed(key, {"worktree": str(wt)}) is False  # never marked
    assert cli._is_reviewed(key, {"reviewed": True}) is False  # no worktree/tip

def test_start_refused_while_harness_live(isolated_config, tmp_path, monkeypatch):
    """Hard guard: a worktree with a live harness refuses a second launch."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd=None: None)
    monkeypatch.setattr(cli.repos, "worktree_branch", lambda path: None)
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 99999, "started_at": 1.0})
    launched = []
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": "/tmp/wt", "branch": "feat/22--add-login"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["start", "o/r#22", "--json"])
    assert r.exit_code == 1
    assert "already has a live harness" in r.stderr
    assert launched == []


def test_run_harness_records_and_clears(monkeypatch):
    """_run_harness records the run before launch and clears it after."""
    rec = []
    monkeypatch.setattr(cli.store, "record_harness_run",
                        lambda k, h, wt="": rec.append(("rec", k, h)))
    monkeypatch.setattr(cli.store, "clear_harness_run",
                        lambda k: rec.append(("clr", k)))
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    cli._run_harness("omp", "prompt", "/tmp/wt", "/tmp", True, False,
                     {"k": "v"}, False, run_key="jira:X")
    assert ("rec", "jira:X", "omp") in rec and ("clr", "jira:X") in rec


def test_start_no_runtime_not_guarded_when_busy(isolated_config, tmp_path, monkeypatch):
    """--no-runtime starts nothing: never guarded, never refused."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd=None: None)
    monkeypatch.setattr(cli.repos, "worktree_branch", lambda path: None)
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 99999, "started_at": 1.0})
    launched = []
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": "/tmp/wt", "branch": "feat/22--add-login"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["start", "o/r#22", "--no-runtime", "--json"])
    assert r.exit_code == 0, r.output
    assert "already has a live harness" not in r.stderr
    assert launched == []


def test_review_all_dry_run_prints_plan_without_spawning(isolated_config,
                                                         tmp_path,
                                                         monkeypatch):
    """--all --dry-run prints the per-child plan and spawns nothing."""
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1",
                     "repo": str(tmp_path / "proj")},
        "jira:B-2": {"branch": "feat/b", "worktree": str(tmp_path / "b"),
                     "pr_url": "https://github.com/o/r/pull/2",
                     "repo": str(tmp_path / "proj")},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []  # dry run must not launch children
    out = json.loads(r.stdout)
    assert sorted(e["key"] for e in out) == ["jira:A-1", "jira:B-2"]
    for row in out:
        assert row["pr_url"].startswith("https://github.com/o/r/pull/")
        assert "-m workagent review" in row["command"]
        assert row["pr_url"] in row["command"]
        assert "--no-tty" in row["command"] and "--post-comments" in row["command"]


def test_review_all_no_runtime_prints_commands_without_spawning(isolated_config,
                                                                tmp_path,
                                                                monkeypatch):
    """--all -N prints each child command as summary rows without spawning."""
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1",
                     "repo": str(tmp_path / "proj")},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--no-runtime", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []
    out = json.loads(r.stdout)
    assert [e["key"] for e in out] == ["jira:A-1"]
    assert out[0]["exit_code"] == ""
    assert "-m workagent review https://github.com/o/r/pull/1" in out[0]["command"]
    assert "--no-tty" in out[0]["command"] and "--post-comments" in out[0]["command"]


def test_review_launch_failure_clears_reviewed(isolated_config, tmp_path,
                                               monkeypatch):
    """A failed non-exec launch reverts the reviewed fields (spec bookkeeping)."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False,
                        persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "branch_tip", lambda wt: "abc123")
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})

    from workagent.errors import HarnessError

    def boom(*a, **k):
        raise HarnessError("no such harness", 1)

    monkeypatch.setattr(cli.backend, "launch", boom)
    url = "https://github.com/o/r/pull/33"
    key = f"pr:{url}"
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r.exit_code == 1
    entry = store.load_links()[key]
    assert "reviewed" not in entry and "reviewed_at" not in entry


def test_review_all_jira_pr_alias_dedupes(isolated_config, tmp_path,
                                          monkeypatch):
    """jira: and pr: links to the same PR → one reviewable child; a reviewed
    pr: entry makes the jira: alias unreviewable too."""
    wt = tmp_path / "a"
    wt.mkdir()
    (wt / ".git").mkdir()
    pr = "https://github.com/o/r/pull/1"
    store.record_link("jira:A-1", {"branch": "feat/a", "worktree": str(wt),
                                   "pr_url": pr})
    store.record_link(f"pr:{pr}", {"pr_url": pr, "branch": "feat/a",
                                   "worktree": str(wt)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    monkeypatch.setattr(cli.repos, "branch_tip", lambda p: "tip1")

    spawned = []
    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert len(spawned) == 1  # alias deduped: one child per worktree/PR
    assert spawned[0][4] == pr  # child spawned with the pr: entry's URL

    # Reviewed pr: entry → jira: alias skipped too.
    links = store.load_links()
    links[f"pr:{pr}"]["reviewed"] = True
    links[f"pr:{pr}"]["reviewed_at"] = "tip1"
    store.save_links(links)
    spawned.clear()
    r2 = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r2.exit_code == 0, r2.output
    assert spawned == []
    assert "nothing to review" in r2.stderr


def test_review_fix_without_all_is_usage_error():
    r = runner.invoke(cli.app, ["review", "https://github.com/o/r/pull/1", "--fix"])
    assert r.exit_code == 2
    assert "--fix requires --all" in r.stderr


def test_status_detail_shows_harness_line(isolated_config, tmp_path,
                                          monkeypatch):
    """The human detail panel includes the harness line, even when empty."""
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    r = _invoke("status", "IPG-929")
    assert r.exit_code == 0, r.output
    assert "harness: —" in r.output


# ── candidates ────────────────────────────────────────────────────────


def _candidates_env(monkeypatch, tmp_path, issues=None, prs=None):
    repo = tmp_path / "proj"
    repo.mkdir()
    cfg = store.load_config()
    cfg["repos"] = {"proj": {"path": str(repo)}}
    # Point the worktree scan at an empty dir: the real ~/dev/worktrees
    # would otherwise leak into every candidates test on dev machines.
    empty = tmp_path / "empty-scan"
    empty.mkdir()
    cfg["scan_root"] = str(empty)
    store.save_config(cfg)
    monkeypatch.setattr(cli, "_repo_tool", lambda path: "gh")
    monkeypatch.setattr(cli.refs, "fetch_open_prs", lambda tool, cwd: prs or [
        {"number": 1, "title": "Add [beta] flag", "branch": "feat/1",
         "updated": "2026-09-20T10:00:00Z",
         "url": "https://github.com/o/r/pull/1", "state": "OPEN"}])
    monkeypatch.setattr(cli.trackers, "list_my_issues",
                        lambda *a, **k: issues or [])


def test_candidates_cli_json(isolated_config, tmp_path, monkeypatch):
    _candidates_env(monkeypatch, tmp_path, issues=[
        {"key": "jira:IPG-981", "title": "T", "url": "u",
         "status": "To Do", "created": "2026-09-20T10:00:00+00:00"}])
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
         "status": "To Do", "created": "2026-09-20T10:00:00+00:00"}])
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
         "status": "To Do", "created": "2026-09-20T10:00:00+00:00"}])
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


def test_candidates_cli_warning_to_stderr(isolated_config, tmp_path,
                                          monkeypatch):
    repo = tmp_path / "proj"
    repo.mkdir()
    cfg = store.load_config()
    cfg["repos"] = {"proj": {"path": str(repo)}}
    empty = tmp_path / "empty-scan"
    empty.mkdir()
    cfg["scan_root"] = str(empty)
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


def test_run_harness_writes_session_row(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    # Post-cutover the worktree row exists in SQLite; seed it here.
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('jira:IPG-929', '/wt', 'b', 'r')")
    result = {"key": "jira:IPG-929"}
    cli._run_harness("omp", "prompt", str(wt_dir), str(repo_dir), False,
                     False, result, True, run_key="jira:IPG-929")
    rows = sq.list_sessions(sq.db_path())
    assert len(rows) == 1 and rows[0]["state"] == "finished"
    assert rows[0]["runtime_name"] == "omp"
    assert rows[0]["file_path"].endswith(".jsonl")


def test_run_harness_no_runtime_writes_nothing(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    result = {"key": "k"}
    cli._run_harness("omp", "prompt", "/tmp/wt", "/tmp", False,
                     True, result, True, run_key="k")
    assert sq.list_sessions(sq.db_path()) == []


def test_cleanup_merges_open_pr_first(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "worktree": "/tmp/wt",
                                     "branch": "feat/9", "repo": "/tmp/proj",
                                     "pr_url": "https://github.com/o/r/pull/9"})
    calls = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: calls.append("cleanup") or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: calls.append("close"))
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "OPEN"}})
    from workagent.errors import run_cmd as _real  # noqa: F841 (documents the seam)
    def fake(*a, **k):
        calls.append(a)
        return ""
    monkeypatch.setattr("workagent.cli.run_cmd", fake)
    entry = dict(store.load_links()["jira:IPG-9"])
    out = cli._cleanup_one("jira:IPG-9", entry, force=True, yes=True,
                           dry_run=False, json_output=True)
    assert out["status"] == "cleaned"
    assert any("merge" in str(c) for c in calls)
    assert "close" not in calls  # merged, not closed


def test_cleanup_merge_failure_keeps_worktree(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "worktree": "/tmp/wt",
                                     "branch": "feat/9", "repo": "/tmp/proj",
                                     "pr_url": "https://github.com/o/r/pull/9"})
    from workagent.errors import HarnessError
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "OPEN"}})
    def boom(*a, **k):
        raise HarnessError("merge conflict")
    monkeypatch.setattr("workagent.cli.run_cmd", boom)
    import pytest
    with pytest.raises(HarnessError):
        cli._cleanup_one("jira:IPG-9", dict(store.load_links()["jira:IPG-9"]),
                         force=False, yes=True, dry_run=False,
                         json_output=True)
    assert "jira:IPG-9" in store.load_links()  # link kept


def test_migrate_command(isolated_config, tmp_path):
    import json
    d = Path(str(isolated_config))
    (d / "links.json").write_text(json.dumps({
        "jira:IPG-1": {"worktree": "/wt", "branch": "feat/1",
                       "repo": "/r"}}))
    r = _invoke("migrate", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["migrated"]["worktrees"] == 1
    assert not (d / "links.json").exists()


def test_migrate_roundtrip_uses_sqlite(isolated_config):
    import json
    d = Path(str(isolated_config))
    store.record_link("jira:IPG-7", {"worktree": "/wt7", "branch": "feat/7",
                                     "repo": "/r", "reviewed": True})
    store.cache_pr_status("feat/7", {"number": 7}, tool="gh")
    r = _invoke("migrate", "--json")
    assert r.exit_code == 0, r.output
    # Legacy files gone; reads now served from SQLite with extras preserved.
    assert not (d / "links.json").exists()
    assert not (d / "pr_cache.json").exists()
    assert store.load_links()["jira:IPG-7"]["reviewed"] is True
    assert store.load_pr_cache()["feat/7"]["tool"] == "gh"
    # Writes after cutover persist in SQLite, not JSON.
    store.record_link("jira:IPG-8", {"worktree": "/wt8", "branch": "feat/8",
                                     "repo": "/r"})
    assert "jira:IPG-8" in store.load_links()
    assert not (d / "links.json").exists()


def test_cleanup_unknown_pr_state_closes_without_merge(isolated_config, monkeypatch):
    store.record_link("jira:IPG-10", {"issue": "IPG-10", "worktree": "/tmp/wt",
                                      "branch": "feat/10", "repo": "/tmp/proj",
                                      "pr_url": "https://github.com/o/r/pull/10"})
    calls = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: calls.append("close"))
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": ""}})
    monkeypatch.setattr("workagent.cli.run_cmd",
                        lambda *a, **k: calls.append("merge") or "")
    cli._cleanup_one("jira:IPG-10", dict(store.load_links()["jira:IPG-10"]),
                     force=False, yes=True, dry_run=False, json_output=True)
    assert "merge" not in calls
    assert "close" in calls

def test_close_pr_treats_merged_mr_as_closed(isolated_config, monkeypatch, capsys):
    """`glab mr close` on a merged MR must not abort cleanup (run eb420c1bb14d)."""
    from workagent.errors import HarnessError
    import workagent.errors as errors
    def fake_run(*a, **k):
        raise HarnessError("glab mr close https://git.jibit.cloud/server/projectx/-/merge_requests/1700 "
                           "failed: ERROR\n\n  This merge request has already been merged.")
    monkeypatch.setattr(errors, "run_cmd", fake_run)
    cli._close_pr({}, "https://git.jibit.cloud/server/projectx/-/merge_requests/1700",
                  force=False, cwd="/tmp")
    assert "already closed" in capsys.readouterr().err

def test_merge_pr_treats_merged_mr_as_success(isolated_config, monkeypatch, capsys):
    """`glab mr merge` on an already-merged MR must not abort cleanup (3d TTL staleness)."""
    from workagent.errors import HarnessError
    import workagent.errors as errors
    def fake_run(*a, **k):
        raise HarnessError("glab mr merge https://git.jibit.cloud/x/-/merge_requests/1 "
                           "failed: ERROR\n\n  This merge request has already been merged.")
    monkeypatch.setattr(cli, "run_cmd", fake_run)
    cli._merge_pr("https://git.jibit.cloud/x/-/merge_requests/1", squash=True, cwd="/tmp")
    assert "already merged/closed" in capsys.readouterr().err

def test_cleanup_merged_state_skips_remote_close(isolated_config, monkeypatch, capsys):
    """Known-merged PR state skips the host close call entirely (no-op)."""
    store.record_link("jira:IPG-11", {"issue": "IPG-11", "worktree": "/tmp/wt",
                                      "branch": "feat/11", "repo": "/tmp/proj",
                                      "pr_url": "https://github.com/o/r/pull/11"})
    calls = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: calls.append("close"))
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "merged"}})
    cli._cleanup_one("jira:IPG-11", dict(store.load_links()["jira:IPG-11"]),
                     force=False, yes=True, dry_run=False, json_output=True)
    assert "close" not in calls
    assert "skipping remote close" in capsys.readouterr().err


def test_close_issue_treats_closed_as_success(isolated_config, monkeypatch, capsys):
    """`gh issue close` on a closed issue continues cleanup instead of raising."""
    from workagent.errors import HarnessError
    import workagent.errors as errors
    def fake_run(*a, **k):
        raise HarnessError("gh issue close 22 --repo o/r failed: GraphQL: Could not resolve to an Issue with the number of 22")
    monkeypatch.setattr(errors, "run_cmd", fake_run)
    cli._close_issue({"tool": "gh", "repo": "o/r", "kind": "issue_or_pr",
                      "number": "22", "url": "https://github.com/o/r/issues/22"},
                     force=False)
    assert "already closed" in capsys.readouterr().err


def test_cleanup_repairs_stale_repo_from_live_worktree(isolated_config, tmp_path, monkeypatch):
    """Stale recorded repo (wrong checkout) is repaired from the live
    worktree, and the MR close runs with cwd inside the real repo."""
    import subprocess
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "-C", str(main), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.name", "t"], check=True)
    (main / "f").write_text("x")
    subprocess.run(["git", "-C", str(main), "add", "."], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(main), "checkout", "-qb", "feat/x"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", str(wt)], check=True)
    store.record_link("jira:X-1", {"issue": "X-1", "worktree": str(wt),
                                   "branch": "feat/x", "repo": "/stale/checkout",
                                   "pr_url": "https://git.example.com/g/r/-/merge_requests/1"})
    seen = {}
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda repo, branch, **k: seen.setdefault("repo", str(repo)) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    def close(parsed, url, force, cwd=None):
        seen["cwd"] = cwd
        # Worktree must still exist at close time: the close runs BEFORE
        # git-wt teardown (IPG-953: close-after-teardown ran glab from a
        # deleted cwd and failed with "no git remote points to a known host").
        seen["wt_alive_at_close"] = Path(cwd).is_dir()
    monkeypatch.setattr(cli, "_close_pr", close)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": ""}})
    cli._cleanup_one("jira:X-1", dict(store.load_links()["jira:X-1"]),
                     force=False, yes=True, dry_run=False, json_output=True)
    assert seen["repo"] == str(main)
    assert seen["cwd"] == str(wt)
    assert seen["wt_alive_at_close"] is True
    assert "jira:X-1" not in store.load_links()


def test_cutover_link_write_preserves_sessions(isolated_config):
    import json
    from workagent import store_sqlite as sq
    d = Path(str(isolated_config))
    (d / "links.json").write_text(json.dumps({
        "jira:IPG-1": {"worktree": "/wt1", "branch": "feat/1", "repo": "/r"},
        "jira:IPG-2": {"worktree": "/wt2", "branch": "feat/2", "repo": "/r"}}))
    r = _invoke("migrate", "--json")
    assert r.exit_code == 0, r.output
    db = sq.db_path()
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('x', '/x', 'x')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'x')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('jira:IPG-9', '/wt9', 'b9', 'x')")
    sid = sq.insert_session(db, worktree_ref="jira:IPG-9",
                            runtime_name="omp", initiator_command="start",
                            prompt="p", file_path="/tmp/s.jsonl",
                            session_id="sid-1")
    store.record_link("jira:IPG-3", {"worktree": "/wt3", "branch": "feat/3",
                                     "repo": "/r"})
    assert sq.get_session(db, sid) is not None
    assert "jira:IPG-3" in store.load_links()


def test_session_id_matches_file(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('jira:IPG-929', '/wt', 'b', 'r')")
    result = {"key": "jira:IPG-929"}
    cli._run_harness("omp", "prompt", str(wt_dir), str(repo_dir), False,
                     False, result, True, run_key="jira:IPG-929")
    rows = sq.list_sessions(db)
    assert len(rows) == 1
    assert rows[0]["file_path"].endswith(rows[0]["id"] + ".jsonl")


def test_candidates_reset_cache_clears(isolated_config, monkeypatch):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira", [{"key": "IPG-1"}])
    monkeypatch.setattr(cli.trackers, "list_my_issues", lambda *a, **k: [])
    monkeypatch.setattr(cli.refs, "fetch_open_prs", lambda *a, **k: [])
    r = _invoke("candidates", "--reset-cache", "--json")
    assert r.exit_code == 0, r.output
    assert sq.get_issue_cache(db, "jira") == ([], None)


def test_repo_add_derives_tracker_from_github_remote(isolated_config, tmp_path, monkeypatch):
    """--tracker omitted: GitHub origin → github:O/R mapping (issue #19)."""
    import subprocess
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "remote", "add", "origin",
                    "https://github.com/owner/repo.git"], check=True)
    monkeypatch.setattr(cli.repos, "_detect_host_cli", lambda path: "gh")
    r = _invoke("repo", "add", "--name", "p", "--path", str(repo_dir), "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["tracker"] == "github:owner/repo"
    assert store.load_config()["trackers"]["github:owner/repo"]["repos"] == [str(repo_dir.resolve())]


def test_repo_add_no_remote_still_needs_tracker(isolated_config, tmp_path):
    r = _invoke("repo", "add", "--name", "p", "--path", str(tmp_path))
    assert r.exit_code == 2
    assert "--tracker" in r.output
    # Failure path leaves no half-registered repo (review catch).
    assert store.load_config().get("repos", {}) == {}


def test_repo_list_shows_tracker(isolated_config, tmp_path):
    _invoke("repo", "add", "--name", "p", "--path", str(tmp_path),
            "--tracker", "IPG")
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0, r.output
    items = json.loads(r.output)
    assert items[0]["trackers"] == "jira:IPG"


def test_repo_list_prefers_host_scoped_tracker(isolated_config, tmp_path):
    """A repo under both jira:PREFIX and its remote-derived host id shows
    the host-scoped one (the harness symptom: jira:IPG won over
    github:ynsr/harness by first-write order)."""
    from workagent import store
    cfg = store.load_config()
    cfg["repos"] = {"h": {"path": str(tmp_path)}}
    cfg["trackers"] = {"jira:IPG": {"repos": [str(tmp_path)]},
                       "github:o/r": {"repos": [str(tmp_path)]}}
    store.save_config(cfg)
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)[0]["trackers"] == "github:o/r"


def test_run_harness_lock_blocked_spawns_nothing(monkeypatch):
    """A live record on the same worktree refuses the launch, spawn-free."""
    monkeypatch.setattr(cli.store, "record_harness_run",
                        lambda k, h, wt="": {"harness": "omp", "pid": 4242,
                                            "worktree": wt})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    result = {"command": "sync"}
    import typer as _typer
    with __import__("pytest").raises(_typer.Exit):
        cli._run_harness("omp", "prompt", "/tmp/wt", "/tmp", True, False,
                         result, False, run_key="jira:X")
    assert launched == []


def test_tracker_add_list_remove_roundtrip(isolated_config):
    """tracker add persists vendor/remote_url; list shows the count; remove cascades."""
    from workagent import store_sqlite as _sq
    _sq.init_db(_sq.db_path())
    r = _invoke("tracker", "add", "IPG", "--json")
    assert r.exit_code == 0, r.output
    row = json.loads(r.stdout)
    assert row["key"] == "jira:IPG" and row["vendor"] == "jira"
    r = _invoke("tracker", "list", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)[0]["repos"] == 0
    r = _invoke("tracker", "remove", "IPG", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == {"removed": "jira:IPG"}


def test_tracker_add_sets_custom_vendor(isolated_config):
    """tracker add --vendor/--remote-url beats key derivation and survives ensures."""
    from workagent import store_sqlite as sq
    sq.init_db(sq.db_path())
    r = _invoke("tracker", "add", "IPG", "--vendor", "Custom",
                "--remote-url", "https://x", "--json")
    assert r.exit_code == 0, r.output
    db = sq.db_path()
    sq.add_tracker_repo(db, "jira:IPG", "/r")
    rows = sq.load_tracker_rows(db)
    assert (rows[0]["vendor"], rows[0]["remote_url"]) == ("Custom", "https://x")
