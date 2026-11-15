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


def test_iter_run_dirs_skips_transcript_tree(isolated_config):
    """The transcript root shares runs/: a harness tree carries neither
    meta.json nor pid, so it is never a run state dir."""
    from workagent import run_store
    d = run_store.new_run_dir("run1")
    run_store.write_meta(d, {"command": "sync", "args": []})
    transcript = run_store.runs_root() / "omp" / "feat-x" / "review"
    transcript.mkdir(parents=True)
    (transcript / "x.jsonl").write_text("{}\n")
    assert run_store.iter_run_dirs() == [d]


def test_prune_finished_leaves_transcript_tree(isolated_config):
    from workagent import run_store
    d = run_store.new_run_dir("run1")
    run_store.write_meta(d, {"command": "sync", "args": []})
    run_store.write_exit(d, 0)
    transcript = run_store.runs_root() / "omp" / "feat-x" / "review"
    transcript.mkdir(parents=True)
    (transcript / "x.jsonl").write_text("{}\n")
    assert run_store.prune_finished(keep=0) == 1
    assert not d.exists()
    assert (transcript / "x.jsonl").is_file()
    # A pruned/closed run dir is stamped with exit_code; a transcript dir never is.
    assert not (transcript / "exit_code").exists()


def test_rehydrate_skips_transcript_tree(isolated_config):
    """Serve startup must not adopt or close a harness transcript tree."""
    from workagent import run_store, web_runs
    transcript = run_store.runs_root() / "omp" / "feat-x" / "review"
    transcript.mkdir(parents=True)
    (transcript / "x.jsonl").write_text("{}\n")
    assert web_runs.rehydrate(web_runs.Registry()) == {"adopted": [], "closed": []}
    assert not (transcript / "exit_code").exists()
