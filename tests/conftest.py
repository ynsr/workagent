"""Shared fixtures: isolated config dir, sys.path for src layout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

NETWORK_CLIS = ("gh", "glab", "jira-cli")


def pytest_addoption(parser):
    parser.addoption(
        "--mock-network",
        action="store_true",
        default=False,
        help="fail fast on network CLIs (gh/glab/jira-cli) instead of spawning them",
    )


def network_guard(real_run):
    """Wrap subprocess.run: network CLIs raise FileNotFoundError, rest passes through."""

    def _guard(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        cmd = argv[0] if argv else ""
        if isinstance(cmd, str) and Path(cmd).name in NETWORK_CLIS:
            raise FileNotFoundError(f"mocked offline: {cmd}")
        return real_run(*args, **kwargs)

    return _guard


@pytest.fixture(autouse=True)
def _mock_network_clis(request, monkeypatch):
    if not request.config.getoption("mock_network"):
        return
    import subprocess

    monkeypatch.setattr(subprocess, "run", network_guard(subprocess.run))


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    d = tmp_path / "workagent-config"
    d.mkdir()
    monkeypatch.setenv("WORKAGENT_CONFIG_DIR", str(d))
    return d
