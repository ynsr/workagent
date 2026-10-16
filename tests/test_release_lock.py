"""Lock + duration primitives tests (Task 1 RED)."""
from __future__ import annotations

import pytest


def test_parse_duration_units(tmp_path):
    from workagent.release_util import parse_duration
    assert parse_duration("10m") == 600
    assert parse_duration("15m") == 900
    assert parse_duration("90s") == 90
    assert parse_duration("2h") == 7200
    assert parse_duration("30") == 30


def test_parse_duration_garbage_is_usage():
    from workagent.release_util import parse_duration
    from workagent.errors import HarnessError
    with pytest.raises(HarnessError) as e:
        parse_duration("ten minutes")
    assert e.value.exit_code == 2


def test_lock_rejects_second_holder(tmp_path):
    from workagent.release_lock import held_lock, lock_path
    p = lock_path(str(tmp_path), "proj")
    with held_lock(p):
        from workagent.errors import HarnessError
        with pytest.raises(HarnessError, match="release already running"):
            with held_lock(p):
                pass


def test_lock_released_after_exit(tmp_path):
    from workagent.release_lock import held_lock, lock_path
    p = lock_path(str(tmp_path), "proj")
    with held_lock(p):
        pass
    with held_lock(p):  # must not raise
        pass


def test_worktree_base_env(tmp_path, monkeypatch):
    from workagent.release_util import worktree_base
    monkeypatch.setenv("WORKTREES_DIR", str(tmp_path))
    assert worktree_base() == tmp_path
