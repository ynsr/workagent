"""errors.run_cmd: JIRA_API_TOKEN injection for `jira` invocations."""

import os
import subprocess

from workagent import errors as E


def _reset_cache():
    E._jira_token_cache = (None, "")


def _capture_run(monkeypatch):
    seen = {}

    def _fake(argv, **kw):
        seen.update(kw)
        seen["argv"] = argv

        class _P:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _P()

    monkeypatch.setattr(subprocess, "run", _fake)
    return seen


def test_jira_gets_token_from_secrets_file(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    secrets = tmp_path / ".secrets"
    secrets.mkdir()
    (secrets / ".env").write_text("export JIRA_API_TOKEN=tok-from-file\n")
    _reset_cache()
    seen = _capture_run(monkeypatch)
    assert E.run_cmd("jira", "issue", "view", "IPG-1", "--raw", echo=False) == "ok"
    assert seen["env"]["JIRA_API_TOKEN"] == "tok-from-file"


def test_exported_token_wins_and_non_jira_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-exported")
    monkeypatch.setenv("HOME", str(tmp_path))
    _reset_cache()
    assert E._jira_api_token() == "tok-exported"
    seen = _capture_run(monkeypatch)
    assert E.run_cmd("jira", "issue", "view", "IPG-1", "--raw", echo=False) == "ok"
    assert seen.get("env") is None  # inherits os.environ, no overlay needed
    E.run_cmd("gh", "issue", "view", "1", echo=False)
    assert seen.get("env") is None


def test_missing_secrets_file_means_no_overlay(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no .secrets/.env here
    _reset_cache()
    assert E._jira_api_token() == ""
    seen = _capture_run(monkeypatch)
    E.run_cmd("jira", "issue", "view", "IPG-1", "--raw", echo=False)
    assert seen.get("env") is None


def test_token_never_echoed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    secrets = tmp_path / ".secrets"
    secrets.mkdir()
    (secrets / ".env").write_text("JIRA_API_TOKEN=s3cr3t-value\n")
    _reset_cache()
    _capture_run(monkeypatch)
    E.run_cmd("jira", "issue", "move", "IPG-1", "Done", echo=True)
    err = capsys.readouterr().err
    assert "s3cr3t-value" not in err
    assert "$ jira issue move IPG-1 Done" in err
    assert "JIRA_API_TOKEN" not in os.environ  # file value not leaked into process env
