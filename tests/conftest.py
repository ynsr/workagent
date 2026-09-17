"""Shared fixtures: isolated config dir, sys.path for src layout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    d = tmp_path / "harness-config"
    d.mkdir()
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(d))
    return d
