"""Shared helpers for split CLI tests (NOT collected: no test_ defs here)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typer.testing import CliRunner

from workagent import cli, store

runner = CliRunner()


def _invoke(*args):
    return runner.invoke(cli.app, list(args))


def _link_session(repo_dir, wt_dir, monkeypatch):
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    store.record_link("jira:IPG-929", {"issue": "IPG-929", "worktree": str(wt_dir),
                                       "branch": "feat/IPG-929--x", "repo": str(repo_dir)})


def _sync_mocks(monkeypatch, local_calls=None, rebase_calls=None, pulled=None):
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    if local_calls is not None:
        monkeypatch.setattr(cli.sync_mod, "local_merge",
                            lambda wt, db, branch="": (local_calls.append((str(wt), db))
                                            or {"status": "merged", "conflicts": []}))
    if rebase_calls is not None:
        monkeypatch.setattr(cli.sync_mod, "rebase_remote",
                            lambda tool, pr, wt: rebase_calls.append((tool, pr["number"], str(wt))))
    monkeypatch.setattr(cli.sync_mod, "pull_rebased",
                        lambda wt, br: (pulled.append((str(wt), br))
                                        if pulled is not None else None) or "reset")
    monkeypatch.setattr(cli.sync_mod, "push", lambda wt, br: None)


def _start_mocks(monkeypatch, repo_dir):
    """Stub repo/issue lookups for `start` (no subprocesses, no network)."""
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add login", "body": "Details here"})
    # Hermetic regardless of where pytest runs: pin the cwd seam so a
    # feature-branch checkout can't flip start into cwd_mode (which takes
    # the branch= path and never passes link=/issue=/slug=).
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd=None: None)
    monkeypatch.setattr(cli.repos, "worktree_branch", lambda path: None)


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
