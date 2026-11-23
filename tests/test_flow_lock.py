"""Tests for cross-process sequential flow execution lock (issue #163)."""
from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

from typer.testing import CliRunner

from workagent import cli_flow, flow_lock, repos, store
from workagent.cli import app

runner = CliRunner()


def _hold_lock_child(base_str: str, scope: str, hold_sec: float, ready_q, done_q) -> None:
    with flow_lock.SequentialStepLock(base=Path(base_str)) as lock:
        lock.acquire(scope)
        ready_q.put("acquired")
        time.sleep(hold_sec)
        done_q.put(time.monotonic())


def test_sequential_lock_blocks_second_process_until_release(tmp_path):
    """A second process blocks on the same repo scope until the first releases."""
    ctx = multiprocessing.get_context("spawn")
    ready_q = ctx.Queue()
    done_q = ctx.Queue()
    proc = ctx.Process(
        target=_hold_lock_child,
        args=(str(tmp_path), "workagent", 0.25, ready_q, done_q),
    )
    proc.start()
    try:
        assert ready_q.get(timeout=5) == "acquired"
        waited: list[str] = []
        acquired: list[str] = []
        with flow_lock.SequentialStepLock(base=tmp_path) as lock:
            lock.acquire(
                "workagent",
                on_wait=waited.append,
                on_acquired=acquired.append,
            )
            got_at = time.monotonic()
        released_at = done_q.get(timeout=5)
        assert waited == ["workagent"]
        assert acquired == ["workagent"]
        assert got_at >= released_at - 0.02
    finally:
        proc.join(timeout=5)


def test_sequential_lock_allows_different_repos_concurrently(tmp_path):
    """Different repo scopes do not block each other."""
    ctx = multiprocessing.get_context("spawn")
    ready_q = ctx.Queue()
    done_q = ctx.Queue()
    proc = ctx.Process(
        target=_hold_lock_child,
        args=(str(tmp_path), "repo-a", 0.4, ready_q, done_q),
    )
    proc.start()
    try:
        assert ready_q.get(timeout=5) == "acquired"
        waited: list[str] = []
        with flow_lock.SequentialStepLock(base=tmp_path) as lock:
            lock.acquire("repo-b", on_wait=waited.append)
        assert waited == []
    finally:
        proc.join(timeout=5)


def test_flow_run_holds_lock_across_contiguous_sequential_steps(tmp_path, monkeypatch):
    """review/fix_comments (parallel) run unlocked; sync+cleanup (sequential) hold one lock."""
    store.create_template(
        name="Review Fix",
        label="review_fix",
        steps=[
            {"kind": "review", "execution_mode": "parallel"},
            {"kind": "fix_comments", "execution_mode": "parallel"},
            {"kind": "sync", "execution_mode": "sequential"},
            {"kind": "cleanup", "execution_mode": "sequential"},
        ],
    )
    repos.register_repo("workagent", tmp_path, tracker_key="github:ynsr/workagent", default_branch_name="main")
    store.record_link(
        "github:ynsr/workagent#157",
        {
            "worktree": str(tmp_path / "wt"),
            "branch": "feat/157",
            "repo": "workagent",
        },
    )

    events: list[tuple[str, str | None]] = []
    orig_acquire = flow_lock.SequentialStepLock.acquire
    orig_release = flow_lock.SequentialStepLock.release
    active: list[flow_lock.SequentialStepLock] = []

    def spy_acquire(self, scope, **kw):
        active.append(self)
        events.append(("acquire", scope))
        return orig_acquire(self, scope, **kw)

    def spy_release(self):
        if self.scope is not None:
            events.append(("release", self.scope))
        return orig_release(self)

    monkeypatch.setattr(flow_lock.SequentialStepLock, "acquire", spy_acquire)
    monkeypatch.setattr(flow_lock.SequentialStepLock, "release", spy_release)
    monkeypatch.setattr(
        cli_flow, "_run_review_step",
        lambda key, cfg, fix, j: (events.append(("fix" if fix else "review", active[-1].scope if active else None)) or {"status": "ok"}),
    )
    monkeypatch.setattr(
        cli_flow, "_run_sync_step",
        lambda key, cfg, j: (events.append(("sync", active[-1].scope if active else None)) or {"status": "ok"}),
    )
    monkeypatch.setattr(
        cli_flow, "_run_cleanup_step",
        lambda key, cfg, j: (events.append(("cleanup", active[-1].scope if active else None)) or {"status": "ok"}),
    )

    res = runner.invoke(app, ["flow", "run", "review_fix", "github:ynsr/workagent#157", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    # Parallel steps ran with no lock held; sync & cleanup ran under the same held lock without releasing between them.
    expected_scope = flow_lock._slug(str(tmp_path))
    assert ("review", None) in events
    assert ("fix", None) in events
    assert ("sync", expected_scope) in events
    assert ("cleanup", expected_scope) in events
    sync_idx = events.index(("sync", expected_scope))
    cleanup_idx = events.index(("cleanup", expected_scope))
    assert ("release", expected_scope) not in events[sync_idx:cleanup_idx]
    assert events[-1] == ("release", expected_scope)
