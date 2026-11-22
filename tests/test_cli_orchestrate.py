"""Tests for `workagent orchestrate` (T14.1)."""
from __future__ import annotations

import json
import os

from typer.testing import CliRunner

from workagent import cli, cli_orchestrate, repos, store_sqlite

runner = CliRunner()


def test_build_orchestrator_prompt_includes_tools_and_repos():
    prompt = cli_orchestrate.build_orchestrator_prompt(
        "Split auth and billing fixes across repos",
        [("api", "/tmp/api"), ("web", "/tmp/web")],
    )
    assert "workagent status --json" in prompt
    assert "workagent candidates --json" in prompt
    assert "workagent message show" in prompt
    assert "workagent start" in prompt
    assert "workagent flow run" in prompt
    assert "WORKAGENT_PARENT_RUN_ID" in prompt
    assert "- api: /tmp/api" in prompt
    assert "- web: /tmp/web" in prompt
    assert "Split auth and billing fixes across repos" in prompt


def test_orchestrate_requires_prompt(isolated_config):
    res = runner.invoke(cli.app, ["orchestrate"])
    assert res.exit_code == 2


def test_orchestrate_mutually_exclusive_modes(isolated_config):
    res = runner.invoke(
        cli.app,
        ["orchestrate", "--prompt", "do work", "--acp", "--launch"],
    )
    assert res.exit_code == 2


def test_orchestrate_dry_run_json(isolated_config, tmp_path):
    r1 = tmp_path / "repo-a"
    r1.mkdir()
    repos.register_repo("repo-a", r1, tracker_key="github:o/repo-a")

    res = runner.invoke(
        cli.app,
        ["orchestrate", "--repo", "repo-a", "--prompt", "Split tasks", "--dry-run", "--json"],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["command"] == "orchestrate"
    assert data["mode"] == "acp"
    assert data["repos"] == ["repo-a"]
    assert data["dry_run"] is True
    assert data["prompt_chars"] > 0


def test_orchestrate_launches_acp_and_propagates_parent_run_id(isolated_config, tmp_path, monkeypatch):
    db = store_sqlite.db_path()
    store_sqlite.init_db(db)

    r1 = tmp_path / "repo-a"
    r1.mkdir()
    repos.register_repo("repo-a", r1, tracker_key="github:o/repo-a")

    seen: dict = {}

    class _FakeHarness:
        name = "omp"
        supports_acp = True
        watches_headless = False

        def session_file_flag(self, path):
            return []

        def command_argv(self, prompt, no_tty, extra_args=None, model=None, effort=None):
            return ["omp", "--acp"]

        def launch_acp(self, prompt, workdir, **kwargs):
            seen["prompt"] = prompt
            seen["workdir"] = workdir
            seen["parent_env"] = os.environ.get("WORKAGENT_PARENT_RUN_ID")
            seen["model"] = kwargs.get("model")
            seen["effort"] = kwargs.get("effort")
            return 0

        def extract_last_assistant_text(self, path, max_chars=None):
            return "JOB_DONE"

    from workagent import backend, cli_validate
    monkeypatch.setattr(backend, "get_harness", lambda name: _FakeHarness())
    monkeypatch.setattr(
        cli_validate,
        "validate_headless",
        lambda **kw: (kw["run_once"](kw["prompt"]), {"verdict": "JOB_DONE", "runs": []})[1],
    )

    res = runner.invoke(
        cli.app,
        [
            "orchestrate",
            "--repo",
            "repo-a",
            "--prompt",
            "Build feature A and B",
            "--model",
            "sonnet",
            "--effort",
            "high",
            "--yes",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    assert seen["workdir"] == str(r1)
    assert seen["model"] == "sonnet"
    assert seen["effort"] == "high"
    assert seen["parent_env"] and seen["parent_env"].isdigit()
    # Env var must be cleaned up after launch
    assert "WORKAGENT_PARENT_RUN_ID" not in os.environ


def test_orchestrate_unknown_repo_fails_fast_without_cloning(isolated_config):
    res = runner.invoke(
        cli.app,
        ["orchestrate", "--repo", "typo-repo", "--prompt", "x", "--dry-run", "--json"],
    )
    assert res.exit_code == 2
    assert "unknown --repo 'typo-repo'" in res.output


def test_orchestrate_key_varies_by_repo_set_and_prompt():
    from workagent.web_args import _target_for
    a = _target_for("orchestrate", ["--repo", "a", "--prompt", "x"])
    b = _target_for("orchestrate", ["--repo", "b", "--prompt", "x"])
    c = _target_for("orchestrate", ["--repo", "a", "--prompt", "y"])
    assert a != b and a != c


def test_orchestrate_web_validator_parity(isolated_config):
    from workagent.web_args import _validate_args
    _validate_args("orchestrate", ["--repo", "a", "--prompt", "-fix it"])
    _validate_args("orchestrate", ["--prompt", "x", "--continue-run", "3"])


def test_insert_run_falls_back_to_run_id_env(isolated_config, monkeypatch):
    import os
    db = store_sqlite.db_path()
    store_sqlite.init_db(db)
    parent = store_sqlite.insert_run(db, "orchestrate", ["--prompt", "x"], 0, harness_name="omp", state="finished")
    monkeypatch.delenv("WORKAGENT_PARENT_RUN_ID", raising=False)
    monkeypatch.setenv("WORKAGENT_RUN_ID", str(parent))
    child = store_sqlite.insert_run(db, "start", ["R"], None, harness_name="omp", state="running")
    assert store_sqlite.get_run(db, child)["parent_run_id"] == parent
    assert [r["id"] for r in store_sqlite.runs_by_parent(db, parent)] == [child]
    monkeypatch.setenv("WORKAGENT_RUN_ID", str(child + 999))
    orphan = store_sqlite.insert_run(db, "start", ["R"], None, harness_name="omp", state="running")
    assert store_sqlite.get_run(db, orphan)["parent_run_id"] is None
