"""Tests for the omp launcher (backend.py)."""

from __future__ import annotations

import os

import pytest

from harness import backend
from harness.errors import HarnessError


class _Execed(Exception):
    """Sentinel: os.execvp was about to replace the process."""

    def __init__(self, file, argv, cwd):
        super().__init__(file)
        self.file, self.argv, self.cwd = file, argv, cwd


def _fake_omp(monkeypatch):
    monkeypatch.setattr(backend.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_launch_tty_chdirs_into_worktree(tmp_path, monkeypatch):
    """TTY exec must run in the worktree, not the caller's cwd (regression:
    harness start used to leave omp in the original directory)."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)
    _fake_omp(monkeypatch)
    monkeypatch.setattr(backend.os, "execvp",
                        lambda file, argv: (_ for _ in ()).throw(_Execed(file, argv, os.getcwd())))
    with pytest.raises(_Execed) as excinfo:
        backend.launch("omp", "do things", str(worktree), no_tty=False)
    assert excinfo.value.cwd == str(worktree)
    assert excinfo.value.argv == ["omp", "do things"]


def test_launch_no_tty_runs_child_in_worktree(tmp_path, monkeypatch):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _fake_omp(monkeypatch)
    seen = {}

    def fake_run(argv, cwd):
        seen["argv"], seen["cwd"] = argv, cwd

        class Proc:
            returncode = 0
        return Proc()

    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    assert backend.launch("omp", "do things", str(worktree), no_tty=True) == 0
    assert seen["cwd"] == str(worktree)
    assert seen["argv"] == ["omp", "-p", "--auto-approve", "do things"]


def test_launch_missing_worktree_is_actionable(tmp_path, monkeypatch):
    _fake_omp(monkeypatch)
    monkeypatch.setattr(backend.os, "execvp", lambda file, argv: None)
    with pytest.raises(HarnessError) as excinfo:
        backend.launch("omp", "p", str(tmp_path / "nope"), no_tty=False)
    assert "cannot cd into" in str(excinfo.value)


def test_command_argv_rejects_unknown_harness():
    with pytest.raises(HarnessError) as excinfo:
        backend.command_argv("claude", "p", no_tty=False)
    assert excinfo.value.exit_code == 2



def test_prompt_includes_push_target():
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/wt", branch="feat/x")
    assert "/wt" in p and "feat/x" in p
    assert "Never create or push a different branch" in p
    assert backend.prompt_for_issue("T", "B", "o/r#22").endswith("B")  # no target → unchanged
    r = backend.prompt_for_review("https://x/pull/1", worktree="/wt", branch="pr-1")
    assert "Never create or push a different branch" in r


def test_prompt_push_target_mentions_origin_branch():
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/w", branch="chore/a--b")
    assert "origin/chore/a--b" in p

def test_omp_session_file_routes_via_resume():
    from harness.backend import OmpRuntime
    assert OmpRuntime().session_file_flag("/tmp/x.jsonl") == ["--resume", "/tmp/x.jsonl"]
    assert OmpRuntime().session_file_flag("") == []
    argv = OmpRuntime().command_argv("prompt", True, ["--auto-approve"])
    assert "--session-file" not in argv
