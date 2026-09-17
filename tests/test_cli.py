"""CLI-level tests with subprocesses stubbed out."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from harness import cli, store


def _ns(**kw):
    base = {"verbose": False, "quiet": False, "json": True}
    base.update(kw)
    return Namespace(**base)


def test_repo_add_list_remove(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    out = cli._cmd_repo(_ns(repo_command="add", name="proj", path=repo_dir, tracker=None))
    assert out["registered"] == "proj"
    out = cli._cmd_repo(_ns(repo_command="list"))
    assert out[0]["name"] == "proj"
    out = cli._cmd_repo(_ns(repo_command="remove", name="proj"))
    assert out == {"removed": "proj"}


def test_start_dry_run(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd: repo_dir)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add login", "body": "Details here"})
    args = _ns(command="start", ref="o/r#22", repo=None, depth=7, base=None,
               harness=None, no_tty=False, dry_run=True, yes=True)
    out = cli._cmd_start(args)
    assert out["dry_run"] is True
    assert out["repo"] == str(repo_dir)
    assert out["issue"] == {"title": "Add login"}
    # Dry run must not record links.
    assert store.load_links() == {}


def test_review_dry_run(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd: repo_dir)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    args = _ns(command="review", ref="https://github.com/o/r/pull/33",
               repo=None, depth=7, harness=None, no_tty=True, dry_run=True, yes=True)
    out = cli._cmd_review(args)
    assert out["dry_run"] is True and out["pr_url"].endswith("/pull/33")


def test_cleanup_missing_link_errors(isolated_config):
    import pytest
    from harness.errors import HarnessError
    args = _ns(command="cleanup", ref="o/r#99", force=True, yes=True, dry_run=False)
    with pytest.raises(HarnessError):
        cli._cmd_cleanup(args)


def test_cleanup_dry_run(isolated_config):
    store.record_link("github:o/r#22", {"branch": "feat/22-x", "repo": "/tmp/proj",
                                        "worktree": "/tmp/wt"})
    args = _ns(command="cleanup", ref="o/r#22", force=False, yes=True, dry_run=True)
    out = cli._cmd_cleanup(args)
    assert out["dry_run"] is True and out["branch"] == "feat/22-x"


def test_status_empty(isolated_config):
    out = cli._cmd_status(_ns(ref=None))
    assert out == {}  # --json shape
