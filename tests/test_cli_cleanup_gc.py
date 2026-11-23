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


def test_destroy_deleted_removes_scoped_session_dir_files_and_worktree_dir(
    tmp_path, monkeypatch, isolated_config
):
    """Destroy removes the linked worktree dir, DB rows, and unrecorded files in session_dir."""
    import subprocess
    from workagent import cli_cleanup_gc as gc
    from workagent import gitwt, store, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)
    repo = tmp_path / "main-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    wt = tmp_path / "wt-10"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feat/10", str(wt)],
        check=True, capture_output=True,
    )
    store.record_link("jira:IPG-10", {
        "issue": "IPG-10", "branch": "feat/10",
        "repo": str(repo), "worktree": str(wt),
    })

    session_dir = isolated_config / "runs" / "omp" / "feat-10" / "start"
    session_dir.mkdir(parents=True)
    unrecorded_jsonl = session_dir / "minted.jsonl"
    unrecorded_jsonl.write_text('{"type":"session"}\n')
    extra_log = session_dir / "output.log"
    extra_log.write_text("log\n")

    sq.insert_run(
        db, "start", ["jira:IPG-10"], 0, worktree_ref="jira:IPG-10",
        harness_name="omp", initiator_command="start",
        file_path="", session_type="start",
        metadata={"session_dir": str(session_dir), "branch_path": "feat-10"},
    )
    store.soft_delete_worktree("jira:IPG-10")
    monkeypatch.setattr(gitwt, "cleanup_worktree", lambda *a, **k: {})

    rows = gc.destroy_deleted(["jira:IPG-10"])

    assert [r["status"] for r in rows] == ["destroyed"]
    assert repo.is_dir()
    assert (repo / "README.md").is_file()
    assert not wt.exists()
    assert not unrecorded_jsonl.exists()
    assert not extra_log.exists()
    assert not session_dir.exists()
    assert sq.runs_for_worktree(db, "jira:IPG-10", include_deleted_worktrees=True) == []
    assert "jira:IPG-10" not in store.load_links(include_inactive=True, include_deleted=True)


def test_destroy_deleted_never_deletes_main_repo_folder(
    tmp_path, monkeypatch, isolated_config, capsys
):
    """Hard cleanup / destroy must never delete a main repo folder, registered repo, ancestor, or symlink."""
    import subprocess
    from workagent import cli_cleanup_gc as gc
    from workagent import gitwt, repos, store, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)

    parent_dir = tmp_path / "projects"
    parent_dir.mkdir()
    repo = parent_dir / "workagent"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("main repo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    reg_repo = tmp_path / "registered-repo"
    reg_repo.mkdir()
    (reg_repo / "keep.txt").write_text("keep\n")
    repos.register_repo("registered-repo", reg_repo, tracker_key="github:o/r")

    symlink_to_repo = tmp_path / "repo-symlink"
    symlink_to_repo.symlink_to(repo)

    gitwt_called = []
    monkeypatch.setattr(gitwt, "cleanup_worktree", lambda *a, **k: gitwt_called.append((a, k)) or {})

    cases = [
        ("branch:main-direct", str(repo), str(repo), "main"),
        ("branch:registered", str(reg_repo), str(repo), "feat/reg"),
        ("branch:ancestor", str(parent_dir), str(repo), "feat/anc"),
        ("branch:symlink", str(symlink_to_repo), str(repo), "feat/sym"),
    ]
    for key, wt_path, repo_path, branch in cases:
        store.record_link(key, {"branch": branch, "repo": repo_path, "worktree": wt_path})
        store.soft_delete_worktree(key)
        rows = gc.destroy_deleted([key])
        assert [r["status"] for r in rows] == ["destroyed"], key
        err = capsys.readouterr().err
        assert "refusing to delete main repo folder" in err, key

    assert not gitwt_called
    assert repo.is_dir() and (repo / "README.md").is_file()
    assert reg_repo.is_dir() and (reg_repo / "keep.txt").is_file()
    assert parent_dir.is_dir()
    assert symlink_to_repo.exists()


def test_destroy_deleted_does_not_rmtree_unscoped_session_dir(
    tmp_path, monkeypatch, isolated_config
):
    """A session_dir without branch_path is not branch-scoped and must not be rmtree'd."""
    from workagent import cli_cleanup_gc as gc
    from workagent import store, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("jira:IPG-11", {"issue": "IPG-11", "branch": "feat/11",
                                      "repo": str(repo), "worktree": str(wt)})
    shared_dir = isolated_config / "runs" / "omp" / "start"
    shared_dir.mkdir(parents=True)
    own_file = shared_dir / "own.jsonl"
    own_file.write_text("{}\n")
    other_file = shared_dir / "other-branch.jsonl"
    other_file.write_text("{}\n")

    sq.insert_run(
        db, "start", ["jira:IPG-11"], 0, worktree_ref="jira:IPG-11",
        harness_name="omp", initiator_command="start",
        file_path=str(own_file), session_type="start",
        metadata={"session_dir": str(shared_dir)},
    )
    store.soft_delete_worktree("jira:IPG-11")
    monkeypatch.setattr(gc, "_teardown_local", lambda *a, **k: None)

    rows = gc.destroy_deleted(["jira:IPG-11"])

    assert [r["status"] for r in rows] == ["destroyed"]
    assert not own_file.exists()
    assert other_file.exists()
    assert shared_dir.is_dir()


def test_cleanup_hard_delete_refuses_main_repo_folder(
    tmp_path, monkeypatch, isolated_config, capsys
):
    """cleanup --hard-delete must not call gitwt.cleanup_worktree on a main repo folder."""
    import subprocess
    from workagent import cli_cleanup as cc
    from workagent import gitwt, store, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)
    repo = tmp_path / "main-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("main\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    entry = {"branch": "main", "repo": str(repo), "worktree": str(repo)}
    store.record_link("branch:main", entry)

    gitwt_called = []
    monkeypatch.setattr(gitwt, "cleanup_worktree", lambda *a, **k: gitwt_called.append((a, k)) or {})

    res = cc._cleanup_one(
        "branch:main", entry, force=True, yes=True, dry_run=False,
        json_output=True, merge=False, hard_delete=True,
    )

    assert res["status"] == "cleaned"
    assert res["cleanup"]["skipped"] == "main-repo"
    assert not gitwt_called
    assert repo.is_dir() and (repo / "README.md").is_file()
    assert "refusing to delete main repo folder" in capsys.readouterr().err
