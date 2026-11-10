"""tests/test_cli_message.py — message CLI (Tasks 3-4 TDD)."""
def _seed(tmp_path, monkeypatch, repo="r1"):
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", (repo, f"/tmp/{repo}", repo))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    return db
def test_message_add_list_round_trip(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "add", "--message-id", "p1",
                                      "--repo", "r1", "--content", "hi", "--json"])
    assert res.exit_code == 0, res.output
    res2 = runner.invoke(cli_mod.app, ["message", "list", "--json"])
    assert res2.exit_code == 0, res2.output
    assert "p1" in res2.output


def test_message_show_update_remove_round_trip(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    assert runner.invoke(cli_mod.app, ["message", "add", "--message-id", "p1",
                                       "--repo", "r1", "--content", "hi",
                                       "--json"]).exit_code == 0
    show = runner.invoke(cli_mod.app, ["message", "show", "p1", "--json"])
    assert show.exit_code == 0, show.output
    assert "p1" in show.output
    upd = runner.invoke(cli_mod.app, ["message", "update", "p1", "--content", "hey", "--json"])
    assert upd.exit_code == 0, upd.output
    assert "hey" in upd.output
    rm = runner.invoke(cli_mod.app, ["message", "remove", "p1", "--yes", "--json"])
    assert rm.exit_code == 0, rm.output
    assert runner.invoke(cli_mod.app, ["message", "show", "p1", "--json"]).exit_code == 2

def test_message_action_rejects_worktree_category(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    import workagent.store_custom_actions as sca
    db = _seed(tmp_path, monkeypatch)
    msg = sca.create_action(db, name="WT only", prompt="p", category="worktree")
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "action", "run", msg["label"], "p1", "--yes", "--json"])
    assert res.exit_code == 2
    assert "worktree" in (res.output + res.stderr).lower()

def test_message_draft_reply_dry_run(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    db = _seed(tmp_path, monkeypatch)
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", thread_id="t1", repos=["r1"])
    calls = []
    monkeypatch.setattr("workagent.cli_message._mm", lambda *a: calls.append(a) or {"channel_id": "c1"})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "draft-reply", "p1", "--text", "ack", "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    assert "c1" in res.output


def test_message_adhoc_validate_guards(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    db = _seed(tmp_path, monkeypatch)
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    runner = CliRunner()
    assert runner.invoke(cli_mod.app, ["message", "adhoc", "nope", "--prompt", "hi",
                                       "--dry-run", "--json"]).exit_code == 2
    assert runner.invoke(cli_mod.app, ["message", "adhoc", "p1",
                                       "--dry-run", "--json"]).exit_code == 2
    assert runner.invoke(cli_mod.app, ["message", "adhoc", "p1", "--prompt", "hi",
                                       "--launch", "--terminal", "--dry-run",
                                       "--json"]).exit_code == 2
    ok = runner.invoke(cli_mod.app, ["message", "adhoc", "p1", "--prompt", "hi",
                                     "--dry-run", "--json"])
    assert ok.exit_code == 0, ok.output
    assert runner.invoke(cli_mod.app, ["message", "validate", "nope",
                                       "--dry-run", "--json"]).exit_code == 2

def test_message_flow_rejects_worktree_steps_without_ref(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    import json as _json
    db = _seed(tmp_path, monkeypatch)
    import workagent.store_flows as sfl
    sfl.create_template(db, name="Syncer", category="message",
                        steps=_json.dumps([{"kind": "sync"}]))
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "flow", "run", "syncer", "p1", "--yes", "--json"])
    assert res.exit_code == 2
    assert "--ref" in (res.output + res.stderr)


def test_message_action_run_repo_path_fail_fast(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    import workagent.store_custom_actions as sca
    db = _seed(tmp_path, monkeypatch)
    act = sca.create_action(db, name="Replier", prompt="p", category="message")
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "action", "run", act["label"], "p1", "--yes", "--json"])
    assert res.exit_code == 2
    assert "--ref" in (res.output + res.stderr)

def test_message_draft_reply_surfaces_send_disabled(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent.errors import HarnessError
    db = _seed(tmp_path, monkeypatch)
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    monkeypatch.setattr("workagent.cli_message._mm",
                        lambda *a: (_ for _ in ()).throw(HarnessError("send_disabled: drafts-only mode", exit_code=1)) if a[0] == "messages" and a[1] == "draft" else {"channel_id": "c1"})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "draft-reply", "p1", "--text", "x", "--json"])
    assert res.exit_code != 0
    assert "send_disabled" in (res.output + res.stderr)
