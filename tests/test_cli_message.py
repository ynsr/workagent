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

def test_message_flow_suggests_ref_when_unresolvable(tmp_path, monkeypatch):
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
    assert "no linked worktree" in (res.output + res.stderr)


def test_message_flow_resolves_linked_worktree_without_ref(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    import json as _json
    db = _seed(tmp_path, monkeypatch)
    import workagent.store_flows as sfl
    sfl.create_template(db, name="Resolv", category="message",
                        steps=_json.dumps([{"kind": "sync"}]))
    from workagent import store_messages as sm
    sm.create_message(db, message_id="p1", repos=["r1"])
    import workagent.cli_message as cm
    monkeypatch.setattr(cm, "_resolve_exec", lambda row, ref, repo: ("/tmp/wt", "k1"))
    monkeypatch.setattr(cm.store, "load_links",
                        lambda: {"k1": {"worktree": "/tmp/wt"}})
    import workagent.worktrees as _wt
    monkeypatch.setattr(_wt, "is_valid_worktree", lambda p: True)
    seen = {}
    monkeypatch.setattr("workagent.cli_flow_run.flow_run",
                        lambda label, ref, **kw: seen.update(label=label, ref=ref))
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "flow", "run", "resolv", "p1", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert seen == {"label": "resolv", "ref": "k1"}


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


def test_message_adhoc_accepts_permalink(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_messages as sm
    _seed(tmp_path, monkeypatch)
    mid = "abcdefghij1234567890klmnop"
    sm.create_message(sm_db_path(tmp_path), message_id=mid, repos=["r1"])
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "adhoc",
                                      f"https://chat.jibit.cloud/myteam/pl/{mid}",
                                      "--prompt", "hi", "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    assert mid in res.output


def test_message_subcommands_accept_permalink_variants(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_messages as sm
    import workagent.store_custom_actions as sca
    import workagent.store_flows as sfl
    import json as _json
    _seed(tmp_path, monkeypatch)
    mid = "abcdefghij1234567890klmnop"
    db = sm_db_path(tmp_path)
    sm.create_message(db, message_id=mid, repos=["r1"])
    act = sca.create_action(db, name="Replier", prompt="p", category="message")
    sfl.create_template(db, name="MSyncer", category="message",
                        steps=_json.dumps([{"kind": "sync"}]))
    runner = CliRunner()
    base = f"https://chat.jibit.cloud/myteam/pl/{mid}"
    ok = runner.invoke(cli_mod.app, ["message", "validate", base + "/?x=1#frag",
                                     "--dry-run", "--json"])
    assert ok.exit_code == 0, ok.output
    assert mid in ok.output
    # action/flow runs resolve the permalink, then fail on the missing --ref guard
    for args in (["message", "action", "run", act["label"], base, "--yes", "--json"],
                 ["message", "flow", "run", "msyncer", base, "--yes", "--json"]):
        res = runner.invoke(cli_mod.app, args)
        assert res.exit_code == 2
        assert "--ref" in (res.output + res.stderr)
        assert "unknown message" not in (res.output + res.stderr)
    unknown = runner.invoke(cli_mod.app, ["message", "validate",
                                          "https://chat.jibit.cloud/myteam/pl/zzz-nope-unknown",
                                          "--dry-run", "--json"])
    assert unknown.exit_code == 2

def test_message_validate_default_prompt_mentions_jira(tmp_path, monkeypatch):
    from workagent import store_messages as sm
    _seed(tmp_path, monkeypatch)
    mid = "abcdefghij1234567890klmnop"
    sm.create_message(sm_db_path(tmp_path), message_id=mid, repos=["r1"])
    seen = {}
    def _fake(msg, prompt, ref_opt, repo_opt, launch, terminal, harness,
              extra_prompt, env_file, session_file, dry_run, yes, json_output):
        seen["prompt"] = prompt
    monkeypatch.setattr("workagent.cli_message._run_prompt", _fake)
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["message", "validate", mid, "--json"])
    assert res.exit_code == 0, res.output
    assert "Validate the claimed issues" in seen["prompt"]
    assert "CONFIRMED" in seen["prompt"] and "REFUTED" in seen["prompt"]


def sm_db_path(tmp_path):
    return tmp_path / "s.db"


def test_message_add_update_related_worktree_round_trip(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import store_sqlite as sq
    db = _seed(tmp_path, monkeypatch, repo="r1")
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                 ("r2", "/tmp/r2", "r2"))
    conn.execute(
        "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at) VALUES (?, ?, ?, ?, datetime('now'))",
        ("wt1", "/tmp/wt1", "main", "r1"))
    conn.commit()
    runner = CliRunner()
    add = runner.invoke(cli_mod.app, ["message", "add", "--message-id", "p1",
                                      "--repo", "r1", "--related-repo", "r2",
                                      "--worktree", "wt1", "--json"])
    assert add.exit_code == 0, add.output
    assert "p1" in add.output
    show = runner.invoke(cli_mod.app, ["message", "show", "p1", "--json"])
    assert show.exit_code == 0, show.output
    assert "r2" in show.output and "wt1" in show.output
    upd = runner.invoke(cli_mod.app, ["message", "update", "p1",
                                      "--related-repo", "r2",
                                      "--worktree", "wt1", "--json"])
    assert upd.exit_code == 0, upd.output
    bad = runner.invoke(cli_mod.app, ["message", "add", "--message-id", "p2",
                                      "--repo", "r1", "--related-repo", "nope",
                                      "--json"])
    assert bad.exit_code == 2
    bad_wt = runner.invoke(cli_mod.app, ["message", "add", "--message-id", "p3",
                                         "--repo", "r1", "--worktree", "nope",
                                         "--json"])
    assert bad_wt.exit_code == 2
