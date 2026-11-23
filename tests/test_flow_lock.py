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


def test_slug_caps_long_paths_with_hash():
    """Deep paths are capped under NAME_MAX with a stable hash suffix."""
    deep_a = "/".join(["segment"] * 40) + "/repo-a"
    deep_b = "/".join(["segment"] * 40) + "/repo-b"
    slug_a = flow_lock._slug(deep_a)
    slug_b = flow_lock._slug(deep_b)
    assert len(slug_a) <= 120
    assert len(slug_b) <= 120
    assert slug_a != slug_b
    # Idempotent when passed back into _slug
    assert flow_lock._slug(slug_a) == slug_a


def test_acquire_does_not_call_on_wait_on_non_contention_oserror(tmp_path, monkeypatch):
    """Non-BlockingIOError OSError (e.g. ENOLCK) propagates without triggering on_wait."""
    import errno
    import pytest

    def fake_flock(fd, op):
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(flow_lock.fcntl, "flock", fake_flock)
    waited: list[str] = []
    with pytest.raises(OSError) as exc_info:
        with flow_lock.SequentialStepLock(base=tmp_path) as lock:
            lock.acquire("workagent", on_wait=waited.append)
    assert exc_info.value.errno == errno.ENOLCK
    assert waited == []


def test_canonical_lock_scope_unifies_repo_name_and_path(tmp_path):
    """A registered repo name, its path, and a symlink resolve to the same scope."""
    from workagent import cli_flow_run

    real_repo = tmp_path / "real-repo"
    real_repo.mkdir()
    sym_repo = tmp_path / "sym-repo"
    sym_repo.symlink_to(real_repo)
    repos.register_repo("workagent", real_repo, tracker_key="github:ynsr/workagent", default_branch_name="main")

    by_name = cli_flow_run._canonical_lock_scope("workagent")
    by_path = cli_flow_run._canonical_lock_scope(str(real_repo) + "/")
    by_sym = cli_flow_run._canonical_lock_scope(str(sym_repo))
    assert by_name == by_path == by_sym == str(real_repo.resolve())


def test_flow_run_releases_lock_on_sequential_to_parallel(tmp_path, monkeypatch):
    """Transitioning from a sequential step to a parallel step releases the lock first."""
    store.create_template(
        name="Seq Then Par",
        label="seq_par",
        steps=[
            {"kind": "sync", "execution_mode": "sequential"},
            {"kind": "review", "execution_mode": "parallel"},
        ],
    )
    repos.register_repo("workagent", tmp_path, tracker_key="github:ynsr/workagent", default_branch_name="main")
    store.record_link(
        "github:ynsr/workagent#158",
        {"worktree": str(tmp_path / "wt"), "branch": "feat/158", "repo": "workagent"},
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
        cli_flow, "_run_sync_step",
        lambda key, cfg, j: (events.append(("sync", active[-1].scope if active else None)) or {"status": "ok"}),
    )
    monkeypatch.setattr(
        cli_flow, "_run_review_step",
        lambda key, cfg, fix, j: (events.append(("review", active[-1].scope if active else None)) or {"status": "ok"}),
    )

    res = runner.invoke(app, ["flow", "run", "seq_par", "github:ynsr/workagent#158", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    expected_scope = flow_lock._slug(str(tmp_path.resolve()))
    sync_idx = events.index(("sync", expected_scope))
    release_idx = events.index(("release", expected_scope))
    review_idx = events.index(("review", None))
    assert sync_idx < release_idx < review_idx


def test_flow_run_releases_lock_on_step_failure(tmp_path, monkeypatch):
    """A failing sequential step releases the lock on exit."""
    from workagent.errors import HarnessError

    store.create_template(
        name="Seq Fail",
        label="seq_fail",
        steps=[{"kind": "sync", "execution_mode": "sequential"}],
    )
    repos.register_repo("workagent", tmp_path, tracker_key="github:ynsr/workagent", default_branch_name="main")
    store.record_link(
        "github:ynsr/workagent#159",
        {"worktree": str(tmp_path / "wt"), "branch": "feat/159", "repo": "workagent"},
    )

    released: list[str] = []
    orig_release = flow_lock.SequentialStepLock.release

    def spy_release(self):
        if self.scope is not None:
            released.append(self.scope)
        return orig_release(self)

    monkeypatch.setattr(flow_lock.SequentialStepLock, "release", spy_release)
    monkeypatch.setattr(
        cli_flow, "_run_sync_step",
        lambda key, cfg, j: (_ for _ in ()).throw(HarnessError("boom", exit_code=1)),
    )

    res = runner.invoke(app, ["flow", "run", "seq_fail", "github:ynsr/workagent#159", "--yes", "--json"])
    assert res.exit_code != 0
    assert released == [flow_lock._slug(str(tmp_path.resolve()))]


def test_flow_run_releases_lock_on_preview_pause(tmp_path, monkeypatch):
    """A pausing (preview=True) sequential step releases the lock on exit."""
    store.create_template(
        name="Seq Pause",
        label="seq_pause",
        steps=[{"kind": "sync", "execution_mode": "sequential"}],
    )
    repos.register_repo("workagent", tmp_path, tracker_key="github:ynsr/workagent", default_branch_name="main")
    store.record_link(
        "github:ynsr/workagent#160",
        {"worktree": str(tmp_path / "wt"), "branch": "feat/160", "repo": "workagent"},
    )

    released: list[str] = []
    orig_release = flow_lock.SequentialStepLock.release

    def spy_release(self):
        if self.scope is not None:
            released.append(self.scope)
        return orig_release(self)

    monkeypatch.setattr(flow_lock.SequentialStepLock, "release", spy_release)
    monkeypatch.setattr(
        cli_flow, "_run_sync_step",
        lambda key, cfg, j: {"status": "preview", "preview": True, "detail": "confirm"},
    )

    res = runner.invoke(app, ["flow", "run", "seq_pause", "github:ynsr/workagent#160", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert released == [flow_lock._slug(str(tmp_path.resolve()))]
