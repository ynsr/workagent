"""CLI adhoc tests (TDD)."""
import json


def _invoke(args):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    from workagent.cli_core import app
    return CliRunner().invoke(app, args)


def test_adhoc_requires_refs():
    res = _invoke(["adhoc", "--prompt", "x", "--yes"])
    assert res.exit_code == 2


def test_adhoc_requires_nonblank_prompt():
    res = _invoke(["adhoc", "k", "--prompt", "   ", "--yes"])
    assert res.exit_code == 2


def test_adhoc_rejects_launch_plus_terminal():
    res = _invoke(["adhoc", "k", "--prompt", "x", "--launch", "--terminal", "--yes"])
    assert res.exit_code == 2


def test_adhoc_skips_unknown_ref_and_still_runs_valid(tmp_path, monkeypatch):
    import tempfile
    wt = tempfile.mkdtemp()
    entry = {"worktree": wt, "branch": "b", "repo": wt, "active": 1}
    monkeypatch.setattr("workagent.store.load_links", lambda *a, **k: {"k": entry})
    monkeypatch.setattr("workagent.store.load_config", lambda *a, **k: {})
    monkeypatch.setattr("workagent.worktrees.is_valid_worktree", lambda p: True)
    monkeypatch.setattr("workagent.cli_adhoc._guard_harness", lambda *a: None)
    calls = []
    monkeypatch.setattr("workagent.cli_adhoc._run_harness",
                        lambda *a, **k: calls.append((a, k)))
    res = _invoke(["adhoc", "nope", "k", "--prompt", "do x", "--yes", "--json"])
    assert res.exit_code == 1, res.output
    assert len(calls) == 1
    assert calls[0][0][1] == "do x"
    payload = json.loads(res.stdout)
    rows = payload["results"]
    assert rows[0]["ref"] == "nope"
    assert "skipped" in rows[0]
    assert rows[1]["key"] == "k"
