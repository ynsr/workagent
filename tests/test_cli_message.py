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
