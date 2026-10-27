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
        default=True,
        help="fail fast on network CLIs (gh/glab/jira-cli) instead of spawning them (default: on)",
    )
    parser.addoption(
        "--allow-network",
        action="store_false",
        dest="mock_network",
        help="opt out of the network-CLI guard (real gh/glab/jira-cli calls allowed; may hang offline)",
    )


def network_guard(real_run):
    """Wrap subprocess.run: network CLIs fail fast, rest passes through.

    gh/glab/jira-cli raise FileNotFoundError instantly (run_cmd converts to
    HarnessError "command not found"); every other argv — including git —
    delegates to the real run untouched.
    """

    def _guard(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        cmd = argv[0] if argv else ""
        if isinstance(cmd, str) and Path(cmd).name in NETWORK_CLIS:
            raise FileNotFoundError(f"mocked offline: {cmd}")
        return real_run(*args, **kwargs)

    return _guard


@pytest.fixture(autouse=True)
def _mock_network_clis(request, monkeypatch):
    # Default-on: gh/glab/jira-cli spawn → FileNotFoundError instantly
    # (run_cmd converts to HarnessError "command not found"). Tests needing
    # a specific CLI stub monkeypatch subprocess.run again — later patches
    # compose over this one. --allow-network opts out for live-CLI runs.
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
