"""tests/test_cli_message.py — message CLI (Tasks 3-4 TDD)."""
def test_message_add_list_round_trip(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "add", "--message-id", "p1",
                                      "--repo", "r1", "--content", "hi", "--json"])
    assert res.exit_code == 0, res.output
    res2 = runner.invoke(cli_mod.app, ["message", "list", "--json"])
    assert res2.exit_code == 0, res2.output
    assert "p1" in res2.output


def test_message_action_rejects_worktree_category(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    import workagent.store_custom_actions as sca
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
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
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", thread_id="t1", repos=["r1"])
    calls = []
    monkeypatch.setattr("workagent.cli_message._mm", lambda *a: calls.append(a) or {"channel_id": "c1"})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "draft-reply", "p1", "--text", "ack", "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    assert "c1" in res.output


def test_message_flow_rejects_worktree_steps_without_ref(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    import json as _json
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    import workagent.store_flows as sfl
    sfl.create_template(db, name="Syncer", category="message",
                        steps=_json.dumps([{"kind": "sync"}]))
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "flow", "run", "syncer", "p1", "--yes", "--json"])
    assert res.exit_code == 2
    assert "sync" in (res.output + res.stderr).lower()
    assert "--ref" in (res.output + res.stderr)


def test_message_draft_reply_surfaces_send_disabled(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    import json as _json
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)", ("r1", "/tmp/r1", "r1"))
    conn.commit()
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    monkeypatch.setattr("workagent.cli_message._mm",
                        lambda *a: (_ for _ in ()).throw(HarnessError("send_disabled: drafts-only mode", exit_code=1)) if a[0] == "messages" and a[1] == "draft" else {"channel_id": "c1"})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "draft-reply", "p1", "--text", "x", "--json"])
    assert res.exit_code != 0
    assert "send_disabled" in (res.output + res.stderr)
