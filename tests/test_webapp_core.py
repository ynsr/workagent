"""Webapp core tests: guard rails, run lifecycle, read endpoints (split from test_webapp.py)."""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from workagent import cli, refs, store, trackers
from workagent.errors import HarnessError
from workagent.webapp import (
    BOOL_FLAGS,
    MAX_LINES,
    MAX_RUNS,
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
    _app,
    _stub_spawn,
    _wait_state,
    client,
)

def test_host_header_allowlist(tmp_path, isolated_config):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/info", headers={"Host": "localhost:3344"}).status_code == 200
        assert c.get("/api/info", headers={"Host": "127.0.0.1:3344"}).status_code == 200
        assert c.get("/api/info", headers={"Host": "10.1.2.3:1"}).status_code == 200  # IP literal
        assert c.get("/api/info", headers={"Host": "devbox.local"}).status_code == 200
        r = c.get("/api/info", headers={"Host": "evil.example.com"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "bad_host"


def test_post_requires_json_content_type(client):
    r = client.post("/api/runs", data="x",
                    headers={"Content-Type": "text/plain", "Host": "localhost"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "bad_content_type"


def test_post_origin_must_match_host(client):
    r = client.post("/api/runs", json={"command": "register", "args": []},
                    headers={"Origin": "http://evil:1", "Host": "localhost"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "bad_origin"
    r = client.post("/api/runs", json={"command": "register", "args": []},
                    headers={"Origin": "http://localhost", "Host": "localhost"})
    assert r.status_code in (202, 400)  # origin ok; body validation decides


# ── validation ────────────────────────────────────────────────────────


def test_unknown_command_rejected(client):
    r = client.post("/api/runs", json={"command": "completions",
                                       "args": ["show", "bash"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_command"


def test_unknown_option_rejected(client):
    r = client.post("/api/runs", json={"command": "sync",
                                       "args": ["--bogus", "1"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_arg"


def test_value_starting_with_dash_rejected(client):
    r = client.post("/api/runs", json={"command": "start",
                                       "args": ["x", "--repo", "-evil"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_arg"


def test_subcommand_allowlist(client):
    r = client.post("/api/runs", json={"command": "repo",
                                       "args": ["frobnicate"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_subcommand"


# ── confirm / force ───────────────────────────────────────────────────


def test_destructive_requires_confirm(client):
    r = client.post("/api/runs", json={"command": "cleanup",
                                       "args": ["IPG-1"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "confirm_required"


def test_force_requires_confirm(client):
    r = client.post("/api/runs", json={"command": "cleanup",
                                       "args": ["IPG-1"], "force": True})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "force_needs_confirm"


def test_dry_run_needs_no_confirm(client, monkeypatch):
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={"command": "cleanup",
                                       "args": ["IPG-1", "--dry-run"]})
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    _wait_state(client, rid, {"succeeded"})


def test_non_destructive_runs_without_confirm(client, monkeypatch):
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={"command": "register",
                                       "args": ["/tmp/nope-wt"]})
    assert r.status_code == 202


# ── run lifecycle (fake child processes) ──────────────────────────────


def test_exit_zero_succeeds(client, monkeypatch):
    _stub_spawn(monkeypatch)
    rid = client.post("/api/runs", json={"command": "cleanup",
                                         "args": ["IPG-1", "--dry-run"]}
                      ).json()["run_id"]
    assert _wait_state(client, rid, {"succeeded"}) == "succeeded"
    detail = client.get(f"/api/runs/{rid}").json()
    assert detail["exit_code"] == 0
    assert detail["lines"][-1]["text"] == "cleanup: would remove"


def test_exit_two_is_needs_input(client, monkeypatch):
    _stub_spawn(monkeypatch)
    rid = client.post("/api/runs", json={"command": "register",
                                         "args": ["/tmp/nope-wt"]}
                      ).json()["run_id"]
    assert _wait_state(client, rid, {"needs_input"}) == "needs_input"


def test_exit_one_fails(client, monkeypatch):
    _stub_spawn(monkeypatch)
    FAKE_SCRIPTS["register"][("/tmp/nope-wt",)] = (["boom"], 1)
    try:
        rid = client.post("/api/runs", json={"command": "register",
                                             "args": ["/tmp/nope-wt"]}
                          ).json()["run_id"]
        assert _wait_state(client, rid, {"failed"}) == "failed"
    finally:
        FAKE_SCRIPTS["register"][("/tmp/nope-wt",)] = (["err"], 2)


def test_real_child_process_end_to_end(client):
    """Real spawn path: workagent register on a missing path exits 2."""
    r = client.post("/api/runs", json={"command": "register",
                                       "args": ["/nonexistent/wt"]})
    assert r.status_code == 202
    rid = r.json()["run_id"]
    assert _wait_state(client, rid, {"needs_input"}) == "needs_input"
    detail = client.get(f"/api/runs/{rid}").json()
    assert detail["exit_code"] == 2
    assert detail["lines"], "stderr output must be captured"
    assert any("error" in ln["text"] for ln in detail["lines"])


class _FakeProc:
    pid = 999999

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_cancel_running_run(client, monkeypatch):
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr("workagent.webapp.os.killpg",
                        lambda pid, sig: sent.append((pid, sig)))
    app_state = client.app.state.registry
    run = app_state.create("sync", ["x"], "sync:test")
    run.proc = _FakeProc()
    r = client.post(f"/api/runs/{run.id}/cancel")
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"
    r = client.post(f"/api/runs/{run.id}/cancel")
    assert r.status_code == 409


def test_409_on_concurrent_target(client, monkeypatch):
    _stub_spawn(monkeypatch)
    client.post("/api/runs", json={"command": "register",
                                        "args": ["/tmp/a"]}
                     ).json()["run_id"]
    # registry entry exists and target 'register:/tmp/b' is held while
    # running; simulate a long-running run by creating one directly
    client.app.state.registry.targets["register:/tmp/b"] = "held"
    r2 = client.post("/api/runs", json={"command": "register",
                                        "args": ["/tmp/b"]})
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "conflict"
    client.app.state.registry.targets.pop("register:/tmp/b", None)


def test_buffer_truncation(tmp_path, isolated_config):
    _app(tmp_path)
    reg = Registry()
    run = reg.create("register", ["x"], "config")
    for i in range(MAX_LINES + 50):
        run.append(f"line-{i}\n")
    assert run.truncated is True
    assert len(run.lines) == MAX_LINES
    assert run.lines[0][1] == "line-50"


def test_eviction_never_drops_running(tmp_path, isolated_config):
    reg = Registry()
    for i in range(MAX_RUNS):
        run = reg.create("register", [f"/tmp/{i}"], f"t{i}")
        run.state = "succeeded"
    reg.create("register", ["/tmp/live"], "tlive")  # running
    reg.create("register", ["/tmp/live2"], "tlive2")
    states = [r.state for r in reg.runs.values()]
    assert "running" in states
    assert len(reg.runs) <= MAX_RUNS


# ── SSE ───────────────────────────────────────────────────────────────


def test_sse_streams_logs_and_state(client, monkeypatch):
    _stub_spawn(monkeypatch)
    rid = client.post("/api/runs", json={"command": "register",
                                         "args": ["/tmp/nope-wt"]}
                      ).json()["run_id"]
    _wait_state(client, rid, {"needs_input"})
    with client.stream("GET", f"/api/runs/{rid}/events") as resp:
        body = b"".join(resp.iter_raw()).decode()
    assert resp.status_code == 200
    assert "event: log" in body
    assert "event: state" in body
    assert '"state": "needs_input"' in body
    assert body.endswith("\n\n")


def test_sse_last_event_id_resume(client, monkeypatch):
    _stub_spawn(monkeypatch)
    rid = client.post("/api/runs", json={"command": "cleanup",
                                         "args": ["IPG-1", "--dry-run"]}
                      ).json()["run_id"]
    _wait_state(client, rid, {"succeeded"})
    with client.stream("GET", f"/api/runs/{rid}/events",
                       headers={"Last-Event-ID": "0"}) as resp:
        full = b"".join(resp.iter_raw()).decode()
    n_logs = full.count("event: log")
    with client.stream("GET", f"/api/runs/{rid}/events",
                       headers={"Last-Event-ID": str(n_logs)}) as resp:
        tail = b"".join(resp.iter_raw()).decode()
    assert "event: log" not in tail
    assert "event: state" in tail


# ── read endpoints ────────────────────────────────────────────────────


def test_info_and_static(client):
    info = client.get("/api/info").json()
    assert info["network_exposed"] is False
    assert info["port"] == 3344
    assert client.get("/").text == "<html>index</html>"
    assert client.get("/app.js").text == "console.log(1)"
    assert client.get("/deep/route").text == "<html>index</html>"
    assert client.get("/api/nope").status_code == 404


def test_status_endpoints(client):
    store.record_link("jira:IPG-1", {"issue": "IPG-1",
                                     "worktree": "/tmp/wt", "branch": "b",
                                     "repo": "/tmp/repo"})
    all_sessions = client.get("/api/status").json()
    assert "jira:IPG-1" in all_sessions
    detail = client.get("/api/status", params={"ref": "IPG-1"}).json()
    assert detail["key"] == "jira:IPG-1"
    assert detail["worktree"] == "/tmp/wt"
    missing = client.get("/api/status", params={"ref": "IPG-999"})
    assert missing.status_code == 404


def test_api_status_includes_harness(client):
    store.record_link("jira:IPG-1", {"issue": "IPG-1",
                                     "worktree": "/tmp/wt", "branch": "b",
                                     "repo": "/tmp/repo"})
    store.record_harness_run("jira:IPG-1", "omp", "/tmp/wt")
    all_sessions = client.get("/api/status").json()
    assert all_sessions["jira:IPG-1"]["harness"].startswith("omp ")
    detail = client.get("/api/status", params={"ref": "IPG-1"}).json()
    assert detail["harness"].startswith("omp ")


def test_api_status_includes_wt_valid(client, tmp_path):
    """Task 7 invalid-row seam: enriched entries carry wt_valid."""
    wt = tmp_path / "wt-valid"
    wt.mkdir()
    store.record_link("jira:IPG-1", {"issue": "IPG-1",
                                     "worktree": "/nonexistent/wt",
                                     "branch": "b", "repo": "/tmp/repo"})
    store.record_link("jira:IPG-2", {"issue": "IPG-2",
                                     "worktree": str(wt),
                                     "branch": "b", "repo": "/tmp/repo"})
    body = client.get("/api/status").json()
    assert body["jira:IPG-1"]["wt_valid"] is False
    assert body["jira:IPG-2"]["wt_valid"] in (True, False)
    detail = client.get("/api/status", params={"ref": "IPG-1"}).json()
    assert detail["wt_valid"] is False
    links = client.get("/api/links").json()
    assert links["worktrees"]["jira:IPG-1"]["wt_valid"] is False


def test_path_endpoint(client, tmp_path):
    wt = tmp_path / "wt2"
    wt.mkdir()
    store.record_link("jira:IPG-2", {"issue": "IPG-2",
                                     "worktree": str(wt), "branch": "b",
                                     "repo": "/tmp/repo"})
    r = client.get("/api/path", params={"ref": "IPG-2"})
    assert r.status_code == 200
    assert r.json()["worktree"] == str(wt)


def test_repos_and_links_endpoints(client):
    store.save_config({"repos": {"projectx": {"path": "/tmp/px"}},
                       "trackers": {"jira:IPG": {"repos": ["/tmp/px"]}}})
    repos = client.get("/api/repos").json()
    assert repos[0]["name"] == "projectx"
    links = client.get("/api/links").json()
    assert links["trackers"]["jira:IPG"]["repos"] == ["/tmp/px"]
    assert "worktrees" in links


def test_links_worktrees_key_launch_prefill(client):
    """Launch-page prefill validates refs against links.worktrees.

    Breaking rename (issue #7): the old "sessions" key is gone; the response
    must expose exactly {"trackers", "worktrees"} and the worktree mapping
    must contain recorded keys.
    """
    store.record_link("jira:IPG-1", {"issue": "IPG-1",
                                     "worktree": "/tmp/wt", "branch": "b",
                                     "repo": "/tmp/repo"})
    links = client.get("/api/links").json()
    assert set(links) == {"trackers", "worktrees"}
    assert "jira:IPG-1" in links["worktrees"]


def test_doctor_endpoint(client):
    r = client.get("/api/doctor").json()
    assert set(r) >= {"status", "tools", "live_hash"}


# ── argv building (same-code child) ──────────────────────────────────


def test_build_argv_uses_same_interpreter():
    argv = _build_argv("start", ["-v", "IPG-1", "--no-tty"])
    assert argv[0].endswith("python") or argv[0].endswith("python3")
    assert argv[1:3] == ["-m", "workagent"]
    assert argv[3] == "-v"
    assert argv[4:] == ["start", "IPG-1", "--no-tty"]


def test_error_shape_on_404(client):
    r = client.get("/api/runs/nope")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found",
                                  "message": "no such run: nope"}}


# ── static-dir resolution ────────────────────────────────────────────


def test_default_static_dir_prefers_source_tree(monkeypatch, tmp_path):
    from workagent import cli
    fake_src = tmp_path / "src"
    (fake_src / "web" / "dist").mkdir(parents=True)
    (fake_src / "web" / "dist" / "index.html").write_text("x")
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        cli, "__file__", str(fake_src / "src" / "workagent" / "cli.py"))
    assert cli._default_static_dir() == fake_src / "web" / "dist"


def test_default_static_dir_falls_back_to_receipt(monkeypatch, tmp_path):
    from workagent import cli
    installed_src = tmp_path / "installed"
    (installed_src / "web" / "dist").mkdir(parents=True)
    (installed_src / "web" / "dist" / "index.html").write_text("x")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(
        {"source_dir": str(installed_src), "source_hash": "abc"}))
    # cli.py lives outside any source tree with a built UI (site-packages),
    # home has no receipt at the default path -> point home at tmp and
    # write the receipt there.
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    receipt_dir = tmp_path / ".local" / "share" / "workagent"
    receipt_dir.mkdir(parents=True)
    receipt_dir.joinpath("install-receipt.json").write_text(
        json.dumps({"source_dir": str(installed_src)}))
    monkeypatch.setattr(
        cli, "__file__", str(tmp_path / "site-packages" / "workagent" / "cli.py"))
    assert cli._default_static_dir() == installed_src / "web" / "dist"


def test_default_static_dir_no_receipt_returns_source_default(
        monkeypatch, tmp_path):
    from workagent import cli
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        cli, "__file__", str(tmp_path / "site-packages" / "workagent" / "cli.py"))
    assert cli._default_static_dir() == \
        tmp_path / "web" / "dist"


# ── /api/issues + /api/candidates (candidates listing) ────────────────
