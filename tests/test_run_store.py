"""Run-dir store tests: per-run state dirs for detached runs."""

from __future__ import annotations


def _transcripts_root(isolated_config):
    return isolated_config / "runs"


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


def test_run_state_lives_beside_the_transcript_tree(isolated_config):
    """Run state lives in runs/_web/: the trees are disjoint by path."""
    from workagent import run_store
    d = run_store.new_run_dir("run1")
    assert d == _transcripts_root(isolated_config) / "_web" / "run1"


def test_iter_run_dirs_ignores_transcript_tree(isolated_config):
    """A harness tree dir is never a run state dir, whatever files it holds."""
    from workagent import run_store
    d = run_store.new_run_dir("run1")
    run_store.write_meta(d, {"command": "sync", "args": []})
    harness = _transcripts_root(isolated_config) / "omp" / "feat-x" / "review"
    harness.mkdir(parents=True)
    (harness / "x.jsonl").write_text("{}\n")
    # Even a stray meta.json/pid in a transcript dir changes nothing.
    (harness / "meta.json").write_text("{}")
    (harness / "pid").write_text("1 1\n")
    assert run_store.iter_run_dirs() == [d]


def test_prune_finished_leaves_transcript_tree(isolated_config):
    from workagent import run_store
    d = run_store.new_run_dir("run1")
    run_store.write_meta(d, {"command": "sync", "args": []})
    run_store.write_exit(d, 0)
    harness = _transcripts_root(isolated_config) / "omp" / "feat-x" / "review"
    harness.mkdir(parents=True)
    (harness / "x.jsonl").write_text("{}\n")
    assert run_store.prune_finished(keep=0) == 1
    assert not d.exists()
    assert (harness / "x.jsonl").is_file()
    # A pruned/closed run dir is stamped with exit_code; a transcript dir never is.
    assert not (harness / "exit_code").exists()


def test_rehydrate_skips_transcript_tree(isolated_config):
    """Serve startup must not adopt or close a harness transcript tree."""
    from workagent import run_store, web_runs
    harness = _transcripts_root(isolated_config) / "omp" / "feat-x" / "review"
    harness.mkdir(parents=True)
    (harness / "x.jsonl").write_text("{}\n")
    (harness / "meta.json").write_text('{"command": "sync", "args": []}')
    assert web_runs.rehydrate(web_runs.Registry()) == {"adopted": [], "closed": []}
    assert not (harness / "exit_code").exists()
