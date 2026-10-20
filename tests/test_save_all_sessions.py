"""Save-all + soft-delete exclusion tests (Task: persist every work run).

Only worktree-attached sessions/runs persist (worktrees FK); unattached
attempts (pre-link failures, repo-keyed releases) write nothing. Soft-deleted
worktree rows hide their sessions/runs/flows from list/detail queries unless
explicitly included; hard-delete cascades sessions/runs via the FK.
"""

from __future__ import annotations


def _seed_worktree(db, key="k", path="/tmp/wt", branch="b"):
    from workagent import store_sqlite as sq
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES (?, ?, ?, NULL, '2026-01-01', '{}')", (key, path, branch))


def test_unattached_preview_writes_nothing(tmp_path, monkeypatch, isolated_config):
    """record_preview_session with no linked worktree persists nothing."""
    from workagent import cli_harness as h
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sid = h.record_preview_session("nope-missing", "start", "p", "omp",
                                   "harness command: x")
    assert sid is None
    assert sq.list_sessions(db, include_deleted_worktrees=True) == []
    assert sq.list_runs(db, limit=100, include_deleted_worktrees=True) == []


def test_attached_preview_persists_session_and_run(tmp_path, monkeypatch, isolated_config):
    """Attached preview writes session + runs row with full output."""
    from workagent import cli_harness as h
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _seed_worktree(db)
    out = "harness command: omp foo\nline2\nline3"
    sid = h.record_preview_session("k", "start", "prompt", "omp", out)
    assert sid
    row = sq.get_session(db, sid)
    assert row["worktree_ref"] == "k"
    runs = sq.list_runs(db, limit=100)
    assert len(runs) == 1 and runs[0]["session_id"] == sid
    assert runs[0]["output"] == out


def test_soft_deleted_hidden_from_lists(tmp_path, monkeypatch, isolated_config):
    """Soft-delete hides sessions/runs/flows from default queries."""
    from workagent import cli_harness as h
    from workagent import store as st
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _seed_worktree(db)
    sid = h.record_preview_session("k", "start", "p", "omp", "harness command: x")
    assert sid
    fid = st.create_flow(template_label="t", worktree_ref="k")["id"]
    assert len(sq.list_sessions(db)) == 1
    assert len(sq.list_runs(db, limit=100)) == 1
    assert len(st.list_flows()) == 1
    st.soft_delete_worktree("k")
    assert sq.list_sessions(db) == []
    assert sq.get_session(db, sid) is None
    assert sq.list_runs(db, limit=100) == []
    assert st.list_flows() == []
    assert st.get_flow(fid) is None
    # Opt-in still sees them (restore path).
    assert len(sq.list_sessions(db, include_deleted_worktrees=True)) == 1
    assert len(sq.list_runs(db, limit=100, include_deleted_worktrees=True)) == 1
    assert len(st.list_flows(include_deleted_worktrees=True)) == 1
    assert st.get_flow(fid, include_deleted_worktrees=True) is not None


def test_hard_delete_cascades_sessions_runs_flows(tmp_path, monkeypatch, isolated_config):
    """delete_worktree_row drops sessions+runs (FK CASCADE) and flows."""
    from workagent import cli_harness as h
    from workagent import store as st
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _seed_worktree(db)
    sid = h.record_preview_session("k", "start", "p", "omp", "harness command: x")
    assert sid
    st.create_flow(template_label="t", worktree_ref="k")
    st.delete_worktree_row("k")
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0] == 0


def test_run_output_cap(tmp_path, monkeypatch, isolated_config):
    """Oversized output is capped at MAX_RUN_OUTPUT_LINES with truncated=1."""
    from workagent import store_sqlite as sq
    from workagent import store_sessions as ss
    db = sq.db_path()
    _seed_worktree(db)
    big = [f"line {i}" for i in range(ss.MAX_RUN_OUTPUT_LINES + 500)]
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp",
                            initiator_command="sync", prompt="",
                            file_path="/tmp/s.jsonl")
    rid = sq.insert_run(db, sid, "sync", ["k"], 0, output=big)
    row = [r for r in sq.list_runs(db, limit=100) if r["id"] == rid][0]
    assert row["truncated"] == 1
    assert len(row["output"].splitlines()) == ss.MAX_RUN_OUTPUT_LINES
    assert row["output"].splitlines()[-1] == big[-1]
