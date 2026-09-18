"""CLI-level tests via typer.testing.CliRunner (subprocesses stubbed out)."""

from __future__ import annotations

import json
import os
import subprocess
import types
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
    assert json.loads(r.output)["registered"] == "proj"
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0
    assert json.loads(r.output)[0]["name"] == "proj"
    r = _invoke("repo", "remove", "proj", "--json")
    assert r.exit_code == 0
    assert json.loads(r.output) == {"removed": "proj"}


def test_start_dry_run(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd, depth=7: repo_dir)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd: None)
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add login", "body": "Details here"})
    r = _invoke("start", "o/r#22", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["dry_run"] is True
    assert out["repo"] == str(repo_dir)
    assert out["issue"] == {"title": "Add login"}
    # Dry run must not record links.
    assert store.load_links() == {}


def test_review_dry_run(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd, depth=7: repo_dir)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed: {"head_ref": ""})
    r = _invoke("review", "https://github.com/o/r/pull/33", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
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
    out = json.loads(r.output)
    assert out["dry_run"] is True and out["branch"] == "feat/22-x"


def test_status_empty(isolated_config):
    r = _invoke("status", "--json")
    assert r.exit_code == 0
    assert json.loads(r.output) == {}


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
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd, depth=7: repo_dir)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd: None)
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
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd, depth=7: repo_dir)
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
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd, depth=7: repo_dir)
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
    worktree.mkdir()
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd, depth=7: repo_dir)
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
