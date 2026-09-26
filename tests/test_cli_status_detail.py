"""CLI status-detail tests (split from test_cli.py)."""

from __future__ import annotations

import json
import subprocess
import os
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _link_session

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


def test_status_base_follows_mr_target_branch(isolated_config, tmp_path, monkeypatch):
    """Cached base disagrees with the MR target (main vs develop) → cache is
    stale; counts recompute against the target branch and the cache heals."""
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    monkeypatch.setattr(cli, "_git_tip", lambda wt, ref: "a1" if ref == "HEAD" else None)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "develop"},
                          tool="glab", base_branch="main",
                          branch_tip="a1", base_tip=None, behind=0, ahead=0)
    ab_calls = []
    monkeypatch.setattr(cli.repos, "ahead_behind",
                        lambda wt, db, branch="": ab_calls.append(db) or {"behind": 0, "ahead": 3})
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:IPG-929"]["commits"] == "0|3"
    assert ab_calls == ["develop"]
    assert store.load_pr_cache()["feat/IPG-929--x"]["base_branch"] == "develop"
    # healed cache: base now agrees with the MR target → served from cache
    ab_calls.clear()
    data = json.loads(_invoke("status", "--json").stdout)
    assert data["jira:IPG-929"]["commits"] == "0|3"
    assert ab_calls == []
