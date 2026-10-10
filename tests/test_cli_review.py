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
    r = runner.invoke(cli.app, ["review", "o/r#22", "--dry-run", "--launch", "--json"])
    assert r.exit_code == 2
    assert "needs a PR/MR URL" in r.output


def test_review_preview_prints_command_and_skips_launch(isolated_config, tmp_path, monkeypatch):
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
                                "--json"])
    assert r.exit_code == 0, r.output
    assert launched == []  # harness must not run
    out = json.loads(r.stdout)
    assert out["worktree_path"] == str(worktree)
    assert out["harness_command"].startswith(f"cd {worktree} && omp ")
    assert "harness command: cd " in r.stderr
    assert str(worktree) in r.stderr


def test_review_preview_persists_session_row(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    store_sqlite.init_db(store_sqlite.db_path())
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
                                "--json"])
    assert r.exit_code == 0, r.output
    assert launched == []
    rows = store_sqlite.list_sessions(store_sqlite.db_path())
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == "preview"
    assert row["session_type"] == "review"
    assert "/sessions/omp/feat/33/review/" in row["file_path"]
    assert row["worktree_ref"] == "feat/33"
    assert "https://github.com/o/r/pull/33" in row["prompt"]


def test_review_preview_tty_lands_shell_in_worktree(isolated_config, tmp_path, monkeypatch, capsys):
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
                   no_tty=False, launch=False, dry_run=False, yes=False, json_output=False,
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
    r = runner.invoke(cli.app, ["review", "feat/IPG-929--x", "--no-tty", "--launch", "--json"])
    assert r.exit_code == 0, r.output
    assert started == []  # no new worktree created
    assert json.loads(r.stdout)["worktree_path"] == str(wt)


def test_review_without_ref_is_usage_error(isolated_config):
    """Bare `workagent review` (no ref, no --all) is a usage error, not a crash."""
    r = runner.invoke(cli.app, ["review"])
    assert r.exit_code == 2, r.output
    assert "missing PR/MR ref" in r.stderr
    assert "use --all" in r.stderr


def test_review_new_fix_session_requires_fix_comments(isolated_config):
    r = runner.invoke(cli.app, ["review", "o/r#1", "--new-fix-session"])
    assert r.exit_code == 2, r.output
    assert "--fix-comments" in r.stderr


def test_run_harness_derives_fix_comments_type(isolated_config, tmp_path, monkeypatch):
    """_run_harness with command=review + fix marker inserts fix_comments type."""
    from workagent import cli_harness as _h, store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k', '/tmp/w', 'b', NULL, '2026-01-01', '{}')")
    monkeypatch.setattr(_h.store, "record_harness_run", lambda *a: None)
    monkeypatch.setattr(_h.store, "clear_harness_run", lambda *a: None)
    monkeypatch.setattr(_h.backend, "launch", lambda *a, **k: None)
    result = {"command": "review", "fix_comments": True}
    _h._run_harness("omp", "Fix all open (not-resolved) review comments on this PR/MR: u",
                    "/tmp", "/tmp", True, True, result, False, run_key="k")
    rows = sq.list_sessions(db)
    assert rows and rows[0]["session_type"] == "fix_comments"


def test_review_continue_reuses_transcript(isolated_config, monkeypatch):
    """Continue path resolves the latest finished review session for the key."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k', '/tmp/w', 'b', NULL, '2026-01-01', '{}')")
    sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                      prompt="p", file_path="/s/old.jsonl", session_id="old-9",
                      session_type="review")
    sq.finish_session(db, "old-9", "finished")
    got = sq.latest_review_session(db, "k")
    assert got and got["file_path"] == "/s/old.jsonl"

def test_fix_comments_explicit_file_adopts_review_reuse(isolated_config):
    """Explicit review transcript path adopts the reuse (no fix_comments rescope)."""
    from workagent import cli_review as _r, store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k', '/tmp/w', 'b', NULL, '2026-01-01', '{}')")
    review_path = str(sq.session_file_path("rev-1896", "omp", branch="feat/x", session_type="review"))
    sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                      prompt="p", file_path=review_path, session_id="rev-1896",
                      session_type="review")
    sq.finish_session(db, "rev-1896", "finished")
    result: dict = {}
    _r._apply_fix_session(result, "k", True, False, review_path)
    assert result["reuse_session_id"] == "rev-1896"
    assert result["session_file"] == review_path
    assert "/review/" in result["session_file"]
    assert "/fix_comments/" not in result["session_file"]


def test_fix_comments_preview_adopts_existing_session(isolated_config):
    """Preview rerun on a recorded transcript adopts the row (no PK warning)."""
    from workagent import cli_harness as _h, store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k', '/tmp/w', 'b', NULL, '2026-01-01', '{}')")
    sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                      prompt="p", file_path="/s/rev-1896.jsonl", session_id="rev-1896",
                      session_type="review")
    sq.finish_session(db, "rev-1896", "finished")
    sid = _h.record_preview_session("k", "review", "p", "omp",
                                    "harness command: cd /tmp/w && omp 'x'",
                                    session_file="/s/rev-1896.jsonl")
    assert sid == "rev-1896"
    assert sq.get_session(db, "rev-1896")["state"] == "finished"
    assert len(sq.list_sessions(db)) == 1


def test_fix_comments_launch_keeps_review_path(isolated_config, monkeypatch):
    """_run_harness with a reuse id never rescopes the review path to fix_comments/."""
    from workagent import cli_harness as _h, store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k', '/tmp/w', 'b', NULL, '2026-01-01', '{}')")
    review_path = str(sq.session_file_path("rev-1896", "omp", branch="feat/x", session_type="review"))
    sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                      prompt="p", file_path=review_path, session_id="rev-1896",
                      session_type="review")
    sq.finish_session(db, "rev-1896", "finished")
    monkeypatch.setattr(_h.store, "record_harness_run", lambda *a: None)
    monkeypatch.setattr(_h.store, "clear_harness_run", lambda *a: None)
    monkeypatch.setattr(_h.backend, "launch", lambda *a, **k: None)
    result = {"command": "review", "fix_comments": True, "branch": "feat/x",
              "reuse_session_id": "rev-1896", "session_file": review_path}
    _h._run_harness("omp", "Fix all open (not-resolved) review comments on this PR/MR: u",
                    "/tmp", "/tmp", True, True, result, False, run_key="k",
                    session_file=review_path)
    assert result["session_file"] == review_path
    assert sq.get_session(db, "rev-1896")["file_path"] == review_path


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

    # Preview (no --launch) starts nothing: must not mark reviewed (Task 2 precedent).
    r0 = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r0.exit_code == 0, r0.output
    assert launched == []
    assert store.load_links()[key].get("reviewed") is None

    # Fresh-worktree launch path: marks reviewed with the worktree tip.
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--launch", "--json"])
    assert r.exit_code == 0, r.output
    assert launched != []
    entry = store.load_links()[key]
    assert entry["reviewed"] is True
    assert entry["reviewed_at"] == "abc123"

    # Reuse-worktree launch path: marks reviewed as well (idempotent re-mark).
    r2 = runner.invoke(cli.app, ["review", url, "--no-tty", "--launch", "--json"])
    assert r2.exit_code == 0, r2.output
    entry2 = store.load_links()[key]
    assert entry2["reviewed"] is True
    assert entry2["reviewed_at"] == "abc123"


def test_review_reuses_existing_worktree_row(isolated_config, tmp_path, monkeypatch):
    """Reviewing an already-tracked worktree stamps pr_url on its own row —
    no second pr:<url> row for the same path/(repo, branch) (UNIQUE
    regression on a real state.db)."""


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
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--launch", "--json"])
    assert r.exit_code == 0, r.output
    links = store.load_links()
    assert set(links) == {"feat/77"}
    assert links["feat/77"]["pr_url"] == url
    assert links["feat/77"]["reviewed"] is True


def test_review_same_branch_other_repo_creates_own_row(isolated_config, tmp_path, monkeypatch):
    """Same branch name in another repo gets its own row (IPG-1002 composite key)."""
    repo_dir = tmp_path / "projectx"
    repo_dir.mkdir()
    other_wt = tmp_path / "wt-commons"
    other_wt.mkdir()
    store.record_link("jira:IPG-1002", {
        "worktree": str(other_wt),
        "branch": "feat/IPG-1002--x",
        "repo": "/repos/ipg-commons",
        "pr_url": "https://git.example.com/server/ipg-commons/-/merge_requests/127",
    })
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/IPG-1002--x"})
    new_wt = tmp_path / "wt-projectx"
    new_wt.mkdir()
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(new_wt),
                                            "branch": "feat/IPG-1002--x"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    url = "https://git.example.com/server/projectx/-/merge_requests/1737"
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r.exit_code == 0, r.output
    links = store.load_links()
    assert links["jira:IPG-1002"]["pr_url"].endswith("/merge_requests/127")
    assert links["jira:IPG-1002"]["repo"] == "/repos/ipg-commons"
    fresh = [k for k in links if k != "jira:IPG-1002"]
    assert len(fresh) == 1
    assert links[fresh[0]]["pr_url"] == url
    assert links[fresh[0]]["repo"] == str(repo_dir)
    assert links[fresh[0]]["branch"] == "feat/IPG-1002--x"
