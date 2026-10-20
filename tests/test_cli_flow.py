"""CLI flow tests (Task 2 TDD)."""


def test_run_dry_run_prints_chain(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    import json as _json
    tpl = {"label": "e2e", "name": "End to End",
           "steps": [{"kind": "start"}, {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["flow", "run", "e2e", "jira:X-1", "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    body = _json.loads(res.stdout)
    assert [s["kind"] for s in body["chain"]] == ["start", "cleanup"]


def test_run_unknown_action_label_fails_fast(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    tpl = {"label": "bad", "name": "Bad",
           "steps": [{"kind": "start"}, {"kind": "action", "action_label": "gone"},
                     {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda l: None)
    created = []
    monkeypatch.setattr("workagent.store.create_flow",
                        lambda **f: created.append(f) or {"id": 1, **f})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["flow", "run", "bad", "jira:X-1", "--yes"])
    assert res.exit_code == 2
    assert created == []


def test_run_resume_cursor_skips_green_steps(tmp_path, monkeypatch):
    """Seed a failed flow at current_step=1; step 0 must not re-execute."""
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    tpl = {"label": "e2e", "name": "End to End",
           "steps": [{"kind": "start"}, {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    existing = {"id": 7, "template_label": "e2e", "worktree_ref": "wt-1",
                "repo_snapshot": {"repo": "r", "default_branch": "main",
                                  "release_branch": ""},
                "status": "failed", "current_step": 1,
                "step_results": [{"kind": "start", "status": "ok", "detail": ""}]}
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [existing])
    updated = {}

    def _update(fid, **fields):
        updated.update(fields)
        return {**existing, **updated}

    monkeypatch.setattr("workagent.store.update_flow", _update)
    monkeypatch.setattr("workagent.store.get_flow",
                        lambda fid: {**existing, **updated})
    ran = []
    monkeypatch.setattr(cli_mod, "_run_start_step",
                        lambda *a, **k: ran.append("start") or {"key": "wt-1", "preview": False})
    monkeypatch.setattr(cli_mod, "_run_cleanup_step",
                        lambda *a, **k: ran.append("cleanup") or {"status": "cleaned"})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda *a, **k: ("wt-1", {"worktree": "/tmp/wt",
                                              "repo": "r", "branch": "b",
                                              "active": 1}, None))
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "e2e", "wt-1", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert ran == ["cleanup"]


def test_run_rejects_cleanup_override(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    tpl = {"label": "e2e", "name": "End to End",
           "steps": [{"kind": "start"}, {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda l: {"label": l})
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [])
    created = []
    monkeypatch.setattr("workagent.store.create_flow",
                        lambda **f: created.append(f) or {"id": 1, **f})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "e2e", "jira:X-1", "--yes",
                         "--step-config", '{"1": {"hard_delete": true}}'])
    assert res.exit_code == 2, res.output
    assert created == []


def test_run_paused_on_preview(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    tpl = {"label": "solo", "name": "Solo",
           "steps": [{"kind": "start", "config": {"preview": True}}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda l: {"label": l})
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [])
    monkeypatch.setattr("workagent.store.create_flow",
                        lambda **f: {"id": 3, "template_label": "solo",
                                     "worktree_ref": "", "repo_snapshot": {},
                                     "status": "pending", "current_step": 0,
                                     "step_results": []})
    states = {}
    monkeypatch.setattr("workagent.store.update_flow",
                        lambda fid, **f: states.update(f) or {"id": fid, **f})
    monkeypatch.setattr(cli_mod, "_run_start_step",
                        lambda *a, **k: {"key": "wt-9", "preview": True,
                                     "command": "omp run ..."})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda *a, **k: ("", {}, {"kind": "issue"}))
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "solo", "jira:X-1", "--yes"])
    assert res.exit_code == 0, res.output
    assert states.get("status") == "paused"



def test_seam_start_calls_runner_headless_skips_preview(monkeypatch):
    import workagent.cli_flow as m
    import workagent.cli_start as s
    calls = []
    monkeypatch.setattr(s.start, "__wrapped__",
                        lambda *a, **k: calls.append((a, k)) or None)
    monkeypatch.setattr(m, "_resolve_ref", lambda ref: (ref, {}, None))
    out = m._run_start_step("jira:X-1", "wt-1", {"launch": True}, False)
    assert calls and calls[0][0][0] == "jira:X-1"
    assert calls[0][1].get("launch") is True and calls[0][1].get("no_tty") is True
    assert "preview" not in out and out["status"] == "started"
    before = len(calls)
    out = m._run_start_step("jira:X-1", "wt-1", {"launch": False}, False)
    assert out["preview"] is True and out["command"]
    assert len(calls) == before


def test_seam_review_calls_runner_headless_skips_preview(monkeypatch):
    import workagent.cli_flow as m
    import workagent.cli_review as r
    calls = []
    monkeypatch.setattr(r.review, "__wrapped__",
                        lambda *a, **k: calls.append((a, k)) or None)
    out = m._run_review_step("wt-1", {"launch": True}, False, False)
    assert calls and calls[0][1].get("fix_comments") is False
    assert calls[0][1].get("launch") is True and calls[0][1].get("no_tty") is True
    assert "preview" not in out and out["status"] == "reviewed"
    before = len(calls)
    out = m._run_review_step("wt-1", {"launch": True}, True, False)
    assert calls and calls[-1][1].get("fix_comments") is True
    out = m._run_review_step("wt-1", {"terminal": True}, False, False)
    assert out["preview"] is True and out["command"]
    assert len(calls) == before + 1


def test_seam_sync_calls_runner_headless_skips_preview(monkeypatch):
    import workagent.cli_flow as m
    import workagent.cli_sync as s
    calls = []
    monkeypatch.setattr(s.sync_cmd, "__wrapped__",
                        lambda *a, **k: calls.append((a, k)) or None)
    out = m._run_sync_step("wt-1", {"merge": True}, False)
    assert calls and calls[0][0][0] == "wt-1"
    assert calls[0][1].get("merge") is True
    assert "preview" not in out and out["status"] == "synced"
    before = len(calls)
    out = m._run_sync_step("wt-1", {"dry_run": True}, False)
    assert out["preview"] is True and out["command"]
    assert len(calls) == before


def test_seam_action_calls_runner_headless_skips_preview(monkeypatch):
    import workagent.cli_flow as m
    import workagent.cli_action as a
    import workagent.store as st
    monkeypatch.setattr(st, "get_action", lambda label: {"label": label})
    calls = []
    monkeypatch.setattr(a.action_run, "__wrapped__",
                        lambda *a_, **k: calls.append((a_, k)) or None)
    out = m._run_action_step("wt-1", "gen", {"launch": True}, False)
    assert calls and calls[0][0][:2] == ("gen", "wt-1")
    assert "preview" not in out and out["status"] == "acted"
    before = len(calls)
    out = m._run_action_step("wt-1", "gen", {"launch": False}, False)
    assert out["preview"] is True and len(calls) == before


def test_seam_cleanup_calls_cleanup_one(monkeypatch):
    import workagent.cli_flow as m
    import workagent.cli_cleanup as c
    import workagent.store as st
    calls = []
    monkeypatch.setattr(st, "load_links",
                        lambda *a, **k: {"wt-1": {"worktree": "/tmp/wt",
                                                 "repo": "r", "branch": "b"}})
    monkeypatch.setattr(c, "_cleanup_one",
                        lambda *a, **k: calls.append((a, k)) or {"status": "x"})
    out = m._run_cleanup_step("wt-1", {}, False)
    assert calls and calls[0][0][0] == "wt-1"
    assert calls[0][1].get("hard_delete") is False
    assert "preview" not in out and out["status"] == "cleaned"


def test_seam_release_calls_run_release_skips_preview(monkeypatch):
    import workagent.cli_flow as m
    import workagent.cli_release as r
    import workagent.release_lock as lk
    import workagent.release_util as ut
    monkeypatch.setattr(ut, "worktree_base", lambda: "/tmp/wtbase")
    monkeypatch.setattr(lk, "lock_path", lambda base, repo: f"{base}/{repo}")
    monkeypatch.setattr(lk, "locked", lambda lock: False)
    calls = []
    monkeypatch.setattr(r, "run_release",
                        lambda *a, **k: calls.append((a, k)) or {"merged": True})
    out = m._run_release_step({"repo": "myrepo"}, {}, False)
    assert calls and calls[0][0][0] == "myrepo"
    assert "preview" not in out and out["status"]
    before = len(calls)
    out = m._run_release_step({"repo": "myrepo"}, {"preview": True}, False)
    assert out["preview"] is True and len(calls) == before


def test_action_step_skip_exception_maps_to_skipped(monkeypatch):
    import workagent.cli_action as a
    import workagent.cli_flow as m
    import workagent.store as st
    from workagent.release_gates import SkippedGate
    monkeypatch.setattr(st, "get_action", lambda label: {"label": label})
    def _raise(*a_, **k):
        raise SkippedGate("nothing-ahead")
    monkeypatch.setattr(a.action_run, "__wrapped__", _raise)
    out = m._run_action_step("wt-1", "myact", {"launch": True}, False)
    assert out == {"key": "wt-1", "label": "myact",
                   "status": "skipped", "detail": "nothing-ahead"}


def test_action_step_skipped_dict_maps_to_skipped(monkeypatch):
    import workagent.cli_action as a
    import workagent.cli_flow as m
    import workagent.store as st
    monkeypatch.setattr(st, "get_action", lambda label: {"label": label})
    monkeypatch.setattr(a.action_run, "__wrapped__",
                        lambda *a_, **k: {"label": "myact", "key": "wt-1",
                                          "skipped": "on-base"})
    out = m._run_action_step("wt-1", "myact", {"launch": True}, False)
    assert out == {"key": "wt-1", "label": "myact",
                   "status": "skipped", "detail": "on-base"}


def test_run_action_skip_records_skipped_and_continues(monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    import json as _json
    tpl = {"label": "skipflow", "name": "Skip Flow",
           "steps": [{"kind": "start"},
                     {"kind": "action", "action_label": "myact"},
                     {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda l: {"label": l})
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [])
    monkeypatch.setattr("workagent.store.create_flow",
                        lambda **f: {"id": 9, "template_label": "skipflow",
                                     "worktree_ref": "wt-1",
                                     "repo_snapshot": {"repo": "r", "launch_ref": "wt-1",
                                                       "default_branch": "main",
                                                       "release_branch": ""},
                                     "status": "pending", "current_step": 0,
                                     "step_results": []})
    updates = []
    def _update(fid, **fields):
        updates.append({"id": fid, **fields})
        return updates[-1]
    monkeypatch.setattr("workagent.store.update_flow", _update)
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda ref: ("wt-1", {"worktree": "/tmp/wt", "repo": "r",
                                             "branch": "b", "active": 1}, None))
    monkeypatch.setattr(cli_mod, "_run_start_step",
                        lambda *a, **k: {"key": "wt-1", "status": "started"})
    monkeypatch.setattr(cli_mod, "_run_action_step",
                        lambda *a, **k: {"key": "wt-1", "label": "myact",
                                         "status": "skipped",
                                         "detail": "nothing-ahead"})
    ran = []
    monkeypatch.setattr(cli_mod, "_run_cleanup_step",
                        lambda *a, **k: ran.append("cleanup") or {"key": "wt-1",
                                                                  "status": "cleaned"})
    monkeypatch.setattr(cli_mod, "_tag_flow_session", lambda *a, **k: None)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "skipflow", "wt-1", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert ran == ["cleanup"]
    done = [u for u in updates if u.get("status") == "done"][-1]
    assert done["step_results"] == [
        {"kind": "start", "status": "ok", "detail": "started"},
        {"kind": "action", "status": "skipped", "detail": "nothing-ahead"},
        {"kind": "cleanup", "status": "ok", "detail": "cleaned"}]
    assert _json.loads(res.stdout) == {"flow": 9, "status": "done", "steps": 3}


def test_template_update_new_label_passed_through(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    import json as _json
    seen = {}

    def _update(label, **fields):
        seen.update({"label": label, **fields})
        return {"id": 1, "label": fields.get("new_label", label),
                "name": "Pipe"}

    monkeypatch.setattr("workagent.store.update_template", _update)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "template", "update", "pipe",
                         "--new-label", "pipe_v2", "--json"])
    assert res.exit_code == 0, res.output
    assert seen == {"label": "pipe", "new_label": "pipe_v2"}
    assert _json.loads(res.stdout)["updated"] == "pipe_v2"


def test_terminal_action_step_spawns_and_pauses(monkeypatch):
    """run_mode: terminal routes through spawn_in_terminal, then pauses."""
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    monkeypatch.setattr("workagent.store.get_action",
                        lambda label: {"label": label})
    monkeypatch.setattr("workagent.store.load_links",
                        lambda *a, **k: {"wt-1": {"worktree": "/tmp/wt"}})
    spawned = {}
    monkeypatch.setattr("workagent.cli_harness.spawn_in_terminal",
                        lambda cmd, cwd="": spawned.update(cmd=cmd, cwd=cwd) or 4242)
    out = cli_mod._run_action_step(
        "wt-1", "myact", {"terminal": True}, False)
    assert out["preview"] is True
    assert out["terminal_pid"] == 4242
    assert spawned["cmd"] == "workagent action run myact wt-1"
    assert spawned["cwd"] == "/tmp/wt"


def test_terminal_spawn_failure_degrades_to_manual_pause(monkeypatch):
    """No emulator/display: pause carries the command + the spawn error."""
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    monkeypatch.setattr("workagent.store.get_action",
                        lambda label: {"label": label})
    monkeypatch.setattr("workagent.store.load_links", lambda *a, **k: {})

    def _boom(cmd, cwd=""):
        raise RuntimeError("no terminal emulator found")

    monkeypatch.setattr("workagent.cli_harness.spawn_in_terminal", _boom)
    out = cli_mod._run_action_step(
        "wt-1", "myact", {"terminal": True}, False)
    assert out["preview"] is True
    assert "terminal_pid" not in out
    assert "no terminal emulator" in str(out.get("terminal_error", ""))


def test_skip_steps_marks_resume_step_done(monkeypatch):
    """--skip-steps advances the cursor without executing the step."""
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    import json as _json
    tpl = {"label": "pipe", "name": "Pipe",
           "steps": [{"kind": "start"}, {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    existing = {"id": 11, "template_label": "pipe", "worktree_ref": "wt-1",
                "repo_snapshot": {"repo": "r", "default_branch": "main",
                                  "release_branch": ""},
                "status": "paused", "current_step": 0,
                "step_results": [{"kind": "start", "status": "paused",
                                  "detail": "workagent start jira:X-1"}]}
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [existing])
    updates = []

    def _update(fid, **fields):
        updates.append({"fid": fid, **fields})
        return {**existing, **updates[-1]}

    monkeypatch.setattr("workagent.store.update_flow", _update)
    monkeypatch.setattr("workagent.store.get_flow",
                        lambda fid: {**existing, **updates[-1]})
    ran = []
    monkeypatch.setattr(cli_mod, "_run_start_step",
                        lambda *a, **k: ran.append("start") or {"status": "x"})
    monkeypatch.setattr(cli_mod, "_run_cleanup_step",
                        lambda *a, **k: ran.append("cleanup") or {"status": "cleaned"})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda ref: ("wt-1", {"worktree": "/tmp/wt", "repo": "r",
                                             "branch": "b", "active": 1}, None))
    monkeypatch.setattr(cli_mod, "_tag_flow_session", lambda *a, **k: None)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "pipe", "wt-1", "--yes",
                         "--skip-steps", "0", "--json"])
    assert res.exit_code == 0, res.output
    assert ran == ["cleanup"]  # step 0 never re-executed
    done = [u for u in updates if u.get("status") == "done"][-1]
    kinds = [(r["kind"], r["status"]) for r in done["step_results"]]
    assert kinds[0] == ("start", "skipped")
    assert kinds[1] == ("cleanup", "ok")
    assert _json.loads(res.stdout) == {"flow": 11, "status": "done", "steps": 2}


def test_skip_steps_rejects_future_step(monkeypatch):
    """Only the resume cursor can be marked done — no jumping ahead."""
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    tpl = {"label": "pipe", "name": "Pipe",
           "steps": [{"kind": "start"}, {"kind": "cleanup"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    existing = {"id": 12, "template_label": "pipe", "worktree_ref": "wt-1",
                "repo_snapshot": {}, "status": "paused", "current_step": 0,
                "step_results": []}
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [existing])
    monkeypatch.setattr("workagent.store.update_flow",
                        lambda fid, **f: {**existing, **f})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda ref: ("wt-1", {}, None))
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "pipe", "wt-1", "--yes",
                         "--skip-steps", "1"])
    assert res.exit_code == 2
    assert "beyond the resume cursor" in res.output


def test_skip_steps_rejects_out_of_range(monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    tpl = {"label": "pipe", "name": "Pipe",
           "steps": [{"kind": "start"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    existing = {"id": 13, "template_label": "pipe", "worktree_ref": "wt-1",
                "repo_snapshot": {}, "status": "paused", "current_step": 0,
                "step_results": []}
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [existing])
    monkeypatch.setattr("workagent.store.update_flow",
                        lambda fid, **f: {**existing, **f})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda ref: ("wt-1", {}, None))
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "pipe", "wt-1", "--yes",
                         "--skip-steps", "5"])
    assert res.exit_code == 2
    assert "out of range" in res.output


def test_no_start_template_seeds_repo_snapshot(monkeypatch):
    """cleanup_release (no start step) must still seed repo_snapshot so the
    release step finds its repo (flow 26 root cause: snapshot stayed
    {'launch_ref'} → 'flow has no repo snapshot — cannot release')."""
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    tpl = {"label": "norun", "name": "NoRun",
           "steps": [{"kind": "sync"}, {"kind": "cleanup"},
                     {"kind": "release"}]}
    monkeypatch.setattr("workagent.store.get_template", lambda l: tpl)
    created = {}

    def _create(**fields):
        created.update(fields)
        return {"id": 21, **fields, "status": "running",
                "current_step": 0, "step_results": []}

    monkeypatch.setattr("workagent.store.create_flow", _create)
    monkeypatch.setattr("workagent.store.list_flows", lambda **k: [])
    monkeypatch.setattr("workagent.store.update_flow",
                        lambda fid, **f: {"id": fid, **f})
    monkeypatch.setattr("workagent.store.get_flow",
                        lambda fid, **k: {"id": fid,
                                          "repo_snapshot": created.get("repo_snapshot", {})})
    monkeypatch.setattr(cli_mod, "_resolve_ref",
                        lambda ref: ("wt-1", {"repo": "r",
                                             "worktree": "/tmp/wt",
                                             "branch": "b"}, None))
    monkeypatch.setattr("workagent.store.load_repos",
                        lambda: {"r": {"default_branch": "main",
                                      "release_branch": "rel"}})
    monkeypatch.setattr(cli_mod, "_run_sync_step",
                        lambda *a, **k: (_ for _ in ()).throw(
                            cli_mod.HarnessError("stop after seed", 1)))
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["flow", "run", "norun", "wt-1", "--yes"])
    assert res.exit_code == 1  # halted at sync — after the seed ran
    assert created["repo_snapshot"]["repo"] == "r"
    assert created["repo_snapshot"]["default_branch"] == "main"
    assert created["repo_snapshot"]["launch_ref"] == "wt-1"


def test_resume_after_soft_delete_keeps_repo_snapshot(monkeypatch):
    """Resume of a flow whose worktree was soft-deleted must still see the
    key and the seeded snapshot (flow 26 resume: deleted row hidden from
    load_links/list_flows/get_flow defaults)."""
    import workagent.cli_flow as cli_mod
    monkeypatch.setattr("workagent.store.load_links",
                        lambda **k: {"wt-9": {"repo": "r"}} if k.get("include_deleted") else {})
    key, entry, _ = cli_mod._resolve_ref("wt-9")
    assert key == "wt-9"
    assert entry.get("repo") == "r"
