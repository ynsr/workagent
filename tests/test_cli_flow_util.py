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

def test_normalize_run_mode_acp_stays_headless():
    from workagent import cli_flow_util as u
    out = u.normalize_config({"run_mode": "acp", "acp": True,
                              "model": "m1", "effort": "high"})
    assert out.get("acp") is True
    assert out.get("model") == "m1"
    assert out.get("effort") == "high"
    assert "terminal" not in out and "preview" not in out


def test_flag_tables_include_acp_model_effort():
    from workagent import cli_flow_util as u
    for table in (u.START_FLAGS, u.REVIEW_FLAGS, u.SYNC_FLAGS,
                  u.ACTION_FLAGS, u.PRODTEST_PLAN_FLAGS, u.RELEASE_FLAGS):
        keys = {k for k, _, _ in table}
        assert {"acp", "model", "effort"} <= keys
        kinds = {k: kind for k, _, kind in table}
        assert kinds["acp"] == "bool"


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


def test_adopt_terminal_transcript_refreshes_agy_meta_stub(tmp_path, monkeypatch):
    import json
    from workagent import cli_flow_util as u, store_sqlite as sq

    db = tmp_path / "s.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    monkeypatch.setattr("workagent.store.config_dir", lambda: tmp_path / "cfg")
    agy_home = tmp_path / "agy-home"
    agy_home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(agy_home))
    sq.init_db(db)

    scope_file, _ = u.mint_terminal_scope("start", branch="feat/stub", harness="agy")
    from pathlib import Path
    Path(scope_file).write_text(
        json.dumps({"type": "agy_meta", "conversation_id": "conv-stub-1"}) + "\n",
        encoding="utf-8",
    )
    brain_log = agy_home / "brain" / "conv-stub-1" / ".system_generated" / "logs"
    brain_log.mkdir(parents=True)
    (brain_log / "transcript.jsonl").write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "stub-refreshed"}) + "\n",
        encoding="utf-8",
    )

    adopted = u.adopt_terminal_transcript(scope_file, 0)
    assert adopted == scope_file
    assert "stub-refreshed" in Path(scope_file).read_text(encoding="utf-8")


def test_adopt_terminal_transcript_resolves_inactive_link_and_issue_ref(tmp_path, monkeypatch):
    import json, sqlite3
    from workagent import cli_flow_util as u, store as _store, store_sqlite as sq

    db = tmp_path / "s.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    monkeypatch.setattr("workagent.store.config_dir", lambda: tmp_path / "cfg")
    agy_home = tmp_path / "agy-home"
    agy_home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(agy_home))

    wt = tmp_path / "wt-inactive"
    wt.mkdir()
    sq.init_db(db)
    _store.record_link(
        "github:o/r#77",
        {
            "worktree": str(wt),
            "branch": "feat/77-inactive",
            "repo": "/tmp/r",
            "issue_url": "https://github.com/o/r/issues/77",
        },
    )
    _store.set_worktree_active("github:o/r#77", False)
    scope_file, _ = u.mint_terminal_scope("start", branch="feat/77-inactive", harness="agy")
    rid = sq.insert_run(
        db, "start", ["start", "https://github.com/o/r/issues/77"], 0,
        worktree_ref=None, harness_name="agy", initiator_command="start",
        prompt="p", file_path=scope_file, session_type="start", state="preview",
    )

    brain_log = agy_home / "brain" / "conv-inact-77" / ".system_generated" / "logs"
    brain_log.mkdir(parents=True)
    (brain_log / "transcript.jsonl").write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "inactive-adopted"}) + "\n",
        encoding="utf-8",
    )
    sum_db = agy_home / "conversation_summaries.db"
    with sqlite3.connect(sum_db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?)",
            ("conv-inact-77", json.dumps([wt.resolve().as_uri()]), "2099-01-01T00:00:00+00:00"),
        )

    adopted = u.adopt_terminal_transcript(scope_file, rid)
    assert adopted == scope_file
    assert "inactive-adopted" in (tmp_path / scope_file).read_text(encoding="utf-8")


def test_run_sync_step_forwards_harness_and_branch_to_spawn(tmp_path, monkeypatch):
    from workagent import cli_flow_steps as steps, store as _store

    monkeypatch.setattr("workagent.store.config_dir", lambda: tmp_path / "cfg")
    _store.record_link("wt-sync-1", {"worktree": "/tmp/wt-sync", "branch": "feat/sync-h", "repo": "/tmp/r"})
    spawned = []
    out = steps.run_sync_step(
        "wt-sync-1",
        {"terminal": True, "harness": "omp"},
        False,
        lambda *a, **k: (spawned.append((a, k)), {"preview": True})[1],
        lambda *a, **k: {},
    )
    assert out == {"preview": True}
    assert spawned[0][0] == ("sync", ["wt-sync-1", "--harness", "omp"], "/tmp/wt-sync")
    assert spawned[0][1]["harness"] == "omp"
    assert spawned[0][1]["branch"] == "feat/sync-h"




def test_flow_step_acp_does_not_pass_launch_true(monkeypatch):
    from workagent import cli_flow_steps, cli_start
    seen = {}
    monkeypatch.setattr(cli_start.start, "__wrapped__", lambda *a, **kw: seen.update(kw))
    cli_flow_steps.run_start_step("github:o/r#1", "", {"acp": True, "model": "m1"},
                                  json_output=True, spawn=None, manual=None)
    assert seen["acp"] is True
    assert seen["launch"] is False
    assert seen["model"] == "m1"


def test_release_fix_default_accepts_acp_model_effort():
    from workagent import cli_release_fix, web_args
    assert "acp" in cli_release_fix.RUN_MODES
    assert "--acp" in web_args.BOOL_FLAGS["release"]
    assert "--model" in web_args.VAL_FLAGS["release"]
    assert "--effort" in web_args.VAL_FLAGS["release"]


def test_web_args_rejects_acp_with_short_L_or_conflicting_run_mode():
    import pytest
    from workagent.web_args import ApiError, _validate_args
    with pytest.raises(ApiError) as e1:
        _validate_args("start", ["github:o/r#1", "--acp", "-L"])
    assert e1.value.status == 400
    with pytest.raises(ApiError) as e2:
        _validate_args("start", ["github:o/r#1", "--acp", "--run-mode", "preview"])
    assert e2.value.status == 400
