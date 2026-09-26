"""CLI sync tests (split from test_cli.py; engine tests live in test_sync.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _link_session, _sync_mocks, _init_repo, _add_wt

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
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
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
                        lambda wt, db, branch="": (local.append((str(wt), db))
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
                        lambda wt, db, branch="": (local.append((str(wt), db))
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


def test_sync_creates_worktree_for_new_pr_ref(isolated_config, tmp_path, monkeypatch):
    """sync <PR-url> with no linked state creates the branch worktree then syncs."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/99"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/99"})
    synced = []
    monkeypatch.setattr(cli, "_sync_one",
                        lambda k, entry, **kw: synced.append(k) or {"key": k, "result": "ok"})
    url = "https://github.com/o/r/pull/99"
    r = runner.invoke(cli.app, ["sync", url, "--json"])
    assert r.exit_code == 0, r.output
    assert synced == ["feat/99"]
    links = store.load_links()
    assert links["feat/99"]["pr_url"] == url


def test_sync_pr_ref_dry_run_creates_nothing(isolated_config, tmp_path, monkeypatch):
    """sync <PR-url> --dry-run prints the plan without creating a worktree."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/100"})

    def _no_start(repo, **kw):
        raise AssertionError("dry-run must not create a worktree")
    monkeypatch.setattr(cli.gitwt, "start_worktree", _no_start)
    url = "https://github.com/o/r/pull/100"
    r = runner.invoke(cli.app, ["sync", url, "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert store.load_links() == {}


def test_sync_missing_link_errors(isolated_config):
    r = _invoke("sync", "o/r#99", "--json")
    assert r.exit_code == 2
    assert "no linked state" in r.output
