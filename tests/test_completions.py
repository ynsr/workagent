"""Completions tests: show per shell, install idempotency, repo-name callback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def test_runtime_completion_protocol_lists_subcommands(isolated_config):
    """Regression: the env-var completion server must work in a fresh process.

    typer >= 0.27 only registers its shell completion classes while
    building an app with add_completion=True; with add_completion=False
    the `_HARNESS_COMPLETE=complete_bash` server died with "Shell bash
    not supported." on every keystroke (ble.sh fires it constantly).
    cli.main() now registers the classes; this exercises the real
    subprocess path and asserts stderr stays empty.
    """
    env = {
        **os.environ,
        "_HARNESS_COMPLETE": "complete_bash",
        "COMP_WORDS": "harness s",
        "COMP_CWORD": "1",
    }
    r = subprocess.run(
        [sys.executable, "-c", "import sys; sys.argv = ['harness', '']; from harness.cli import main; main()"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "start" in r.stdout.split()
    assert "not supported" not in r.stderr  # doctor dev-warning allowed; server error forbidden


def test_show_eval_line_discards_server_stderr():
    """The sourced eval line must never let the server print into the shell."""
    assert "2>/dev/null" in completions.eval_line("harness", "bash")
    assert "2>/dev/null" in completions.eval_line("harness", "fish")
