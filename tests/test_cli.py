"""CLI-level tests via typer.testing.CliRunner (subprocesses stubbed out)."""

from __future__ import annotations

import json
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
