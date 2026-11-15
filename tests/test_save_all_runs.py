"""Save-all + soft-delete exclusion tests (Task: persist every work run).

Every run persists — attached rows carry their worktree ref, and unattached
attempts (pre-link failures, repo-keyed releases) persist with an empty ref.
Soft-deleted worktree rows hide their runs/flows from list/detail queries
unless explicitly included; a hard delete purges the worktree's runs and
flows (runs.worktree_ref has no FK).
"""

from __future__ import annotations


def _seed_worktree(db, key="k", path="/tmp/wt", branch="b"):
    from workagent import store_sqlite as sq
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES (?, ?, ?, NULL, '2026-01-01', '{}')", (key, path, branch))


def test_unattached_run_persists_empty_ref(tmp_path, isolated_config):
    """A run with no linked worktree persists repo-scoped and stays visible."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    rid = sq.insert_run(db, "start", ["k"], None, worktree_ref=None,
                        file_path="/f/unattached.jsonl", state="preview")
    assert rid
    rows = sq.list_runs(db, limit=100, include_deleted_worktrees=True)
    assert [r["id"] for r in rows] == [rid]
    assert rows[0]["worktree_ref"] == ""
    assert rows[0]["file_path"] == "/f/unattached.jsonl"


def test_attached_preview_run_keeps_full_output(tmp_path, isolated_config):
    """An attached preview run carries its ref, transcript and full output."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _seed_worktree(db)
    out = ["harness command: omp foo", "line2", "line3"]
    rid = sq.insert_run(db, "start", ["k"], None, worktree_ref="k",
                        harness_name="omp", initiator_command="omp",
                        prompt="prompt", file_path="/f/p.jsonl",
                        session_type="start", state="preview", output=out)
    row = sq.get_run(db, rid)
    assert row["worktree_ref"] == "k"
    assert row["state"] == "preview"
    runs = sq.list_runs(db, limit=100)
    assert len(runs) == 1 and runs[0]["id"] == rid
    assert runs[0]["output"] == "\n".join(out)


def test_soft_deleted_hidden_from_lists(tmp_path, isolated_config):
    """Soft-delete hides runs/flows from default queries."""
    from workagent import store as st
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _seed_worktree(db)
    rid = sq.insert_run(db, "start", ["k"], 0, worktree_ref="k",
                        file_path="/f/p.jsonl")
    fid = st.create_flow(template_label="t", worktree_ref="k")["id"]
    assert len(sq.list_runs(db, limit=100)) == 1
    assert len(st.list_flows()) == 1
    st.soft_delete_worktree("k")
    assert sq.list_runs(db, limit=100) == []
    assert sq.get_run(db, rid) is None
    assert st.list_flows() == []
    assert st.get_flow(fid) is None
    # Opt-in still sees them (restore path).
    assert len(sq.list_runs(db, limit=100, include_deleted_worktrees=True)) == 1
    assert sq.get_run(db, rid, include_deleted_worktrees=True)["id"] == rid
    assert len(st.list_flows(include_deleted_worktrees=True)) == 1
    assert st.get_flow(fid, include_deleted_worktrees=True) is not None


def test_hard_delete_purges_runs_and_flows(tmp_path, isolated_config):
    """delete_worktree_row drops the worktree's runs/flows, never repo-scoped runs."""
    from workagent import store as st
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _seed_worktree(db)
    attached = sq.insert_run(db, "start", ["k"], 0, worktree_ref="k",
                             file_path="/f/p.jsonl")
    repo = sq.insert_run(db, "start", [], 0, worktree_ref="",
                         file_path="/f/repo.jsonl")
    st.create_flow(template_label="t", worktree_ref="k")
    st.delete_worktree_row("k")
    rows = sq.list_runs(db, limit=100, include_deleted_worktrees=True)
    assert [r["id"] for r in rows] == [repo]
    assert attached != repo
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0] == 0


def test_run_output_cap(tmp_path, isolated_config):
    """Oversized output is capped at MAX_RUN_OUTPUT_LINES with truncated=1."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _seed_worktree(db)
    big = [f"line {i}" for i in range(sq.MAX_RUN_OUTPUT_LINES + 500)]
    rid = sq.insert_run(db, "sync", ["k"], 0, worktree_ref="k",
                        harness_name="omp", initiator_command="sync",
                        file_path="/f/s.jsonl", output=big)
    row = [r for r in sq.list_runs(db, limit=100) if r["id"] == rid][0]
    assert row["truncated"] == 1
    assert len(row["output"].splitlines()) == sq.MAX_RUN_OUTPUT_LINES
    assert row["output"].splitlines()[-1] == big[-1]
