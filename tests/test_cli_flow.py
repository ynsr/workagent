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
