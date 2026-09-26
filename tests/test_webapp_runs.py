"""Webapp run-args tests: argv building, session-file injection, arg validation (split from test_webapp.py)."""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from workagent import cli, refs, store, trackers
from workagent.errors import HarnessError
from workagent.webapp import (
    VAL_FLAGS,
    Registry,
    Run,
    _build_argv,
    _target_for,
    _validate_args,
    create_app,
)
from tests.webapp_helpers import (
    FAKE_SCRIPTS,
    _stub_spawn,
    _wait_state,
    client,
)

def test_start_accepts_and_forwards_launch(client, monkeypatch):
    """`start --launch` passes validation and reaches the child argv."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "start", "args": ["IPG-1", "--no-tty", "--launch"],
        "confirm": True,
    })
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    run = client.app.state.registry.get(rid)
    assert "--launch" in run.argv and "--yes" in run.argv  # server appends --yes
    assert "--session-file" in run.argv and run.session_file.endswith(".jsonl")
    _wait_state(client, rid, {"succeeded"})


def test_review_accepts_launch_shorthand_L(client, monkeypatch):
    """-L (CLI shorthand) passes web validation like --launch."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "review", "args": ["o/r#33", "-L", "--dry-run"],
    })
    assert r.status_code == 202, r.text


def test_start_rejects_stale_no_harness_alias(client):
    """Old `--no-harness`/`-N` are rejected with a bad_arg hint (renamed to --launch)."""
    r = client.post("/api/runs", json={
        "command": "start", "args": ["IPG-1", "--no-tty", "--no-harness"],
        "confirm": True,
    })
    assert r.status_code == 400, r.text
    body = r.json()["error"]
    assert body["code"] == "bad_arg" and "--launch" in body["message"]


def test_validate_args_allows_verbose_global():
    _validate_args("sync", ["-v", "IPG-1"])


def test_webapp_review_all_flags_and_target():
    _validate_args("review", ["--all"])
    _validate_args("review", ["--all", "--sequential", "--fix"])
    _validate_args("review", ["--all", "--fix-comments"])
    _validate_args("review", ["o/r#33", "--fix-comments"])
    assert _target_for("review", ["--all"]) == "review:all"
    assert _target_for("review", ["o/r#33"]) == "review:o/r#33"


def test_webapp_open_allowed_and_target():
    _validate_args("open", ["jira:X-1"])
    assert _target_for("open", ["jira:X-1"]) == "open:jira:X-1"


def test_webapp_cleanup_merged_target():
    assert _target_for("cleanup", ["--merged"]) == "cleanup:all"
    assert _target_for("cleanup", ["IPG-1"]) == "cleanup:IPG-1"


def test_api_default_repo_prefers_linked_worktree(client, tmp_path, monkeypatch):
    """Issue #26: /api/default-repo returns the linked-worktree repo, else single-linked."""
    from workagent import repos, trackers, store
    linked = tmp_path / "proj"
    linked.mkdir()
    trackers.check_or_record("jira:IPG", str(linked), persist=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("jira:IPG-1", {"worktree": str(wt), "branch": "b",
                                     "repo": str(linked)})
    r = client.get("/api/default-repo", params={"ref": "IPG-1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ref": "IPG-1", "repo": str(linked)}
    assert client.get("/api/default-repo", params={"ref": "IPG-99"}).json()["repo"] == str(linked)


def test_register_run_key_unique_per_path(client):
    r1 = client.post("/api/runs", json={"command": "register",
                                        "args": ["/tmp/wt-a"]})
    r2 = client.post("/api/runs", json={"command": "register",
                                        "args": ["/tmp/wt-b"]})
    assert r1.status_code == 202, r1.text
    assert r2.status_code == 202, r2.text
    assert r1.json()["run_id"] != r2.json()["run_id"]


def test_start_run_injects_session_file(client, monkeypatch):
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        seen["session_file"] = run.session_file
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    r = client.post("/api/runs", json={"command": "start",
                                       "args": ["IPG-1"],
                                       "confirm": True})
    assert r.status_code == 202, r.text
    assert seen["session_file"].endswith(".jsonl")
    assert "--session-file" in seen["argv"]
    detail = client.get(f"/api/runs/{r.json()['run_id']}").json()
    assert detail["session_file"] == seen["session_file"]


def test_sync_explicit_session_file_recorded(client, monkeypatch):
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    body = {"command": "sync", "args": ["k", "--session-file", "/tmp/s.jsonl"],
            "confirm": True}
    r = client.post("/api/runs", json=body)
    assert r.status_code == 202, r.text
    assert seen["argv"].count("--session-file") == 1
    detail = client.get(f"/api/runs/{r.json()['run_id']}").json()
    assert detail["session_file"] == "/tmp/s.jsonl"


def test_all_with_session_file_rejected(client):
    for command in ("review", "sync"):
        r = client.post("/api/runs", json={
            "command": command,
            "args": ["--all", "--session-file", "/tmp/s.jsonl"],
            "confirm": True})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "bad_arg"


def test_review_force_all_passthrough_accepted():
    """Issue #28: --force-all passes web arg validation for review --all."""
    from workagent import webapp as _w2
    from workagent.webapp import _validate_args
    _validate_args("review", ["--all", "--force-all"])
    for cmd in ("start", "review", "cleanup", "sync", "open", "register", "repo", "link", "tracker"):
        assert cmd in _w2.SPECS, f"SPECS missing {cmd}"


def test_start_review_multi_repo_requires_explicit_repo(client, tmp_path, monkeypatch):
    """Issue #29: ambiguous tracker + no --repo → 400 repo_ambiguous (not a run)."""
    from workagent import store
    from workagent import store_sqlite as _sq
    _stub_spawn(monkeypatch)
    _sq.init_db(_sq.db_path())
    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "b").mkdir(exist_ok=True)
    wt = tmp_path / "wt-pinned"
    wt.mkdir(exist_ok=True)
    store.add_tracker_repo("jira:IPG", str(tmp_path / "a"))
    store.add_tracker_repo("jira:IPG", str(tmp_path / "b"))
    from workagent import trackers as _t
    assert len(_t.linked_repos("jira:IPG")) == 2, _t.linked_repos("jira:IPG")
    store.add_tracker_repo("github:o/r", str(tmp_path / "a"))
    store.add_tracker_repo("github:o/r", str(tmp_path / "b"))
    for command, ref in (("start", "IPG-987"), ("review", "https://github.com/o/r/pull/1")):
        r = client.post("/api/runs", json={"command": command,
                                           "args": [ref, "--dry-run"],
                                           "confirm": True})
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["error"]["code"] == "repo_ambiguous"
        assert "--repo" in body["error"]["message"]
    # Rule-1 pinned: exact key already linked to a worktree repo skips the guard.
    store.record_link("jira:IPG-987", {"branch": "feat/x", "worktree": str(wt),
                                      "repo": str(tmp_path / "a"),
                                      "added_at": "2026-01-01T00:00:00+00:00"})
    r = client.post("/api/runs", json={"command": "start",
                                       "args": ["IPG-987", "--dry-run"],
                                       "confirm": True})
    assert r.status_code == 202, r.text
    r = client.post("/api/runs", json={"command": "start",
                                       "args": ["IPG-987", "--dry-run", "--repo", str(tmp_path / "a")],
                                       "confirm": True})
    assert r.status_code == 202, r.text


def test_repo_remove_worktrees_require_force(client, tmp_path, monkeypatch):
    """repo remove over a repo with linked worktrees 400s unless force."""
    from workagent import store_sqlite as sq
    _stub_spawn(monkeypatch)
    db = sq.db_path()
    sq.register_repo_row(db, "proj", str(tmp_path / "proj"), "jira:IPG")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'proj', '2026-01-01T00:00:00+00:00')")
    r = client.post("/api/runs", json={"command": "repo",
                                       "args": ["remove", "proj"],
                                       "confirm": True})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "worktrees_exist"
    r = client.post("/api/runs", json={"command": "repo",
                                       "args": ["remove", "proj"],
                                       "confirm": True, "force": True})
    assert r.status_code == 202, r.text


def test_fetch_origins_dedupes_per_repo(tmp_path, monkeypatch):
    """_fetch_origins fetches once per distinct repo; only with refresh."""
    links = {
        "jira:A": {"worktree": str(tmp_path / "wt1"), "repo": str(tmp_path / "repo1")},
        "jira:B": {"worktree": str(tmp_path / "wt2"), "repo": str(tmp_path / "repo1")},
        "jira:C": {"worktree": str(tmp_path / "wt3"), "repo": str(tmp_path / "repo2")},
    }
    for e in links.values():
        open(e["worktree"], "w").close()
    calls = []
    monkeypatch.setattr(cli, "run_cmd",
                        lambda *a, **k: calls.append(a) or "")
    cli._fetch_origins(links, True)
    assert len([a for a in calls if "fetch" in a]) == 2  # deduped per repo
    cli._fetch_origins(links, False)
    assert len([a for a in calls if "fetch" in a]) == 2  # no fetch without refresh


def test_status_endpoint_refresh_fetches(client, isolated_config, tmp_path, monkeypatch):
    """/api/status?refresh=true triggers an origin fetch (ref path fetches
    only that repo); plain GET does not."""
    wt = tmp_path / "wt"; wt.mkdir()
    store.record_link("jira:IPG-929", {"issue": "IPG-929", "worktree": str(wt),
                                       "branch": "feat/IPG-929--x",
                                       "repo": str(tmp_path / "proj")})
    git_calls = []
    monkeypatch.setattr(cli, "run_cmd",
                        lambda *a, **k: git_calls.append(a) or "")
    r = client.get("/api/status", params={"refresh": True})
    assert r.status_code == 200, r.text
    assert [a for a in git_calls if "fetch" in a]  # fetched for the ref's repo
    git_calls.clear()
    r = client.get("/api/status")
    assert r.status_code == 200, r.text
    assert not [a for a in git_calls if "fetch" in a]  # no fetch without refresh


def test_resume_run_unknown_is_404_not_500(client):
    """POST /api/runs/<unknown>/resume -> 404 JSON (never bare 500)."""
    r = client.post("/api/runs/does-not-exist-123/resume")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found"
