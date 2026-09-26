"""Webapp data tests: issues/candidates/sessions/mirror/trackers/resume (split from test_webapp.py)."""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from workagent import cli, refs, store, trackers
from workagent.errors import HarnessError
from workagent.webapp import (
    Registry,
    Run,
    create_app,
)
from tests.webapp_helpers import (
    FAKE_SCRIPTS,
    _issue_row,
    _register_repo,
    _stub_spawn,
    _wait_state,
    client,
)

def test_api_issues_missing_cli(client, monkeypatch):
    """200 + warning field even when the tracker CLIs are missing."""

    def boom(*a, **k):
        raise HarnessError("command not found")

    monkeypatch.setattr(trackers, "run_cmd", boom)
    r = client.get("/api/issues")
    assert r.status_code == 200
    body = r.json()
    assert body["issues"] == []
    assert body["warning"]


def test_api_issues_force_bypasses_cache(client, monkeypatch):
    """?force=true reaches list_my_issues with force=True (live re-fetch)."""
    seen = {}

    def fake(warnings, force=False):
        seen["force"] = force
        return []

    monkeypatch.setattr(trackers, "list_my_issues", fake)
    assert client.get("/api/issues").json()["issues"] == []
    assert seen["force"] is False
    assert client.get("/api/issues?force=true").json()["issues"] == []
    assert seen["force"] is True


def test_api_issues_jira_ok(client, monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"url": "https://jira.example.com"}))
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (cfg,))

    def fake(*a, **k):
        if a[0] == "gh":
            raise HarnessError("command not found: gh")
        return json.dumps({"issues": [
            {"key": "IPG-981", "fields": {"summary": "T",
                                          "status": {"name": "In Progress"},
                                          "created": "2026-09-20T10:00:00.000+0000"}}]})

    monkeypatch.setattr(trackers, "run_cmd", fake)
    body = client.get("/api/issues").json()
    assert body["issues"][0]["key"] == "jira:IPG-981"
    assert body["issues"][0]["url"] == "https://jira.example.com/browse/IPG-981"
    assert "gh" in body["warning"]


def test_candidates_exclude_linked(client, monkeypatch, tmp_path):
    _register_repo(tmp_path)
    store.record_link("github:o/r#1",
                      {"pr_url": "https://github.com/o/r/pull/1"})
    monkeypatch.setattr(cli, "_repo_tool", lambda path: "gh")
    monkeypatch.setattr(cli.refs, "fetch_open_prs", lambda tool, cwd: [
        {"number": 1, "title": "linked", "branch": "feat/1",
         "updated": "2026-09-20T10:00:00Z",
         "url": "https://github.com/o/r/pull/1", "state": "OPEN"},
        {"number": 2, "title": "open", "branch": "feat/2",
         "updated": "2026-09-21T10:00:00Z",
         "url": "https://github.com/o/r/pull/2", "state": "OPEN"},
    ])
    monkeypatch.setattr(trackers, "list_my_issues", lambda *a, **k: [])
    body = client.get("/api/candidates").json()
    assert [p["url"] for p in body["prs"]] == ["https://github.com/o/r/pull/2"]
    assert body["prs"][0]["key"] == "github:o/r#2"
    assert body["prs"][0]["repo"] == "o/r"


def test_candidates_issue_window(client, monkeypatch, tmp_path):
    _register_repo(tmp_path)
    monkeypatch.setattr(cli, "_repo_tool", lambda path: None)
    monkeypatch.setattr(cli.refs, "fetch_open_prs", lambda tool, cwd: [])
    monkeypatch.setattr(trackers, "list_my_issues", lambda *a, **k: [
        _issue_row(10, "jira:IPG-OLD"),
        _issue_row(2, "jira:IPG-NEW"),
    ])
    body = client.get("/api/candidates").json()
    assert [i["key"] for i in body["issues"]] == ["jira:IPG-NEW"]


def test_candidates_shape_and_warnings(client, monkeypatch, tmp_path):
    _register_repo(tmp_path)
    monkeypatch.setattr(cli, "_repo_tool", lambda path: "gh")

    def boom(tool, cwd):
        raise HarnessError("gh pr list failed: auth")

    monkeypatch.setattr(cli.refs, "fetch_open_prs", boom)
    monkeypatch.setattr(trackers, "list_my_issues", lambda *a, **k: [])
    body = client.get("/api/candidates").json()
    assert set(body) == {"prs", "issues", "worktrees", "warnings"}
    assert body["prs"] == [] and body["issues"] == []
    assert body["worktrees"] == []
    assert any("proj" in w for w in body["warnings"])


def test_api_sessions_empty(client):
    body = client.get("/api/sessions").json()
    assert body == {"sessions": []}


def test_api_sessions_roundtrip(client, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
                            initiator_command="start", prompt="hello",
                            file_path="/tmp/x.jsonl")
    body = client.get("/api/sessions").json()
    assert body["sessions"][0]["id"] == sid
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["prompt"] == "hello" and detail["runs"] == []
    assert client.get("/api/sessions/nope").status_code == 404


def test_mirror_run_persists_session_run(client):
    from workagent import store_sqlite as sq
    from workagent.webapp import _mirror_run
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
                            initiator_command="start", prompt="hello",
                            file_path="/tmp/x.jsonl",
                            session_id="2026-09-26T00-00-00-000Z-1234")
    run = Run(id="abc123", command="start", args=["IPG-1"], target="start",
              argv=["/py", "-m", "workagent", "start", "IPG-1", "--yes",
                    "--session-file", "/s/sessions/omp/2026-09-26T00-00-00-000Z-1234.jsonl"],
              session_file="/s/sessions/omp/2026-09-26T00-00-00-000Z-1234.jsonl")
    run.exit_code = 0
    _mirror_run(run)
    detail = sq.get_session(db, sid)
    assert len(detail["runs"]) == 1
    row = detail["runs"][0]
    assert row["command"] == "start" and row["exit_code"] == 0
    assert "--session-file" in row["args"]


def test_mirror_run_skips_without_session(client):
    from workagent.webapp import _mirror_run
    run = Run(id="abc123", command="cleanup", args=["IPG-1", "--dry-run"],
              target="cleanup:IPG-1")
    run.exit_code = 0
    _mirror_run(run)  # no session_file → no-op, never raises


def test_api_repos_includes_tracker(client, monkeypatch):
    import workagent.store as store
    cfg = store.load_config()
    cfg["repos"] = {"p": {"path": "/tmp/proj"}}
    cfg["trackers"] = {"jira:IPG": {"repos": ["/tmp/proj"]}}
    store.save_config(cfg)
    r = client.get("/api/repos")
    assert r.status_code == 200
    assert r.json()[0]["tracker"] == "jira:IPG"


def test_run_tracker_add_requires_remote_url(client, tmp_path, monkeypatch):
    """tracker add via /api/runs is rejected without --remote-url (400)."""
    r = client.post("/api/runs", json={"command": "tracker", "args": ["add", "jira:IPG"]})
    assert r.status_code == 400, r.text
    assert "--remote-url" in r.json()["error"]["message"]


def test_api_trackers_lists_rows(client, tmp_path, monkeypatch):
    """GET /api/trackers returns key/vendor/remote_url + repo counts."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sq.upsert_tracker(db, "jira:IPG", vendor="jira", remote_url="https://jira/browse/IPG")
    sq.add_tracker_repo(db, "jira:IPG", "/r")
    r = client.get("/api/trackers")
    assert r.status_code == 200, r.text
    assert r.json() == {"trackers": [{"key": "jira:IPG", "vendor": "jira",
                                       "remote_url": "https://jira/browse/IPG", "repos": 1}]}


def test_api_repos_includes_trackers_array(client, tmp_path, monkeypatch):
    """GET /api/repos carries the joined trackers array + first-tracker compat."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sq.ensure_tracker(db, "jira:IPG")
    sq.ensure_tracker(db, "github:o/r")
    sq.register_repo_row(db, "p", "/tmp/proj", "jira:IPG")
    sq.add_tracker_repo(db, "github:o/r", "/tmp/proj")
    r = client.get("/api/repos")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["trackers"] == ["github:o/r", "jira:IPG"]
    assert row["tracker"] == "github:o/r"


def test_resume_run_needs_session(client):
    run = client.app.state.registry.create("register", ["/tmp/x"],
                                           "register:/tmp/x")
    r = client.post(f"/api/runs/{run.id}/resume")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "no_session"


def test_resume_run_opens_terminal(client, monkeypatch, tmp_path):
    session = tmp_path / "s.jsonl"
    session.write_text("{}\n")
    opened: dict = {}
    monkeypatch.setattr("workagent.webapp._open_terminal",
                        lambda wt, sf: opened.update(wt=wt, sf=sf))
    run = client.app.state.registry.create("review", ["o/r#1"],
                                           "review:o/r#1")
    run.session_file = str(session)
    run.worktree = "/tmp/wt"
    r = client.post(f"/api/runs/{run.id}/resume")
    assert r.status_code == 200, r.text
    assert opened == {"wt": "/tmp/wt", "sf": str(session)}


def test_resume_session_opens_terminal(client, monkeypatch, tmp_path):
    from workagent import store_sqlite as sq
    session = tmp_path / "s.jsonl"
    session.write_text("{}\n")
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
                            initiator_command="start", prompt="hello",
                            file_path=str(session))
    opened: dict = {}
    monkeypatch.setattr("workagent.webapp._open_terminal",
                        lambda wt, sf: opened.update(wt=wt, sf=sf))
    r = client.post(f"/api/sessions/{sid}/resume")
    assert r.status_code == 200, r.text
    assert opened == {"wt": "/wt", "sf": str(session)}


def test_specs_mirror_cli_flags():
    """Parity: webapp BOOL_FLAGS/VAL_FLAGS mirror the real Typer CLI (#25 checklist).

    Catches drift like `--post-comments`, `sync --force/-y`, `register -y`.
    Read-only/local commands (status/cd/doctor/migrate/candidates/serve/
    completions) are intentionally not web-exposed.
    """
    import typer.main as _tm

    from workagent import webapp as _w

    def _walk(cmd, path):
        out = {}
        bools, vals = set(), set()
        for p in getattr(cmd, "params", []) or []:
            names = list(getattr(p, "opts", []) or []) + list(getattr(p, "secondary_opts", []) or [])
            if not names:
                continue
            (bools if getattr(p, "is_flag", False) else vals).update(names)
        out[" ".join(path)] = (bools, vals)
        for name, sub in (getattr(cmd, "commands", {}) or {}).items():
            out.update(_walk(sub, path + [name]))
        return out

    inv = _walk(_tm.get_command(cli.app), [])
    skip = {"", "completions", "completions install", "completions show",
            "serve", "doctor", "migrate", "candidates", "status", "cd",
            "repo", "link", "tracker"}  # bare groups never invoked; subcommands covered
    for path, (b, v) in sorted(inv.items()):
        if path in skip:
            continue
        assert path in _w.BOOL_FLAGS or path in _w.VAL_FLAGS, f"{path} missing from webapp inventory"
        cb = {x for x in b if x.startswith("-")}
        cv = {x for x in v if x.startswith("-")}
        assert cb == set(_w.BOOL_FLAGS.get(path, ())), f"BOOL drift [{path}]"
        assert cv == set(_w.VAL_FLAGS.get(path, ())), f"VAL drift [{path}]"
