"""CLI-level tests via typer.testing.CliRunner (subprocesses stubbed out)."""

from __future__ import annotations

import json
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from typer.testing import CliRunner

from harness import cli, store

runner = CliRunner()


def _invoke(*args):
    return runner.invoke(cli.app, list(args))


def test_repo_add_list_remove(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = _invoke("repo", "add", "--name", "proj", "--path", str(repo_dir), "--json")
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
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed: {"head_ref": ""})
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


def test_link_list_sessions_enriched(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db: None)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("link", "list", "--json")
    data = json.loads(r.stdout)
    assert data["sessions"]["jira:IPG-929"]["pr"] == "-"


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
    from harness import doctor as _doctor
    monkeypatch.setenv("HOME", str(tmp_path))
    r = _invoke("doctor")
    assert r.exit_code == 1
    assert "doctor: missing" in r.output
    receipt = tmp_path / ".local/share/harness/install-receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"source_hash": _doctor.source_hash()}))
    r = _invoke("doctor")
    assert r.exit_code == 0
    assert "doctor: ok" in r.output


def test_repo_list_csv(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _invoke("repo", "add", "--name", "proj", "--path", str(repo_dir))
    r = _invoke("repo", "list", "--csv")
    assert r.exit_code == 0
    lines = [l for l in r.output.strip().splitlines() if l]
    assert lines[0] == "name,path"
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


def test_version(isolated_config):
    r = _invoke("--version")
    assert r.exit_code == 0
    assert "harness" in r.output


def test_help_shows_examples_and_exit_codes(isolated_config):
    r = _invoke("--help")
    assert r.exit_code == 0
    assert "Exit codes" in r.output
    assert "harness start" in r.output
def _start_mocks(monkeypatch, repo_dir):
    """Stub repo/issue lookups for `start` (no subprocesses, no network)."""
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add login", "body": "Details here"})


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


def test_start_no_harness_prints_command_and_skips_launch(isolated_config, tmp_path, monkeypatch):
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
    r = runner.invoke(cli.app, ["start", "o/r#22", "--no-harness", "--json"])
    assert r.exit_code == 0, r.output
    assert launched == []  # harness must not run
    out = json.loads(r.stdout)
    assert out["worktree_path"] == str(worktree)
    assert out["harness_command"].startswith("omp ")
    assert "harness command: omp" in r.stderr
    assert str(worktree) in r.stderr


def test_start_no_harness_tty_lands_shell_in_worktree(isolated_config, tmp_path, monkeypatch, capsys):
    """TTY --no-harness replaces the process with the user's shell in the worktree."""
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
                  no_tty=False, no_harness=True, dry_run=False, yes=False, json_output=False)
    assert excinfo.value.args[0] == "/bin/bash"
    assert excinfo.value.args[2] == str(worktree)
    captured = capsys.readouterr()
    assert "harness command: omp" in captured.err
    assert f"worktree_path: {worktree}" in captured.out


def test_review_no_harness_prints_command_and_skips_launch(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["review", "https://github.com/o/r/pull/33",
                                "--no-harness", "--json"])
    assert r.exit_code == 0, r.output
    assert launched == []  # harness must not run
    out = json.loads(r.stdout)
    assert out["worktree_path"] == str(worktree)
    assert out["harness_command"].startswith("omp ")
    assert "harness command: omp" in r.stderr
    assert str(worktree) in r.stderr


def test_review_no_harness_tty_lands_shell_in_worktree(isolated_config, tmp_path, monkeypatch, capsys):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed: {"head_ref": "feat/33"})
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
                   no_tty=False, no_harness=True, dry_run=False, yes=False, json_output=False)
    assert excinfo.value.args[0] == "/bin/zsh"
    assert excinfo.value.args[2] == str(worktree)


def test_no_harness_shorthand_N_on_start_and_review(isolated_config, tmp_path, monkeypatch):
    """`-N` is accepted as shorthand for --no-harness on both subcommands."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue", lambda ref: {"title": "t", "body": "b"})
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
    assert json.loads(r.stdout)["harness_command"].startswith("omp ")
    assert json.loads(r2.stdout)["harness_command"].startswith("omp ")


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
    from harness import completions as c
    w = c.cd_wrapper("harness", "bash")
    assert w.startswith("harness-cd()") and 'cd "$(command harness cd' in w
    assert "function harness-cd" in c.cd_wrapper("harness", "fish")
    snip = c.install_snippet("harness", "bash")
    assert "harness-cd()" in snip


def test_sync_rebase_failure_falls_back_to_local_merge(
        isolated_config, tmp_path, monkeypatch):
    from harness.errors import HarnessError
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
