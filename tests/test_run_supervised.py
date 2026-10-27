"""Supervisor shim tests: detach child, log output, record exit."""

from __future__ import annotations


def test_supervised_runs_child_and_records_exit(isolated_config, tmp_path):
    from workagent import run_store
    from workagent.run_supervised import run_supervised, supervisor_argv
    d = run_store.new_run_dir("sup1")
    argv = supervisor_argv(d, ["true-marker-flag"])
    assert "_run-supervised" in argv and "--run-dir" in argv
    # Real run with a trivial child (no setsid games in test — direct call).
    code = run_supervised(d, ["/bin/echo", "hello-sup"])
    assert code == 0
    assert "hello-sup" in (d / "output.log").read_text()
    assert run_store.read_exit(d) == 0
    pid, _ticks = run_store.read_pid(d)
    assert isinstance(pid, int) and pid > 0


def test_supervised_spawn_failure_records_127(isolated_config):
    from workagent import run_store
    from workagent.run_supervised import run_supervised
    d = run_store.new_run_dir("supfail")
    code = run_supervised(d, ["/nonexistent-child-binary-xyz"])
    assert code == 127
    assert run_store.read_exit(d) == 127
