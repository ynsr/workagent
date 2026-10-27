"""Run-dir store tests: per-run state dirs for detached runs."""

from __future__ import annotations


def test_run_dir_roundtrip(isolated_config):
    from workagent import run_store
    d = run_store.new_run_dir("abc123")
    run_store.write_meta(d, {"command": "sync", "args": ["IPG-1"],
                             "target": "sync:IPG-1"})
    assert run_store.read_meta(d)["target"] == "sync:IPG-1"
    assert run_store.read_exit(d) is None
    run_store.write_pid(d, 12345, 6789)
    assert run_store.read_pid(d) == (12345, 6789)
    run_store.write_exit(d, 0)
    assert run_store.read_exit(d) == 0
    assert (d / "output.log").exists()
