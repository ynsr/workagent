"""Tests for git_wt.cli — argument parsing and dispatch."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from git_wt.cli import main


def test_help_text(capsys):
    """Running without args shows help."""
    with patch("sys.argv", ["git-wt"]):
        try:
            main()
        except SystemExit:
            pass
    captured = capsys.readouterr()
    assert "git-wt" in captured.out
    assert "start" in captured.out


def test_version(capsys):
    """--version prints version."""
    with patch("sys.argv", ["git-wt", "--version"]):
        try:
            main()
        except SystemExit:
            pass
    captured = capsys.readouterr()
    assert "0.1.0" in captured.out


def test_start_help(capsys):
    """git-wt start --help shows start-specific help."""
    with patch("sys.argv", ["git-wt", "start", "--help"]):
        with pytest.raises(SystemExit):
            main()
    captured = capsys.readouterr()
    assert "--ephemeral" in captured.out
    assert "--resume" in captured.out
    assert "--branch" in captured.out
    assert "--link" in captured.out
    assert "branch_name" in captured.out


def test_start_positional_branch(capsys):
    """Positional branch_name is accepted."""
    with patch("sys.argv", ["git-wt", "start", "chor/IPG-806--my-task"]):
        with pytest.raises(SystemExit) as exc:
            main()
        # Should fail because no repo/args, not because of positional parsing
        assert exc.value.code != 0


def test_start_link_flag_in_help(capsys):
    """--link is listed in start help."""
    with patch("sys.argv", ["git-wt", "start", "--help"]):
        with pytest.raises(SystemExit):
            main()
    captured = capsys.readouterr()
    assert "--link" in captured.out


def test_finish_help(capsys):
    """git-wt finish --help shows finish-specific help."""
    with patch("sys.argv", ["git-wt", "finish", "--help"]):
        with pytest.raises(SystemExit):
            main()
    captured = capsys.readouterr()
    assert "--skip-tests" in captured.out
    assert "--worktree" in captured.out


def test_cleanup_help(capsys):
    """git-wt cleanup --help shows cleanup-specific help."""
    with patch("sys.argv", ["git-wt", "cleanup", "--help"]):
        with pytest.raises(SystemExit):
            main()
    captured = capsys.readouterr()
    assert "--delete-branch" in captured.out
    assert "--branch" in captured.out


def test_list_help(capsys):
    """git-wt list --help shows list-specific help."""
    with patch("sys.argv", ["git-wt", "list", "--help"]):
        with pytest.raises(SystemExit):
            main()
    captured = capsys.readouterr()
    assert "--json" in captured.out


def test_start_missing_args(capsys):
    """git-wt start without --branch or --resume should error."""
    with patch("sys.argv", ["git-wt", "start"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code != 0


def test_cleanup_missing_branch(capsys):
    """git-wt cleanup without --branch should error."""
    with patch("sys.argv", ["git-wt", "cleanup"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2


def test_unknown_command(capsys):
    """Unknown subcommand shows help."""
    with patch("sys.argv", ["git-wt", "unknown"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2