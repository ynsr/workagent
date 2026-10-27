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
