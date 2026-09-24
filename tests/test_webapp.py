"""Tests for workagent serve (FastAPI app in workagent.webapp).

Mutating runs use a fake argv via monkeypatching Registry/_spawn so no
real command executes. Read endpoints reuse the real in-process helpers
against an isolated WORKAGENT_CONFIG_DIR.
"""

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


def _app(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    dist.joinpath("index.html").write_text("<html>index</html>")
    dist.joinpath("app.js").write_text("console.log(1)")
    return create_app(dist, "127.0.0.1", 3344, ["devbox.local"]), dist

@pytest.fixture
def client(tmp_path, isolated_config):
    app, _dist = _app(tmp_path)
    with TestClient(app, base_url="http://localhost:3344") as c:
        yield c


# ── security middleware ───────────────────────────────────────────────


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


def _stub_spawn(monkeypatch):
    """Replace the real child process with a scripted fake."""

    def fake_spawn(run: Run, registry: Registry) -> None:
        def reader() -> None:
            script = FAKE_SCRIPTS.get(run.command, {})
            lines, code = script.get(tuple(run.args) or (), ([], 0))
            for ln in lines:
                run.append(ln + "\n")
            with run.lock:
                run.exit_code = code
                run.state = {0: "succeeded", 2: "needs_input"}.get(
                    code, "failed")
            registry.release_target(run)

        run.proc = object()  # non-None so cancel paths take the fake branch
        threading.Thread(target=reader, daemon=True).start()

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)


FAKE_SCRIPTS: dict[str, dict[tuple, tuple[list, int]]] = {
    "cleanup": {("IPG-1", "--dry-run"): (["cleanup: would remove"], 0)},
    "register": {("/tmp/nope-wt",): (["err"], 2)},
    "start": {},
}


def _wait_state(client, rid, states, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/runs/{rid}").json()["state"]
        if s in states:
            return s
        time.sleep(0.02)
    raise AssertionError(f"run {rid} never reached {states}: {s}")


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


def test_start_accepts_and_forwards_no_runtime(client, monkeypatch):
    """`start --no-runtime` passes validation and reaches the child argv."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "start", "args": ["IPG-1", "--no-tty", "--no-runtime"],
        "confirm": True,
    })
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    run = client.app.state.registry.get(rid)
    assert "--no-runtime" in run.argv and "--yes" in run.argv  # server appends --yes
    assert "--session-file" in run.argv and run.session_file.endswith(".jsonl")
    _wait_state(client, rid, {"succeeded"})


def test_review_accepts_no_runtime_shorthand_N(client, monkeypatch):
    """-N (CLI shorthand) passes web validation like --no-runtime."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "review", "args": ["o/r#33", "-N", "--dry-run"],
    })
    assert r.status_code == 202, r.text


def test_start_rejects_stale_no_harness_alias(client):
    """Old `--no-harness` (pre-#14 rename) is rejected with a bad_arg hint (issue #15)."""
    r = client.post("/api/runs", json={
        "command": "start", "args": ["IPG-1", "--no-tty", "--no-harness"],
        "confirm": True,
    })
    assert r.status_code == 400, r.text
    body = r.json()["error"]
    assert body["code"] == "bad_arg" and "--no-runtime" in body["message"]

def test_validate_args_allows_verbose_global():
    _validate_args("sync", ["-v", "IPG-1"])


def test_webapp_review_all_flags_and_target():
    _validate_args("review", ["--all"])
    _validate_args("review", ["--all", "--sequential", "--fix"])
    assert _target_for("review", ["--all"]) == "review:all"
    assert _target_for("review", ["o/r#33"]) == "review:o/r#33"


def test_webapp_open_allowed_and_target():
    _validate_args("open", ["jira:X-1"])
    assert _target_for("open", ["jira:X-1"]) == "open:jira:X-1"


def test_webapp_cleanup_merged_target():
    assert _target_for("cleanup", ["--merged"]) == "cleanup:all"
    assert _target_for("cleanup", ["IPG-1"]) == "cleanup:IPG-1"


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


def _register_repo(tmp_path, name="proj"):
    repo = tmp_path / name
    repo.mkdir()
    cfg = store.load_config()
    cfg["repos"] = {name: {"path": str(repo)}}
    # Same leak guard as test_cli._candidates_env: keep the real
    # ~/dev/worktrees out of candidates endpoint tests.
    empty = tmp_path / "empty-scan"
    empty.mkdir(exist_ok=True)
    cfg["scan_root"] = str(empty)
    store.save_config(cfg)
    return str(repo)


def _issue_row(days_ago, key="jira:IPG-1"):
    from datetime import datetime, timedelta, timezone

    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"key": key, "title": "T", "url": "u", "status": "To Do",
            "created": created.isoformat()}


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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('k', '/wt', 'b', 'r')")
    sid = sq.insert_session(db, worktree_ref="k", runtime_name="omp",
                            initiator_command="start", prompt="hello",
                            file_path="/tmp/x.jsonl")
    body = client.get("/api/sessions").json()
    assert body["sessions"][0]["id"] == sid
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["prompt"] == "hello" and detail["runs"] == []
    assert client.get("/api/sessions/nope").status_code == 404


def test_api_repos_includes_tracker(client, monkeypatch):
    import workagent.store as store
    cfg = store.load_config()
    cfg["repos"] = {"p": {"path": "/tmp/proj"}}
    cfg["trackers"] = {"jira:IPG": {"repos": ["/tmp/proj"]}}
    store.save_config(cfg)
    r = client.get("/api/repos")
    assert r.status_code == 200
    assert r.json()[0]["tracker"] == "jira:IPG"


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
    sq.upsert_tracker(db, "jira:IPG")
    sq.upsert_tracker(db, "github:o/r")
    sq.register_repo_row(db, "p", "/tmp/proj", "jira:IPG")
    sq.add_tracker_repo(db, "github:o/r", "/tmp/proj")
    r = client.get("/api/repos")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["trackers"] == ["github:o/r", "jira:IPG"]
    assert row["tracker"] == "github:o/r"


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
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key)"
                     " VALUES ('k', '/wt', 'b', 'r')")
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
    for cmd in ("start", "review", "cleanup", "sync", "open", "register", "repo", "link", "tracker"):
        assert cmd in _w.SPECS, f"SPECS missing {cmd}"
