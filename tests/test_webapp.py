"""Tests for harness serve (FastAPI app in harness.webapp).

Mutating runs use a fake argv via monkeypatching Registry/_spawn so no
real command executes. Read endpoints reuse the real in-process helpers
against an isolated HARNESS_CONFIG_DIR.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from harness import store
from harness.webapp import (
    MAX_LINES,
    MAX_RUNS,
    Registry,
    Run,
    _build_argv,
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

    monkeypatch.setattr("harness.webapp._spawn", fake_spawn)


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
    """Real spawn path: harness register on a missing path exits 2."""
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
    monkeypatch.setattr("harness.webapp.os.killpg",
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
    # registry entry exists and target 'config' is held while running;
    # simulate a long-running run by creating one directly
    client.app.state.registry.targets["config"] = "held"
    r2 = client.post("/api/runs", json={"command": "register",
                                        "args": ["/tmp/b"]})
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "conflict"
    client.app.state.registry.targets.pop("config", None)


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
    assert "sessions" in links


def test_doctor_endpoint(client):
    r = client.get("/api/doctor").json()
    assert set(r) >= {"status", "tools", "live_hash"}


# ── argv building (same-code child) ──────────────────────────────────


def test_build_argv_uses_same_interpreter():
    argv = _build_argv("start", ["-v", "IPG-1", "--no-tty"])
    assert argv[0].endswith("python") or argv[0].endswith("python3")
    assert argv[1:3] == ["-m", "harness"]
    assert argv[3] == "-v"
    assert argv[4:] == ["start", "IPG-1", "--no-tty"]


def test_start_accepts_and_forwards_no_harness(client, monkeypatch):
    """`start --no-harness` passes validation and reaches the child argv."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "start", "args": ["IPG-1", "--no-tty", "--no-harness"],
        "confirm": True,
    })
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    run = client.app.state.registry.get(rid)
    assert run.argv[-2:] == ["--no-harness", "--yes"]  # server appends --yes
    _wait_state(client, rid, {"succeeded"})


def test_review_accepts_no_harness_shorthand_N(client, monkeypatch):
    """-N (CLI shorthand) passes web validation like --no-harness."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "review", "args": ["o/r#33", "-N", "--dry-run"],
    })
    assert r.status_code == 202, r.text


def test_validate_args_allows_verbose_global():
    _validate_args("sync", ["-v", "IPG-1"])


def test_error_shape_on_404(client):
    r = client.get("/api/runs/nope")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found",
                                  "message": "no such run: nope"}}


# ── static-dir resolution ────────────────────────────────────────────


def test_default_static_dir_prefers_source_tree(monkeypatch, tmp_path):
    from harness import cli
    fake_src = tmp_path / "src"
    (fake_src / "web" / "dist").mkdir(parents=True)
    (fake_src / "web" / "dist" / "index.html").write_text("x")
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        cli, "__file__", str(fake_src / "src" / "harness" / "cli.py"))
    assert cli._default_static_dir() == fake_src / "web" / "dist"


def test_default_static_dir_falls_back_to_receipt(monkeypatch, tmp_path):
    from harness import cli
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
    receipt_dir = tmp_path / ".local" / "share" / "harness"
    receipt_dir.mkdir(parents=True)
    receipt_dir.joinpath("install-receipt.json").write_text(
        json.dumps({"source_dir": str(installed_src)}))
    monkeypatch.setattr(
        cli, "__file__", str(tmp_path / "site-packages" / "harness" / "cli.py"))
    assert cli._default_static_dir() == installed_src / "web" / "dist"


def test_default_static_dir_no_receipt_returns_source_default(
        monkeypatch, tmp_path):
    from harness import cli
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        cli, "__file__", str(tmp_path / "site-packages" / "harness" / "cli.py"))
    assert cli._default_static_dir() == \
        tmp_path / "web" / "dist"
