"""CLI start/harness tests (split from test_cli.py)."""

from __future__ import annotations

import json
import os
import subprocess
import types
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _link_session, _start_mocks

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


def test_start_refused_while_harness_live(isolated_config, tmp_path, monkeypatch):
    """Hard guard: a worktree with a live harness refuses a second launch."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _start_mocks(monkeypatch, repo_dir)
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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('jira:IPG-929', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('jira:IPG-9', '/wt9', 'b9', 'x', '2026-01-01T00:00:00+00:00')")
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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('jira:IPG-929', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    result = {"key": "jira:IPG-929"}
    cli._run_harness("omp", "prompt", str(wt_dir), str(repo_dir), False,
                     False, result, True, run_key="jira:IPG-929")
    rows = sq.list_sessions(db)
    assert len(rows) == 1
    assert rows[0]["file_path"].endswith(rows[0]["id"] + ".jsonl")


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


def test_start_accepts_pr_ref(isolated_config, tmp_path, monkeypatch):
    """start <PR-url> creates the branch worktree and launches the harness."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"title": "T", "body": "B",
                                                  "head_ref": "feat/88"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/88"})
    monkeypatch.setattr(cli.backend, "prompt_for_issue", lambda *a, **k: "PROMPT")
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    url = "https://github.com/o/r/pull/88"
    r = runner.invoke(cli.app, ["start", url, "--no-tty", "--json"])
    assert r.exit_code == 0, r.output
    assert launched != []
    links = store.load_links()
    assert set(links) == {"feat/88"}
    assert links["feat/88"]["pr_url"] == url
