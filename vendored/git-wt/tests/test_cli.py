"""Tests for git_wt.cli — Typer commands, flags, output, exit codes."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from git_wt import __version__
from git_wt.cli import _normalize_bare_resume, app, _RESUME_PICK


# ── help & version ───────────────────────────────────────────────────


def test_help_text(runner: CliRunner):
    """--help shows Rich help with a one-liner, commands, Example, exit codes."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout
    assert "worktrees" in out
    assert "start" in out and "finish" in out and "cleanup" in out and "list" in out
    assert "Example" in out
    assert "Exit codes" in out


def test_bare_invocation_shows_help(runner: CliRunner):
    """No args → help (usage error exit), never a traceback."""
    result = runner.invoke(app, [])
    assert "worktrees" in result.stdout


def test_version(runner: CliRunner):
    """--version prints 'git-wt <version>' and exits 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"git-wt {__version__}" in result.stdout


def test_version_flag_position_independent(runner: CliRunner):
    """--version works before and after the subcommand name."""
    assert runner.invoke(app, ["--version", "list"]).exit_code == 0
    assert runner.invoke(app, ["list", "--version"]).exit_code == 0


# ── per-command help ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cmd", "needle"),
    [
        ("start", "--link"),
        ("start", "--resume"),
        ("start", "--on-dirty"),
        ("finish", "--worktree"),
        ("cleanup", "--branch"),
        ("list", "--csv"),
    ],
)
def test_command_help_lists_flags(runner: CliRunner, cmd: str, needle: str):
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0
    assert needle in result.stdout


# ── global flags: before and after the subcommand ────────────────────


def test_flags_accepted_before_subcommand(runner: CliRunner, tmp_repo, monkeypatch):
    """-v/--json work in the pre-subcommand position."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["-v", "--json", "list"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)


def test_flags_accepted_after_subcommand(runner: CliRunner, tmp_repo, monkeypatch):
    """-v/--json work in the post-subcommand position (argparse parity)."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["list", "-v", "--json"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)


def test_verbose_start_detail_on_stderr(runner: CliRunner, tmp_repo, monkeypatch):
    """-v start puts extra detail on stderr; stdout stays data-only."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["-v", "start", "--branch", "feat/1--verbose"])
    assert result.exit_code == 0, result.stderr
    assert "action" in result.stderr
    assert "Created worktree" in result.stdout
    assert "cd " in result.stdout


# ── start ────────────────────────────────────────────────────────────


def test_start_positional_branch(runner: CliRunner, tmp_repo, monkeypatch):
    """Positional branch name creates the worktree (integration)."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["start", "feat/7--positional"])
    assert result.exit_code == 0, result.stderr
    assert "Created worktree" in result.stdout
    assert "cd " in result.stdout


def test_start_missing_args_exit_2(runner: CliRunner, tmp_repo, monkeypatch):
    """No branch/type-issue-slug/resume → usage error with actionable message."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 2
    assert "--branch" in result.stderr


def test_start_resume_bare_sentinel(runner: CliRunner, tmp_repo, monkeypatch):
    """Normalized bare --resume hits the interactive picker (non-TTY → exit 2)."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["start", f"--resume={_RESUME_PICK}"])
    assert result.exit_code == 2
    assert "--resume" in result.stderr


def test_normalize_bare_resume():
    """argparse-exact semantics: a '-'-prefixed next token is not consumed."""
    assert _normalize_bare_resume(["start", "--resume"]) == ["start", f"--resume={_RESUME_PICK}"]
    assert _normalize_bare_resume(["start", "--resume", "--ephemeral"]) == [
        "start",
        f"--resume={_RESUME_PICK}",
        "--ephemeral",
    ]
    assert _normalize_bare_resume(["start", "--resume", "feat/x"]) == [
        "start",
        "--resume",
        "feat/x",
    ]


def test_start_resume_existing(runner: CliRunner, tmp_repo, monkeypatch):
    """--resume <branch> resumes an existing worktree (integration)."""
    monkeypatch.chdir(tmp_repo)
    first = runner.invoke(app, ["start", "--branch", "feat/9--resume-me"])
    assert first.exit_code == 0, first.stderr
    second = runner.invoke(app, ["start", "--resume", "feat/9--resume-me"])
    assert second.exit_code == 0, second.stderr
    assert "Resumed worktree" in second.stdout


def test_start_invalid_on_dirty_exit_2(runner: CliRunner, tmp_repo, monkeypatch):
    """Unknown --on-dirty value is a usage error (exit 2)."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["start", "--branch", "feat/1--x", "--on-dirty", "bogus"])
    assert result.exit_code == 2


# ── list ─────────────────────────────────────────────────────────────


def test_list_json_shape(runner: CliRunner, tmp_repo, monkeypatch):
    """--json prints a worktree list of dicts (branch + path) on stdout."""
    monkeypatch.chdir(tmp_repo)
    assert runner.invoke(app, ["start", "--branch", "feat/2--lister"]).exit_code == 0
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    branches = {wt["branch"] for wt in payload}
    assert "feat/2--lister" in branches
    assert all("path" in wt for wt in payload)


def test_list_csv_header_and_rows(runner: CliRunner, tmp_repo, monkeypatch):
    """--csv prints a header row then one row per worktree."""
    monkeypatch.chdir(tmp_repo)
    assert runner.invoke(app, ["start", "--branch", "feat/3--csv"]).exit_code == 0
    result = runner.invoke(app, ["list", "--csv"])
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "branch,path"
    assert any(line.startswith("feat/3--csv,") for line in lines[1:])


def test_list_table_is_pretty_not_csv(runner: CliRunner, tmp_repo, monkeypatch):
    """Default output is the Rich table (human-first), not CSV."""
    monkeypatch.chdir(tmp_repo)
    assert runner.invoke(app, ["start", "--branch", "feat/4--pretty"]).exit_code == 0
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.stderr
    assert "branch,path" not in result.stdout
    assert "feat/4--pretty" in result.stdout


def test_list_empty_repo_json(runner: CliRunner, tmp_repo, monkeypatch):
    """Fresh repo (no worktrees beyond main) still prints valid JSON/CSV."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0, result.stderr
    assert isinstance(json.loads(result.stdout), list)


# ── finish / cleanup ─────────────────────────────────────────────────


def test_finish_missing_worktree_non_tty_exit_2(runner: CliRunner, tmp_repo, monkeypatch):
    """Non-TTY without --worktree fails with the actionable flag name (exit 2)."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["finish"])
    assert result.exit_code == 2
    assert "--worktree" in result.stderr


def test_cleanup_missing_branch_non_tty_exit_2(runner: CliRunner, tmp_repo, monkeypatch):
    """Non-TTY without --branch fails with the actionable flag name (exit 2)."""
    monkeypatch.chdir(tmp_repo)
    result = runner.invoke(app, ["cleanup"])
    assert result.exit_code == 2
    assert "--branch" in result.stderr


def test_cleanup_removed_worktree(runner: CliRunner, tmp_repo, monkeypatch):
    """cleanup --branch removes the worktree; --json reports actions (integration)."""
    monkeypatch.chdir(tmp_repo)
    assert runner.invoke(app, ["start", "--branch", "feat/5--clean"]).exit_code == 0
    result = runner.invoke(app, ["cleanup", "--branch", "feat/5--clean", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert any("removed worktree" in a for a in payload["actions"])


def test_unknown_command_exit_2(runner: CliRunner):
    """Unknown subcommand → usage error."""
    result = runner.invoke(app, ["bogus"])
    assert result.exit_code == 2


def test_unknown_flag_exit_2(runner: CliRunner):
    """Unknown flag → usage error, no traceback."""
    result = runner.invoke(app, ["list", "--bogus"])
    assert result.exit_code == 2
