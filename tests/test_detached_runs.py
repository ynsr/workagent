"""Detached-run survival: simulated restart (fresh Registry + rehydrate)."""

from __future__ import annotations


def test_run_survives_registry_drop_and_cancels(isolated_config):
    """Simulated restart: fresh Registry + rehydrate keeps run live, cancel kills it."""
    import time
    from workagent import run_store, store, web_runs
    from workagent.webapp import Registry
    reg1 = Registry()
    run = web_runs.Run(id="surv1", command="cleanup", args=["x"],
                       target="cleanup:surv1",
                       argv=["/bin/sh", "-c", "echo started; sleep 30"])
    reg1.runs[run.id] = run
    web_runs._spawn(run, reg1)
    assert web_runs._wait_log(run, "started", timeout=5)
    reg2 = Registry()  # serve restarted: RAM gone, disk remains
    out = web_runs.rehydrate(reg2)
    assert "surv1" in out["adopted"]
    run2 = reg2.get("surv1")
    assert web_runs._wait_log(run2, "started", timeout=5)  # tail continuity
    web_runs._cancel(run2)
    assert run2.state == "cancelled"
    pid, _ticks = run_store.read_pid(run_store.runs_root() / "surv1")
    deadline = time.time() + 5
    while pid is not None and store._pid_alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    assert pid is None or not store._pid_alive(pid)

def test_shutdown_does_not_cancel_tracked_runs(isolated_config):
    """SIGTERM shutdown must not kill tracked runs (flow #47 / issue #66).

    Shutdown only flags should_exit; live detached runs keep their PIDs and
    the next boot re-adopts them via rehydrate.
    """
    import time
    import types
    from workagent import run_store, store, web_runs
    from workagent.webapp import Registry, _shutdown_leave_detached_running
    reg1 = Registry()
    run = web_runs.Run(id="survshutdown", command="flow", args=["run"],
                       target="flow:tpl:ref",
                       argv=["/bin/sh", "-c", "echo started; sleep 30"])
    reg1.runs[run.id] = run
    web_runs._spawn(run, reg1)
    try:
        assert web_runs._wait_log(run, "started", timeout=5)
        pid, _ticks = run_store.read_pid(run_store.runs_root() / "survshutdown")
        assert pid is not None and store._pid_alive(pid)
        # Simulate SIGTERM shutdown: only should_exit is set, no cancel.
        server = types.SimpleNamespace(should_exit=False)
        _shutdown_leave_detached_running(server)
        assert server.should_exit is True
        assert store._pid_alive(pid)
        # Fresh boot re-adopts the still-live run (no exit written).
        reg2 = Registry()
        out = web_runs.rehydrate(reg2)
        assert "survshutdown" in out["adopted"]
        run2 = reg2.get("survshutdown")
        assert run2.state == "running"
        assert web_runs._wait_log(run2, "started", timeout=5)
    finally:
        web_runs._cancel(reg1.runs["survshutdown"])
        pid, _ticks = run_store.read_pid(run_store.runs_root() / "survshutdown")
        deadline = time.time() + 5
        while pid is not None and store._pid_alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        assert pid is None or not store._pid_alive(pid)


def test_cancel_refuses_reused_pid(isolated_config):
    """Stale pid file pointing at an unrelated live process: never kill, mark failed."""
    import subprocess
    from workagent import run_store, store, web_runs
    from workagent.webapp import Registry
    other = subprocess.Popen(["sleep", "30"])
    try:
        d = run_store.new_run_dir("reused1")
        run_store.write_meta(d, {"command": "sync", "args": [],
                                 "target": "sync:reused1", "created": 0.0})
        # Wrong ticks on purpose: the guard must refuse this PID.
        (d / "pid").write_text(f"{other.pid} 1\n")
        ticks = store._proc_start_ticks(other.pid)
        assert ticks is None or ticks != 1
        reg = Registry()
        run = web_runs.Run(id="reused1", command="sync", args=[],
                           target="sync:reused1")
        run.run_dir = str(d)
        reg.runs[run.id] = run
        web_runs._cancel(run)
        assert run.state == "failed"
        assert other.poll() is None  # unrelated process untouched
    finally:
        other.kill()
        other.wait()


def test_rehydrate_partial_trailing_line(isolated_config, monkeypatch):
    """rehydrate() stops at the last newline so a partial trailing line is tailed whole."""
    import os
    from workagent import run_store, store, web_runs
    from workagent.webapp import Registry

    d = run_store.new_run_dir("partial1")
    run_store.write_meta(d, {"command": "sync", "args": [], "target": "sync:partial1", "created": 0.0})
    (d / "pid").write_text(f"{os.getpid()} {store._proc_start_ticks(os.getpid())}\n")
    (d / "output.log").write_bytes(b"line1\npart")

    captured: dict = {}

    def fake_tailer(run, registry, backfill=False, initial_offset=None):
        captured["initial_offset"] = initial_offset

    monkeypatch.setattr(web_runs, "_start_tailer", fake_tailer)
    reg = Registry()
    out = web_runs.rehydrate(reg)
    assert "partial1" in out["adopted"]
    run = reg.get("partial1")
    assert [txt for _, txt in run.lines] == ["line1"]
    assert captured["initial_offset"] == len(b"line1\n")


def test_rehydrate_live_run_finalizes_precreated_db_row(isolated_config):
    """A precreated DB row survives rehydrate and is finalized when the run exits 0."""
    import time
    from workagent import run_store, store_sqlite as _sq, web_runs
    from workagent.webapp import Registry

    db = _sq.db_path()
    _sq.connect(db).close()
    reg1 = Registry()
    run = web_runs.Run(id="dbreh1", command="review", args=["IPG-1"],
                       target="review:IPG-1",
                       argv=["/bin/sh", "-c", "echo started; sleep 0.5; echo done"])
    reg1.runs[run.id] = run
    web_runs._spawn(run, reg1)
    assert web_runs._wait_log(run, "started", timeout=5)
    assert run.db_run_id > 0
    rid = run.db_run_id
    meta = run_store.read_meta(run_store.runs_root() / "dbreh1")
    assert meta.get("db_run_id") == rid

    reg2 = Registry()
    out = web_runs.rehydrate(reg2)
    assert "dbreh1" in out["adopted"]
    run2 = reg2.get("dbreh1")
    assert run2.db_run_id == rid

    deadline = time.time() + 5
    while run2.state == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert run2.state == "succeeded"

    deadline = time.time() + 5
    row = None
    while time.time() < deadline:
        row = _sq.get_run(db, rid, include_deleted_worktrees=True)
        if row and row["state"] != "running":
            break
        time.sleep(0.05)
    assert row is not None
    assert row["state"] == "finished"
    assert row["exit_code"] == 0


def test_rehydrate_finalizes_run_finished_during_downtime(isolated_config):
    """If a detached run exits while serve is down, rehydrate still finalizes its DB row."""
    from workagent import run_store, store_sqlite as _sq, web_runs
    from workagent.webapp import Registry

    db = _sq.db_path()
    _sq.connect(db).close()
    rid = _sq.insert_run(
        db, "review", ["IPG-2"], None,
        metadata={"origin": "web-run", "web_run_id": "downfin1", "target": "review:IPG-2"},
        state="running",
    )
    d = run_store.new_run_dir("downfin1")
    run_store.write_meta(d, {
        "command": "review", "args": ["IPG-2"], "target": "review:IPG-2",
        "created": 100.0, "db_run_id": rid,
    })
    (d / "output.log").write_text("JOB_DONE\n")
    run_store.write_exit(d, 0)

    web_runs.rehydrate(Registry())
    row = _sq.get_run(db, rid, include_deleted_worktrees=True)
    assert row is not None
    assert row["state"] == "finished"
    assert row["exit_code"] == 0
    assert "JOB_DONE" in (row["output"] or "")


def test_rehydrate_finalizes_dead_run_db_row(isolated_config):
    """If a detached run's process died while serve was down, rehydrate marks its DB row failed."""
    from workagent import run_store, store_sqlite as _sq, web_runs
    from workagent.webapp import Registry

    db = _sq.db_path()
    _sq.connect(db).close()
    rid = _sq.insert_run(
        db, "review", ["IPG-3"], None,
        metadata={"origin": "web-run", "web_run_id": "downdead1", "target": "review:IPG-3"},
        state="running",
    )
    d = run_store.new_run_dir("downdead1")
    run_store.write_meta(d, {
        "command": "review", "args": ["IPG-3"], "target": "review:IPG-3",
        "created": 100.0,
    })
    out = web_runs.rehydrate(Registry())
    assert "downdead1" in out["closed"]
    row = _sq.get_run(db, rid, include_deleted_worktrees=True)
    assert row is not None
    assert row["state"] == "failed"
    assert row["exit_code"] == -1


def test_rehydrate_reconciles_orphaned_web_run_rows(isolated_config):
    """Orphaned web-run rows whose run_dir was already pruned are reconciled from child runs."""
    from workagent import store_sqlite as _sq, web_runs
    from workagent.webapp import Registry

    db = _sq.db_path()
    _sq.connect(db).close()
    ok_parent = _sq.insert_run(
        db, "review", ["IPG-4"], None,
        metadata={"origin": "web-run", "web_run_id": "pruned_ok", "target": "review:IPG-4"},
        state="running",
    )
    _sq.insert_run(db, "review", ["IPG-4"], 0, state="finished",
                   parent_run_id=ok_parent)

    fail_parent = _sq.insert_run(
        db, "review", ["IPG-5"], None,
        metadata={"origin": "web-run", "web_run_id": "pruned_fail", "target": "review:IPG-5"},
        state="running",
    )

    web_runs.rehydrate(Registry())
    ok_row = _sq.get_run(db, ok_parent, include_deleted_worktrees=True)
    fail_row = _sq.get_run(db, fail_parent, include_deleted_worktrees=True)
    assert ok_row is not None and ok_row["state"] == "finished" and ok_row["exit_code"] == 0
    assert fail_row is not None and fail_row["state"] == "failed" and fail_row["exit_code"] == -1

    # Child still running -> parent marked failed (-1).
    running_child_parent = _sq.insert_run(
        db, "review", ["IPG-6"], None,
        metadata={"origin": "web-run", "web_run_id": "pruned_running_child", "target": "review:IPG-6"},
        state="running",
    )
    _sq.insert_run(db, "review", ["IPG-6"], None, state="running",
                   parent_run_id=running_child_parent)

    # Failed child alongside a newer finished child -> parent marked failed (-1).
    mixed_child_parent = _sq.insert_run(
        db, "review", ["IPG-7"], None,
        metadata={"origin": "web-run", "web_run_id": "pruned_mixed_child", "target": "review:IPG-7"},
        state="running",
    )
    _sq.insert_run(db, "review", ["IPG-7"], 1, state="failed",
                   parent_run_id=mixed_child_parent)
    _sq.insert_run(db, "review", ["IPG-7"], 0, state="finished",
                   parent_run_id=mixed_child_parent)

    web_runs.rehydrate(Registry())
    rc_row = _sq.get_run(db, running_child_parent, include_deleted_worktrees=True)
    mc_row = _sq.get_run(db, mixed_child_parent, include_deleted_worktrees=True)
    assert rc_row is not None and rc_row["state"] == "failed" and rc_row["exit_code"] == -1
    assert mc_row is not None and mc_row["state"] == "failed" and mc_row["exit_code"] == -1


def test_rehydrate_live_run_find_web_run_fallback_without_db_run_id(isolated_config, monkeypatch):
    """Live adopted run whose meta.json lacks db_run_id resolves it via find_web_run and populates argv."""
    import os
    from workagent import run_store, store, store_sqlite as _sq, web_runs
    from workagent.webapp import Registry

    db = _sq.db_path()
    _sq.connect(db).close()
    rid = _sq.insert_run(
        db, "review", ["IPG-8"], None,
        metadata={"origin": "web-run", "web_run_id": "livefallback1", "target": "review:IPG-8"},
        state="running",
    )
    d = run_store.new_run_dir("livefallback1")
    run_store.write_meta(d, {
        "command": "review", "args": ["IPG-8", "--fix-comments"],
        "target": "review:IPG-8", "created": 100.0,
    })
    (d / "pid").write_text(f"{os.getpid()} {store._proc_start_ticks(os.getpid())}\n")
    monkeypatch.setattr(web_runs, "_start_tailer", lambda *a, **kw: None)

    reg = Registry()
    out = web_runs.rehydrate(reg)
    assert "livefallback1" in out["adopted"]
    run = reg.get("livefallback1")
    assert run is not None
    assert run.db_run_id == rid
    assert run.argv == ["review", "IPG-8", "--fix-comments"]


def test_rehydrate_finalized_fix_run_stamps_review_and_uses_exit_mtime(isolated_config):
    """Rehydrated fix-comments run populates argv (stamping parent review) and uses exit_code mtime."""
    import os
    from workagent import run_store, store_sqlite as _sq, web_runs
    from workagent.webapp import Registry

    db = _sq.db_path()
    _sq.connect(db).close()
    sfile = str(isolated_config / "review.jsonl")
    review_id = _sq.insert_run(
        db, "review", ["IPG-9"], 0, file_path=sfile,
        session_type="review", state="finished",
    )
    fix_id = _sq.insert_run(
        db, "review", ["IPG-9", "--fix-comments", "--use-review-session"], None,
        file_path=sfile,
        metadata={"origin": "web-run", "web_run_id": "fixrehyd1", "target": "review:IPG-9"},
        state="running",
    )
    d = run_store.new_run_dir("fixrehyd1")
    run_store.write_meta(d, {
        "command": "review",
        "args": ["IPG-9", "--fix-comments", "--use-review-session"],
        "target": "review:IPG-9",
        "created": 100.0,
        "session_file": sfile,
        "db_run_id": fix_id,
    })
    run_store.write_exit(d, 0)
    os.utime(d / "exit_code", (125.0, 125.0))

    web_runs.rehydrate(Registry())
    fix_row = _sq.get_run(db, fix_id, include_deleted_worktrees=True)
    review_row = _sq.get_run(db, review_id, include_deleted_worktrees=True)
    assert fix_row is not None
    assert fix_row["state"] == "finished"
    assert fix_row["duration_ms"] == 25000
    assert review_row is not None
    assert review_row["metadata"].get("review_comments_fixed_at")
    assert review_row["metadata"].get("fixed_by_run_id") == "fixrehyd1"
