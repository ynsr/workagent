"""--mock-network flag: network CLIs fail fast offline, git passes through."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.errors import HarnessError, run_cmd
from tests.conftest import NETWORK_CLIS, network_guard


def test_network_guard_blocks_each_cli():
    real_calls = []
    guard = network_guard(lambda *a, **k: real_calls.append(a) or "ok")
    for cli in NETWORK_CLIS:
        with pytest.raises(FileNotFoundError, match="mocked offline"):
            guard([cli, "anything"], capture_output=True)
    assert real_calls == []


def test_network_guard_passes_git_through():
    sentinel = object()
    guard = network_guard(lambda *a, **k: sentinel)
    assert guard(["git", "status"], capture_output=True) is sentinel
    assert guard(["git-wt", "start"], capture_output=True) is sentinel


def test_network_guard_matches_basename():
    guard = network_guard(lambda *a, **k: "real")
    assert guard(["git", "status"], capture_output=True) == "real"
    with pytest.raises(FileNotFoundError, match="mocked offline"):
        guard([f"/usr/bin/{NETWORK_CLIS[0]}", "pr", "list"])


def test_run_cmd_converts_mocked_missing_cli(monkeypatch):
    monkeypatch.setattr(subprocess, "run", network_guard(subprocess.run))
    with pytest.raises(HarnessError, match="command not found: gh"):
        run_cmd("gh", "pr", "list", echo=False)


def test_path_basename_check():
    assert Path("/usr/bin/gh").name == "gh"
