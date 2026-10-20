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


def _skip_action_fixtures(monkeypatch, tmp_path, **action_kwargs):
    """Mirror test_pre_fail_skips_harness fixtures; stub harness + guard + config."""
    import tempfile
    from workagent import store_custom_actions as sca
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    kwargs = {"name": "Skip Gate", "prompt": "p"}
    kwargs.update(action_kwargs)
    action = sca.create_action(db, **kwargs)
    wt = tempfile.mkdtemp()
    entry = {"worktree": wt, "branch": "b", "repo": wt, "active": 1}
    monkeypatch.setattr("workagent.store.get_action", lambda label: action)
    monkeypatch.setattr("workagent.store.load_links",
                        lambda *a, **k: {"k": entry})
    monkeypatch.setattr("workagent.worktrees.resolve_any",
                        lambda ref, l: ("k", entry, None))
    monkeypatch.setattr("workagent.worktrees.is_valid_worktree",
                        lambda p: True)
    monkeypatch.setattr("workagent.store.load_config",
                        lambda: {"default_harness": "omp"})
    monkeypatch.setattr("workagent.cli_action._guard_harness",
                        lambda *a, **k: None)
    called = []
    def _fake_harness(*a, **k):
        called.append(1)
        return {"label": action["label"], "key": "k", "status": "acted"}
    monkeypatch.setattr("workagent.cli_action._run_harness", _fake_harness)
    return action, entry, called


def test_pre_skip_prints_skipped_no_harness(tmp_path, monkeypatch):
    import json as _json
    import os
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent.release_gates import SkippedGate
    action, entry, called = _skip_action_fixtures(
        monkeypatch, tmp_path, pre_execution="exit 10")
    seen = {}
    def _pre(repo, key, command, cwd, timeout, env_file=None, extra_env=None):
        seen["extra_env"] = extra_env
        raise SkippedGate("would push 3 commits")
    monkeypatch.setattr("workagent.release_gates.run_repo_command", _pre)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "run", action["label"], "k",
                         "--launch", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert called == []
    assert "pre-execution failed" not in res.stderr
    body = _json.loads(res.stdout)
    assert body == {"label": action["label"], "key": "k",
                    "skipped": "would push 3 commits"}
    assert "SKIP:" not in body["skipped"]
    assert seen.get("extra_env") is None  # headless: no preview flag
    assert "WORKAGENT_PREVIEW" not in os.environ


def test_post_skip_returns_skip_result(tmp_path, monkeypatch):
    import json as _json
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent.release_gates import SkippedGate
    action, entry, called = _skip_action_fixtures(
        monkeypatch, tmp_path, post_execution="exit 10")
    def _post(repo, key, command, cwd, timeout, env_file=None, extra_env=None):
        if key == "post-execution":
            raise SkippedGate("mr-exists")
    monkeypatch.setattr("workagent.release_gates.run_repo_command", _post)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "run", action["label"], "k",
                         "--launch", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert called == [1]
    body = _json.loads(res.stdout)
    assert body["skipped"] == "mr-exists"
    assert "SKIP:" not in body["skipped"]


def test_pre_exit_1_still_fails_as_today(tmp_path, monkeypatch):
    import json as _json
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent.errors import HarnessError
    action, entry, called = _skip_action_fixtures(
        monkeypatch, tmp_path, pre_execution="exit 1")
    def _boom(*a, **k):
        raise HarnessError("sh failed with exit 1")
    monkeypatch.setattr("workagent.release_gates.run_repo_command", _boom)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "run", action["label"], "k",
                         "--launch", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert called == []
    assert "pre-execution failed" in res.stderr
    assert _json.loads(res.stdout)["skipped"] == "pre-execution failed"


def test_preview_passes_preview_env_to_pre(tmp_path, monkeypatch):
    import os
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    action, entry, called = _skip_action_fixtures(
        monkeypatch, tmp_path, pre_execution="echo hi")
    seen = {}
    def _pre(repo, key, command, cwd, timeout, env_file=None, extra_env=None):
        seen["extra_env"] = extra_env
    monkeypatch.setattr("workagent.release_gates.run_repo_command", _pre)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "run", action["label"], "k",
                         "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert called == [1]
    assert seen.get("extra_env") == {"WORKAGENT_PREVIEW": "1"}
    assert "WORKAGENT_PREVIEW" not in os.environ


def test_update_changes_fields(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent.cli_core import app
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_action as cli_mod
    import json as _json
    row = {"id": 1, "label": "generate_post_prd", "name": "New name"}
    monkeypatch.setattr("workagent.store.update_action",
                        lambda label, **f: {**row, "label": label})
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "update", "generate_post_prd",
                         "--name", "New name", "--json"])
    assert res.exit_code == 0, res.output
    assert _json.loads(res.stdout)["name"] == "New name"


def test_update_no_fields_exits_2(tmp_path):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_action as cli_mod
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "update", "generate_post_prd"])
    assert res.exit_code == 2

def test_extract_append_prompt_json_last_line():
    from workagent.cli_action import extract_append_prompt
    out = 'pushing stuff\n{"append_prompt": "Resolved base branch: main."}\n'
    assert extract_append_prompt(out) == "Resolved base branch: main."


def test_extract_append_prompt_garbage_ignored():
    from workagent.cli_action import extract_append_prompt
    assert extract_append_prompt("just some logs\nmore logs\n") == ""
    assert extract_append_prompt("") == ""


def test_extract_append_prompt_non_dict_ignored():
    from workagent.cli_action import extract_append_prompt
    assert extract_append_prompt('[1, 2, 3]\n"hello"\n42\n') == ""


def test_extract_append_prompt_non_string_ignored():
    from workagent.cli_action import extract_append_prompt
    assert extract_append_prompt('{"append_prompt": 42}\n') == ""
    assert extract_append_prompt('{"append_prompt": null}\n') == ""
    assert extract_append_prompt('{"other": "x"}\n') == ""


def test_extract_append_prompt_later_line_wins():
    from workagent.cli_action import extract_append_prompt
    out = ('{"append_prompt": "first"}\n'
           'noise\n'
           '{"append_prompt": "second"}\n')
    assert extract_append_prompt(out) == "second"


def test_action_run_appends_pre_before_extra(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import cli_action as ca
    action, entry, _called = _skip_action_fixtures(
        monkeypatch, tmp_path, pre_execution="echo hi")
    monkeypatch.setattr(
        "workagent.release_gates.run_repo_command",
        lambda *a, **k: 'log line\n{"append_prompt": "Resolved base branch: main."}\n')
    seen = {}
    def _fake_harness(harness_name, prompt, wt, repo, no_tty, use_launch,
                      result, json_output, **kw):
        seen["prompt"] = prompt
        return {"label": action["label"], "key": "k", "status": "acted"}
    monkeypatch.setattr("workagent.cli_action._run_harness", _fake_harness)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "run", action["label"], "k",
                         "--launch", "--yes", "--json",
                         "--extra-prompt", "EXTRA-BITS"])
    assert res.exit_code == 0, res.output
    prompt = seen["prompt"]
    assert "Resolved base branch: main." in prompt
    assert "EXTRA-BITS" in prompt
    assert prompt.index("Resolved base branch: main.") < prompt.index("EXTRA-BITS")
    assert prompt.index("Related worktrees:") < prompt.index("Resolved base branch: main.")


def test_action_run_non_json_pre_changes_nothing(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    from workagent import cli_action as ca
    action, entry, _called = _skip_action_fixtures(
        monkeypatch, tmp_path, pre_execution="echo hi")
    monkeypatch.setattr(
        "workagent.release_gates.run_repo_command",
        lambda *a, **k: "just logs, no json\n")
    seen = {}
    def _fake_harness(harness_name, prompt, wt, repo, no_tty, use_launch,
                      result, json_output, **kw):
        seen["prompt"] = prompt
        return {"label": action["label"], "key": "k", "status": "acted"}
    monkeypatch.setattr("workagent.cli_action._run_harness", _fake_harness)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "run", action["label"], "k",
                         "--launch", "--yes", "--json",
                         "--extra-prompt", "EXTRA-BITS"])
    assert res.exit_code == 0, res.output
    base = ca.build_action_prompt(action, entry, "k",
                                  {"k": entry})
    from workagent.cli_harness import append_extra_prompt as _aep
    assert seen["prompt"] == _aep(base, "EXTRA-BITS")


def test_update_new_label_passed_through(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_action as cli_mod
    seen = {}

    def _update(label, **fields):
        seen.update({"label": label, **fields})
        return {"id": 1, "label": fields.get("new_label", label),
                "name": "Foo"}

    monkeypatch.setattr("workagent.store.update_action", _update)
    runner = CliRunner()
    res = runner.invoke(cli_mod.app,
                        ["action", "update", "foo",
                         "--new-label", "bar", "--json"])
    assert res.exit_code == 0, res.output
    assert seen == {"label": "foo", "new_label": "bar"}
    import json as _json
    assert _json.loads(res.stdout)["updated"] == "bar"

def test_open_mr_preset_gate_detects_existing_mr(tmp_path, monkeypatch):
    """Open MR preset pre-exec must SKIP when an open MR exists.

    Regression: the gate used `glab mr list ... --state opened`, but glab
    has no --state flag — the error was hidden by 2>/dev/null so the gate
    always passed and the harness created duplicate MRs.
    """
    import re
    import tempfile
    from typer.testing import CliRunner
    from workagent import cli as cli_mod
    src = open("web/src/lib/actionPresets.ts").read()
    pre = re.search(r"const OPEN_MR_PRE_EXECUTION = `(.*?)`\n", src, re.S).group(1)
    assert "--state" not in pre, "glab mr list has no --state flag"
    assert "-F json" in pre
    from workagent import store_custom_actions as sca
    from workagent import store_sqlite as sq
    db = tmp_path / "s.db"
    sq.init_db(db)
    action = sca.create_action(db, name="Open MR Test", prompt="p", pre_execution=pre)
    wt = tempfile.mkdtemp()
    entry = {"worktree": wt, "branch": "b", "repo": wt, "active": 1}
    monkeypatch.setattr("workagent.store.get_action", lambda label: action)
    monkeypatch.setattr("workagent.store.load_links", lambda *a, **k: {"k": entry})
    monkeypatch.setattr("workagent.worktrees.resolve_any", lambda ref, l: ("k", entry, None))
    monkeypatch.setattr("workagent.worktrees.is_valid_worktree", lambda p: True)
    called = []
    monkeypatch.setattr("workagent.cli_action._run_harness", lambda *a, **k: called.append(1))
    monkeypatch.setattr("workagent.cli_action._guard_harness", lambda *a, **k: None)
    # Fake git + glab: feature branch, 1 ahead of develop, open MR exists.
    import os
    import stat
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "git").write_text('#!/bin/sh\nif [ "$2" = "rev-parse" ]; then echo "feat/x"; elif [ "$2" = "symbolic-ref" ]; then echo "refs/remotes/origin/develop"; elif [ "$2" = "rev-list" ]; then echo 2; fi\nexit 0\n')
    (bindir / "glab").write_text('#!/bin/sh\ne=0\nfor a in "$@"; do [ "$a" = "--state" ] && e=1; done\nif [ $e = 1 ]; then echo "Unknown flag: --state" >&2; exit 1; fi\necho \'[{"iid": 1, "state": "opened"}]\'\nexit 0\n')
    for f in ("git", "glab"):
        os.chmod(bindir / f, os.stat(bindir / f).st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["action", "run", action["label"], "k", "--launch", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert called == [], "harness must not run when an open MR exists"
    import json as _json
    assert "already exists" in _json.loads(res.stdout)["skipped"]
