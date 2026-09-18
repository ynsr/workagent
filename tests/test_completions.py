"""Completions tests: show per shell, install idempotency, repo-name callback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli, completions, store

runner = CliRunner()


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_show_nonempty_per_shell(shell):
    r = runner.invoke(cli.app, ["completions", "show", shell])
    assert r.exit_code == 0
    assert r.output.strip()
    assert "harness" in r.output


def test_show_unknown_shell_exits_2():
    r = runner.invoke(cli.app, ["completions", "show", "powershell"])
    assert r.exit_code == 2
    assert "choose from: bash, zsh, fish" in r.output


def test_install_preserves_rc_and_bak(isolated_config, tmp_path):
    rc = tmp_path / "rc"
    rc.write_text("# my config\nexport FOO=1\n", encoding="utf-8")
    r = runner.invoke(cli.app, ["completions", "install", "bash", "--rcfile", str(rc), "--yes"])
    assert r.exit_code == 0, r.output
    text = rc.read_text(encoding="utf-8")
    assert "export FOO=1" in text
    assert completions.START_MARKER.format(prog="harness") in text
    assert Path(str(rc) + ".bak").read_text(encoding="utf-8") == "# my config\nexport FOO=1\n"


def test_install_second_run_byte_identical(isolated_config, tmp_path):
    rc = tmp_path / "rc"
    rc.write_text("", encoding="utf-8")
    assert runner.invoke(cli.app, ["completions", "install", "zsh", "--rcfile", str(rc), "--yes"]).exit_code == 0
    first = rc.read_bytes()
    r = runner.invoke(cli.app, ["completions", "install", "zsh", "--rcfile", str(rc), "--yes"])
    assert r.exit_code == 0
    assert rc.read_bytes() == first


def test_install_replaces_stale_block(isolated_config, tmp_path):
    rc = tmp_path / "rc"
    rc.write_text("keep\n# >>> harness completion >>>\nold-eval-line\n# <<< harness completion <<<\n", encoding="utf-8")
    r = runner.invoke(cli.app, ["completions", "install", "bash", "--rcfile", str(rc), "--yes"])
    assert r.exit_code == 0, r.output
    text = rc.read_text(encoding="utf-8")
    assert text.startswith("keep\n")
    assert "old-eval-line" not in text
    assert text.count(completions.START_MARKER.format(prog="harness")) == 1


def test_repo_names_callback_filters_and_tolerates_missing(isolated_config):
    store.record_link("github:o/r#1", {})  # touch config dir
    cfg = store.load_config()
    cfg["repos"] = {"projectx": {"path": "/tmp/x"}, "media-db": {"path": "/tmp/y"}}
    store.save_config(cfg)
    cb = completions.complete_names(lambda: list(cfg["repos"]))
    assert cb(None, "pro") == ["projectx"]
    assert cb(None, "") == ["media-db", "projectx"]
    assert cb(None, "zzz") == []


def test_repo_names_callback_missing_store_returns_empty(tmp_path, monkeypatch):
    from harness import store as _store
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "absent"))
    names = cli.repos.repo_names()
    assert names == []
    cb = completions.complete_names(cli.repos.repo_names)
    assert cb(None, "pro") == []
