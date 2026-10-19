"""CLI action tests (Task 3 TDD)."""


def test_build_action_prompt_appends_child_paths():
    from workagent.cli_action import build_action_prompt
    action = {"prompt": "Do the thing."}
    entry = {"worktree": "/wt/parent", "branch": "feat/x"}
    links = {"p": {"worktree": "/wt/parent", "branch": "feat/x"},
             "c": {"worktree": "/wt/child", "branch": "feat/x",
                   "parent_key_ref": "p"}}
    out = build_action_prompt(action, entry, "p", links)
    assert "Do the thing." in out
    assert "/wt/parent" in out and "/wt/child" in out
    assert out.index("/wt/parent") < out.index("/wt/child")
def test_unknown_label_exits_2(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent.cli_core import app
    import workagent.cli  # noqa: F401  (register commands)
    runner = CliRunner()
    res = runner.invoke(app, ["action", "run", "nope", "some-ref"])
    assert res.exit_code == 2

def test_launch_terminal_xor(tmp_path):
    import typer
    from workagent import cli_action as ca
    try:
        ca._resolve_mode({"default_options": {}}, True, True)
    except typer.Exit as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected typer.Exit")
    assert ca._resolve_mode({"default_options": {"run_mode": "headless"}}, False, False) == "headless"
    assert ca._resolve_mode({"default_options": {}}, False, False) == "preview"

def test_pre_fail_skips_harness(tmp_path, monkeypatch):
    import tempfile
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import cli_action as ca
    from workagent import store_custom_actions as sca
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    action = sca.create_action(db, name="Pre Fail", prompt="p",
                               pre_execution="exit 3")
    wt = tempfile.mkdtemp()
    entry = {"worktree": wt, "branch": "b", "repo": wt, "active": 1}
    monkeypatch.setattr("workagent.store.get_action", lambda label: action)
    monkeypatch.setattr("workagent.store.load_links",
                        lambda *a, **k: {"k": entry})
    monkeypatch.setattr("workagent.worktrees.resolve_any",
                        lambda ref, l: ("k", entry, None))
    monkeypatch.setattr("workagent.worktrees.is_valid_worktree",
                        lambda p: True)
    called = []
    monkeypatch.setattr("workagent.cli_action._run_harness",
                        lambda *a, **k: called.append(1))
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "run", action["label"], "k",
                         "--launch", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert called == []
    assert "pre-execution failed" in res.stderr
