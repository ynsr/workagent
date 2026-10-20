"""Final-review gap tests: F3 tagging, F4 then_fix_comments ban, F6/F7 flows."""
from __future__ import annotations


def _invoke(args, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    return CliRunner().invoke(cli_mod.app, args)


def test_run_rejects_step_config_then_fix_comments(tmp_path, monkeypatch):
    """F4: --step-config cannot smuggle then_fix_comments past the flow ban."""
    import workagent.cli_flow as cli_mod
    tpl = {"label": "e2e", "name": "E2E",
           "steps": [{"kind": "start"}, {"kind": "review"},
                     {"kind": "fix_comments"}, {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda l: {"label": l})
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [])
    created = []
    monkeypatch.setattr("workagent.store.create_flow",
                        lambda **f: created.append(f) or {"id": 1, **f})
    res = _invoke(["flow", "run", "e2e", "jira:X-1", "--yes",
                   "--step-config", '{"1": {"then_fix_comments": true}}'],
                  monkeypatch)
    assert res.exit_code == 2, res.output
    assert created == []


def test_tag_flow_session_stamps_newest(tmp_path, monkeypatch):
    """F3: the just-minted step session (newest first) gets the flow_id."""
    import workagent.cli_flow as m
    import workagent.store_sqlite as sq
    # list_sessions is ORDER BY created_at DESC (newest first).
    sessions = [{"id": "new", "worktree_ref": "wt-1"},
                {"id": "old", "worktree_ref": "wt-1"}]
    monkeypatch.setattr(sq, "list_sessions", lambda db, **k: sessions)
    stamped = {}
    monkeypatch.setattr(sq, "set_session_metadata",
                        lambda db, sid, patch: stamped.update({sid: patch}))
    m._tag_flow_session("wt-1", 9)
    assert stamped == {"new": {"flow_id": 9}}


def test_run_fresh_forces_new_row(tmp_path, monkeypatch):
    """F6: --fresh skips resume and creates a new flow row."""
    import workagent.cli_flow as cli_mod
    tpl = {"label": "e2e", "name": "E2E",
           "steps": [{"kind": "start", "config": {"preview": True}}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda l: {"label": l})
    existing = {"id": 7, "template_label": "e2e", "worktree_ref": "",
                "repo_snapshot": {"launch_ref": "jira:X-1"},
                "status": "paused", "current_step": 0, "step_results": []}
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [existing])
    created = []
    monkeypatch.setattr("workagent.store.create_flow",
                        lambda **f: created.append(f) or {"id": 8, **f,
                            "status": "pending", "current_step": 0,
                            "step_results": [], "repo_snapshot": {}})
    monkeypatch.setattr("workagent.store.update_flow",
                        lambda fid, **f: {"id": fid, **f})
    monkeypatch.setattr(cli_mod, "_run_start_step",
                        lambda *a, **k: {"key": "", "preview": True,
                                         "command": "workagent start ..."})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda *a, **k: ("", {}, {"kind": "issue"}))
    res = _invoke(["flow", "run", "e2e", "jira:X-1", "--yes", "--fresh"],
                  monkeypatch)
    assert res.exit_code == 0, res.output
    assert len(created) == 1


def test_resume_matches_launch_ref_not_first_row(monkeypatch):
    """F7: cross-issue resume — T on B must not adopt T-on-A's paused row."""
    import workagent.cli_flow as m
    import workagent.store as st
    flows = [
        {"id": 1, "template_label": "t", "worktree_ref": "",
         "repo_snapshot": {"launch_ref": "jira:A-1"}, "status": "paused"},
        {"id": 2, "template_label": "t", "worktree_ref": "",
         "repo_snapshot": {"launch_ref": "jira:B-2"}, "status": "paused"},
    ]
    monkeypatch.setattr(st, "list_flows", lambda **k: flows)
    assert m._find_resumable("t", "jira:B-2", "")["id"] == 2
    assert m._find_resumable("t", "jira:A-1", "")["id"] == 1
    assert m._find_resumable("t", "jira:C-3", "") is None


def test_release_lock_skip_returns_skipped(tmp_path, monkeypatch):
    """F6: a locked repo short-circuits the release step (no runner call)."""
    import workagent.cli_flow as m
    import workagent.cli_release as r
    import workagent.release_lock as lk
    import workagent.release_util as ut
    monkeypatch.setattr(ut, "worktree_base", lambda: "/tmp/wtbase")
    monkeypatch.setattr(lk, "lock_path", lambda base, repo: f"{base}/{repo}")
    monkeypatch.setattr(lk, "locked", lambda lock: True)
    calls = []
    monkeypatch.setattr(r, "run_release",
                        lambda *a, **k: calls.append((a, k)) or {"merged": True})
    out = m._run_release_step({"repo": "myrepo"}, {}, False)
    assert out.get("skipped") == "release-locked"
    assert calls == []


def test_pre_post_ordering_with_deferred_pre(tmp_path, monkeypatch):
    """F6: pre runs (deferred until Start mints the worktree), then post."""
    import workagent.cli_flow as cli_mod
    tpl = {"label": "e2e", "name": "E2E", "pre_execution": "make lint",
           "post_execution": "make test",
           "steps": [{"kind": "start"}, {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda l: {"label": l})
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [])
    monkeypatch.setattr("workagent.store.create_flow",
                        lambda **f: {"id": 5, "template_label": "e2e",
                                     "worktree_ref": "", "repo_snapshot": {},
                                     "status": "pending", "current_step": 0,
                                     "step_results": []})
    states = {}
    monkeypatch.setattr("workagent.store.update_flow",
                        lambda fid, **f: states.update(f) or {"id": fid, **f})
    monkeypatch.setattr("workagent.store.load_repos", lambda: {})
    links = {"wt-1": {"worktree": "/tmp/wt-1",
                      "repo": "r", "branch": "b"}}
    seen = {"n": 0}

    # NB: _resolve_ref is mocked (no load_links there); the first three real
    # load_links calls are the snapshot seed + resume backfill (both
    # include_deleted=True) + the pre-step read — all must see no link so
    # pre stays deferred until the start step mints the worktree.
    def _links(*a, **k):
        seen["n"] += 1
        return {} if seen["n"] <= 3 else links
    monkeypatch.setattr("workagent.store.load_links", _links)
    order = []
    monkeypatch.setattr(cli_mod, "_run_repo_command",
                        lambda repo, phase, cmd, cwd: order.append(phase))
    monkeypatch.setattr(cli_mod, "_run_start_step",
                        lambda *a, **k: {"key": "wt-1", "status": "started"})
    monkeypatch.setattr(cli_mod, "_run_cleanup_step",
                        lambda *a, **k: {"status": "cleaned"})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda *a, **k: ("", {}, {"kind": "issue"}))
    res = _invoke(["flow", "run", "e2e", "jira:X-1", "--yes", "--json"],
                  monkeypatch)
    assert res.exit_code == 0, res.output
    assert order == ["pre-execution", "post-execution"]


def test_soft_delete_hides_flow_undelete_restores(tmp_path, monkeypatch):
    """F6: soft-deleted worktrees hide their flows; undelete restores."""
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    import workagent.store_sqlite as sq
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('wt-1', '/tmp/wt-1', 'b', NULL, '2026-01-01', '{}')")
    sf.create_template(db, name="E2E",
                       steps='[{"kind": "start"}, {"kind": "cleanup"}]')
    sf.create_flow(db, template_label="e2e", worktree_ref="wt-1")
    assert len(sf.list_flows(db)) == 1
    sq.soft_delete_worktree(db, "wt-1")
    assert sf.list_flows(db) == []
    assert len(sf.list_flows(db, include_deleted_worktrees=True)) == 1
    sq.undelete_worktree(db, "wt-1")
    assert len(sf.list_flows(db)) == 1

def test_terminal_start_forwards_repo_flags(monkeypatch):
    """Terminal/manual start+review steps must carry --repo/--related-repo.

    Regression: the spawned terminal ran bare `workagent start <ref>`,
    so a multi-repo tracker (jira:IPG → 5 repos) errored with
    "linked to multiple repos" even though the step config picked one.
    """
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as m
    spawned = {}
    monkeypatch.setattr("workagent.cli_harness.spawn_in_terminal",
                        lambda cmd, cwd="": spawned.update(cmd=cmd) or 4242)
    out = m._run_start_step(
        "jira:IPG-1052", "",
        {"terminal": True, "repo": "projectx",
         "related_repo": ["ipg-commons"]}, False)
    assert out["preview"] is True and out["terminal_pid"] == 4242
    assert "--repo projectx" in spawned["cmd"]
    assert "--related-repo ipg-commons" in spawned["cmd"]
    out = m._run_start_step("jira:IPG-1052", "", {"preview": True}, False)
    assert out["preview"] is True and "manual_args" in out
    out = m._run_review_step(
        "wt-1", {"terminal": True, "repo": "projectx",
                 "use_review_session": True}, True, False)
    assert "--repo projectx" in out["command"]
    assert "--fix-comments" in out["command"]
    assert "--use-review-session" in out["command"]


def test_terminal_spawn_appends_terminal_flag(monkeypatch):
    """Terminal steps must spawn with --terminal so the child really runs.

    Regression: the flow spawned `workagent start <ref> --repo …` without
    --terminal, so the child ran its preview branch and only printed the
    harness command — the opened terminal never launched the agent.
    sync/cleanup/release have no --terminal flag: they degrade to a
    manual pause with the command printed.
    """
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as m
    import workagent.cli_flow_util as u
    spawned = {}
    monkeypatch.setattr("workagent.cli_harness.spawn_in_terminal",
                        lambda cmd, cwd="": spawned.update(cmd=cmd) or 4242)
    out = m._run_start_step("jira:IPG-1052", "",
                            {"terminal": True, "repo": "projectx"}, False)
    assert out["terminal_pid"] == 4242
    assert spawned["cmd"].rstrip().endswith("--terminal")
    assert "--repo projectx" in spawned["cmd"]
    out = m._run_review_step("wt-1", {"terminal": True}, False, False)
    assert out["command"].rstrip().endswith("--terminal")
    out = u.spawn_terminal_step("sync", ["wt-1"], "")
    assert "--terminal" not in out["command"]
    assert out["command"] == "workagent sync wt-1"
    out = u.spawn_terminal_step("cleanup", ["wt-1"], "")
    assert "--terminal" not in out["command"]
    out = u.spawn_terminal_step("release", ["myrepo"], "")
    assert "--terminal" not in out["command"]
