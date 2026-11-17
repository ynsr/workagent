"""F1/F5 helper tests: run_mode + extra-flags translation, banned scanner."""
from __future__ import annotations


def test_normalize_run_mode_preview_and_terminal():
    from workagent import cli_flow_util as u
    assert u.normalize_config({"run_mode": "preview"}) == {"preview": True}
    assert u.normalize_config({"run_mode": "terminal"}) == {"terminal": True}
    assert u.normalize_config({"run_mode": "dry_run"}) == {"dry_run": True}
    # headless strips manual flags inherited from the template
    assert u.normalize_config({"run_mode": "headless", "preview": True}) == {}
    # no run_mode: passthrough
    assert u.normalize_config({"preview": True}) == {"preview": True}


def test_normalize_extra_flags_parses_kv_and_boolean():
    from workagent import cli_flow_util as u
    out = u.normalize_config({"extra-flags": "--depth 7 --merge"})
    assert out == {"depth": 7, "merge": True}
    out = u.normalize_config({"extra-flags": "--repo=main"})
    assert out == {"repo": "main"}
    # dashed keys normalize to the underscore names _pick allowlists
    out = u.normalize_config({"extra-flags": "--no-tty --related-repo proj --extra-prompt=hi"})
    assert out == {"no_tty": True, "related_repo": ["proj"], "extra_prompt": "hi"}
    # related_repo as direct key (not via extra-flags) coerces too; lists pass through
    assert u.normalize_config({"related_repo": "a, b"}) == {"related_repo": ["a", "b"]}
    assert u.normalize_config({"related_repo": ["a"]}) == {"related_repo": ["a"]}
    assert u.normalize_config({"related_repo": "  "}) == {}
    # underscore variant accepted too
    out = u.normalize_config({"extra_flags": "--depth 7"})
    assert out == {"depth": 7}
    # combined with run_mode in one override
    out = u.normalize_config({"run_mode": "preview", "extra-flags": "--depth 7"})
    assert out == {"preview": True, "depth": 7}


def test_has_banned_scans_keys_and_values_without_blob_false_positives():
    from workagent import cli_flow_util as u
    from workagent import store_flows as sf
    # key hit
    assert u.has_banned({"then_fix_comments": True}, sf._BANNED_STEP_FLAGS)
    # nested value hit
    assert u.has_banned({"flags": "--then-fix-comments"}, sf._BANNED_STEP_FLAGS)
    # innocent prose mentioning extra_prompt must NOT trip
    assert u.has_banned({"extra_prompt": "then fix comments later"}, sf._BANNED_STEP_FLAGS) is None
    assert u.has_banned({"extra_prompt": "mention then-fix-comments"}, sf._BANNED_STEP_FLAGS) == "then-fix-comments"
    # cleanup flags
    assert u.has_banned({"hard_delete": True}, sf._BANNED_CLEANUP_FLAGS)
    assert u.has_banned({}, sf._BANNED_CLEANUP_FLAGS) is None


def test_terminal_capable_includes_prodtest():
    from workagent import cli_flow_util as u
    assert "prodtest" in u.TERMINAL_CAPABLE
    assert u.TERMINAL_CAPABLE == ("start", "review", "action", "prodtest")


def test_persist_terminal_pause_records_harness_name(monkeypatch):
    from workagent import cli_flow_util as u
    from workagent import cli_harness as h
    recorded = []
    monkeypatch.setattr(h, "record_headless_attempt",
                        lambda *a, **k: recorded.append((a, k)) or 42)
    rid = u.persist_terminal_pause("key-1", "start", ["start", "key-1"], "cmd", 1,
                                   harness_name="omp")
    assert rid == 42
    assert recorded[0][1].get("harness_name") == "omp"


def test_adopt_terminal_transcript_adopts_agy_workspace(tmp_path, monkeypatch):
    import json
    import sqlite3
    from workagent import cli_flow_util as u, store as _store, store_sqlite as sq

    db = tmp_path / "s.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    monkeypatch.setattr("workagent.store.config_dir", lambda: tmp_path / "cfg")
    agy_home = tmp_path / "agy-home"
    agy_home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(agy_home))

    wt = tmp_path / "wt-agy"
    wt.mkdir()
    _store.record_link("wt-agy-key", {"worktree": str(wt), "branch": "feat/agy", "repo": "/tmp/r"})
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('wt-agy-key', ?, 'feat/agy', NULL, '2026-01-01', '{}')",
            (str(wt),),
        )

    scope_file, _scope_dir = u.mint_terminal_scope("start", branch="feat/agy", harness="agy")
    rid = sq.insert_run(
        db, "start", ["wt-agy-key"], 0, worktree_ref="wt-agy-key",
        harness_name="agy", initiator_command="start", prompt="p",
        file_path=scope_file, session_type="start", state="preview",
    )

    brain_log = agy_home / "brain" / "conv-flow-1" / ".system_generated" / "logs"
    brain_log.mkdir(parents=True)
    (brain_log / "transcript.jsonl").write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "flow-done"}) + "\n",
        encoding="utf-8",
    )
    sum_db = agy_home / "conversation_summaries.db"
    with sqlite3.connect(sum_db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?)",
            ("conv-flow-1", json.dumps([wt.resolve().as_uri()]), "2099-01-01T00:00:00+00:00"),
        )

    adopted = u.adopt_terminal_transcript(scope_file, rid)
    assert adopted == scope_file
    row = sq.get_run(db, rid, include_deleted_worktrees=True)
    assert (row or {}).get("file_path") == scope_file
    assert ((row or {}).get("metadata") or {}).get("conversation_id") == "conv-flow-1"
    assert "flow-done" in (tmp_path / scope_file).read_text(encoding="utf-8")

