"""CLI review (single) tests (split from test_cli.py)."""

from __future__ import annotations

import json
import types
import os
import subprocess
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _init_repo, _add_wt, _start_mocks

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
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
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
                   all_wts=False, sequential=False, fix=False, force_all=False, post_comments=False)
    assert excinfo.value.args[0] == "/bin/zsh"
    assert excinfo.value.args[2] == str(worktree)


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
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    r = runner.invoke(cli.app, ["review", "feat/IPG-929--x", "--no-tty", "--no-runtime", "--json"])
    assert r.exit_code == 0, r.output
    assert started == []  # no new worktree created
    assert json.loads(r.stdout)["worktree_path"] == str(wt)


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
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    url = "https://github.com/o/r/pull/33"
    key = "feat/33"

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
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
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


def test_review_new_pr_keys_row_by_branch(isolated_config, tmp_path, monkeypatch):
    """A fresh PR head branch is recorded under the branch name with pr_url."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "branch_tip", lambda wt: "abc123")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/77"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/77"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    url = "https://github.com/o/r/pull/77"
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r.exit_code == 0, r.output
    links = store.load_links()
    assert set(links) == {"feat/77"}
    assert links["feat/77"]["pr_url"] == url
    assert links["feat/77"]["reviewed"] is True
