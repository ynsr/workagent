"""Soft-delete GC: transcript files go with the purged worktree row."""

from __future__ import annotations

import pytest


@pytest.fixture
def linked_worktree(tmp_path, monkeypatch, isolated_config):
    """A soft-deletable worktree with one recorded transcript run."""
    from workagent import cli_cleanup_gc as gc
    from workagent import store, store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    repo.mkdir()
    wt.mkdir()
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "branch": "feat/9",
                                     "repo": str(repo), "worktree": str(wt)})
    run_dir = isolated_config / "runs" / "omp" / "feat-9" / "review"
    run_dir.mkdir(parents=True)
    transcript = run_dir / "t-9.jsonl"
    transcript.write_text("{}\n")
    sq.insert_run(db, "review", ["jira:IPG-9"], 0, worktree_ref="jira:IPG-9",
                  harness_name="omp", initiator_command="review",
                  file_path=str(transcript), session_type="review")
    store.soft_delete_worktree("jira:IPG-9")
    monkeypatch.setattr(gc, "_teardown_local", lambda *a, **k: None)
    return gc, sq, db, transcript, run_dir


def test_gc_deletes_transcripts_the_store_purge_orphaned(linked_worktree):
    """The store GC drops the runs rows first; the files must still go."""
    gc, sq, db, transcript, run_dir = linked_worktree

    rows = gc.gc_soft_deleted(retention_days=-1)

    assert rows == [{"key": "jira:IPG-9", "branch": "feat/9",
                     "status": "purged"}]
    assert sq.runs_for_worktree(db, "jira:IPG-9",
                                include_deleted_worktrees=True) == []
    assert not transcript.exists()
    assert not run_dir.exists()  # emptied branch dirs pruned up to runs/


def test_destroy_deleted_deletes_transcripts(linked_worktree):
    gc, sq, db, transcript, run_dir = linked_worktree

    rows = gc.destroy_deleted(["jira:IPG-9"])

    assert [r["status"] for r in rows] == ["destroyed"]
    assert not transcript.exists()
    assert not run_dir.exists()
