"""CLI status-table tests (split from test_cli.py)."""

from __future__ import annotations

import json
import subprocess
import csv
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _link_session

def test_status_empty(isolated_config):
    r = _invoke("status", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout) == {}


def test_status_table_shows_behind_ahead(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind",
                        lambda wt, db, branch="": {"behind": 5, "ahead": 8})
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
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    r = _invoke("status", "--json")
    data = json.loads(r.stdout)
    assert data["jira:IPG-929"]["pr"] == "PR #123 (merged)"


def test_status_pr_live_query_then_cache(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
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
                        lambda wt, db, branch="": ab_calls.append(db)
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
                        lambda wt, db, branch="": {"behind": 7, "ahead": 0})
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
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    fetches = []
    monkeypatch.setattr(cli.refs, "fetch_ci_status",
                        lambda tool, url, cwd=None: fetches.append(url)
                        or "success")
    monkeypatch.setattr(cli.refs, "fetch_pr_comment_stats",
                        lambda tool, url, cwd=None: {"reviews": 0, "unresolved": 0, "resolved": 0})
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
    fetches = []
    monkeypatch.setattr(cli.refs, "fetch_ci_status",
                        lambda tool, url, cwd=None: fetches.append(url)
                        or "failure")
    monkeypatch.setattr(cli.refs, "fetch_pr_comment_stats",
                        lambda tool, url, cwd=None: {"reviews": 0, "unresolved": 0, "resolved": 0})
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
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
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
    monkeypatch.setattr(cli.refs, "fetch_pr_comment_stats",
                        lambda tool, url, cwd=None: {"reviews": 1, "unresolved": 0, "resolved": 1})
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


def test_status_reviews_column_and_force_all(isolated_config, tmp_path, monkeypatch):
    """Issue #28: reviews R|U|R column rides rows/JSON; --force-all re-includes."""
    from workagent import store as _store
    a = tmp_path / "a"; a.mkdir(); (a / ".git").mkdir()
    b = tmp_path / "b"; b.mkdir(); (b / ".git").mkdir()
    pr = "https://github.com/o/r/pull/1"
    _store.record_link("jira:A-1", {"branch": "feat/a", "worktree": str(a),
                                    "repo": str(tmp_path), "pr_url": pr})
    _store.record_link("jira:B-2", {"branch": "feat/b", "worktree": str(b),
                                    "repo": str(tmp_path), "pr_url": pr})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    monkeypatch.setattr(cli.refs, "fetch_pr_list_for_branch",
                        lambda tool, branch, cwd=None: [])
    monkeypatch.setattr(cli, "_repo_default_branch", lambda repo: None)
    _store.cache_pr_status("feat/a", {"number": 1, "state": "open", "url": pr},
                           tool="gh", base_branch="main", branch_tip="a1",
                           base_tip="b1", behind=0, ahead=1)
    _store.cache_review_stats("feat/a", {"reviews": 2, "unresolved": 1, "resolved": 3},
                              sha="a1")
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: "a1" if ref == "HEAD" else None)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    monkeypatch.setattr(cli.refs, "fetch_ci_status", lambda tool, url, cwd=None: None)
    monkeypatch.setattr(cli.refs, "fetch_pr_comment_stats",
                        lambda tool, url, cwd=None: {"reviews": 0, "unresolved": 0, "resolved": 0})
    rows, columns = cli._session_rows(_store.load_links(), False, False)
    assert columns[-1] == "reviews"
    assert rows[0]["reviews"] == "2|1|3"
    keys = [k for k, _ in cli._reviewable_keys(_store.load_links())]
    assert "jira:B-2" in keys and "jira:A-1" not in keys  # unresolved skip
    forced = [k for k, _ in cli._reviewable_keys(_store.load_links(), force=True)]
    assert "jira:A-1" in forced  # --force-all re-includes
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:A-1"]["reviews"] == "2|1|3"
    assert data["jira:A-1"]["reviews_detail"] == {"reviews": 2, "unresolved": 1, "resolved": 3}


def test_status_json_detail_carries_ci(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
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
    monkeypatch.setattr(cli.refs, "fetch_pr_comment_stats",
                        lambda tool, url, cwd=None: {"reviews": 1, "unresolved": 1, "resolved": 0})
    data = json.loads(_invoke("status", "IPG-929", "--json").stdout)
    assert data["ci"] == "running"
    enriched = json.loads(_invoke("status", "--json").stdout)
    assert enriched["jira:IPG-929"]["ci"] == "running"


def test_status_ref_detail(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind",
                        lambda wt, db, branch="": {"behind": 1, "ahead": 2})
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
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("link", "list", "--json")
    data = json.loads(r.stdout)
    assert "sessions" not in data
    assert data["worktrees"]["jira:IPG-929"]["pr"] == "-"
