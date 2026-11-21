"""Web ACP endpoints: harness options, run input, pending question (issue #112)."""

from __future__ import annotations

import json

from tests.webapp_helpers import client  # noqa: F401 (pytest fixture)


def _seed_running(client, monkeypatch, command="flow", args=None):
    """Create a run held in 'running' (blocking spawn, released by test)."""
    import threading
    import workagent.web_runs as w
    gate = threading.Event()
    real_spawn = w._spawn

    def blocking_spawn(run, registry):
        def reader() -> None:
            assert gate.wait(timeout=10)
            with run.lock:
                run.exit_code = 0
                run.state = "succeeded"
            registry.release_target(run)
        run.proc = object()
        import threading as _t
        _t.Thread(target=reader, daemon=True).start()

    monkeypatch.setattr("workagent.webapp._spawn", blocking_spawn)
    r = client.post("/api/runs", json={"command": command,
                                       "args": args if args is not None else ["template", "list"]})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    yield_run = client.app.state.registry.get(run_id)
    assert yield_run.state == "running"
    return run_id, gate


def test_harness_options_unknown_harness_404(client, monkeypatch):
    r = client.get("/api/harnesses/nope/options")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_harness_options_non_acp_supported_false(client, monkeypatch):
    import workagent.backend as b

    class _Legacy:
        name = "legacy"
        label = "Legacy"
        binary = "legacy-bin"
        supports_acp = False

        def discover_options(self, env_file=None):
            raise AssertionError("must not probe non-ACP harness")

    monkeypatch.setattr(b, "HARNESSES", {"legacy": _Legacy()})
    r = client.get("/api/harnesses/legacy/options")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["harness"] == "legacy"
    assert body["supported"] is False
    assert body["models"] == [] and body["efforts"] == []


def test_harness_options_acp_probe_ok(client, monkeypatch):
    import workagent.backend as b
    from workagent.acp_harness import HarnessOptions

    class _Acp:
        name = "fakeacp"
        label = "Fake"
        binary = "fakeacp-bin"
        supports_acp = True

        def discover_options(self, env_file=None):
            return HarnessOptions(models=[{"id": "m1", "name": "M1"}],
                                  efforts=[{"id": "hi", "name": "High"}],
                                  current_model="m1", current_effort="hi")

    monkeypatch.setattr(b, "HARNESSES", {"fakeacp": _Acp()})
    r = client.get("/api/harnesses/fakeacp/options")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supported"] is True
    assert body["models"] == [{"id": "m1", "name": "M1"}]
    assert body["current_model"] == "m1"
    assert body["cached"] is False


def test_harness_options_probe_error_warns(client, monkeypatch):
    import workagent.backend as b

    class _Broken:
        name = "broken"
        label = "Broken"
        binary = "broken-bin"
        supports_acp = True

        def discover_options(self, env_file=None):
            raise RuntimeError("no agent binary")

    monkeypatch.setattr(b, "HARNESSES", {"broken": _Broken()})
    r = client.get("/api/harnesses/broken/options")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supported"] is True
    assert "warning" in body


def test_harness_options_cached_second_hit(client, monkeypatch):
    import workagent.backend as b
    from workagent.acp_harness import HarnessOptions

    calls = []

    class _Acp:
        name = "slowacp"
        label = "Slow"
        binary = "slowacp-bin"
        supports_acp = True

        def discover_options(self, env_file=None):
            calls.append(1)
            return HarnessOptions(models=[{"id": "m", "name": "M"}])

    monkeypatch.setattr(b, "HARNESSES", {"slowacp": _Acp()})
    assert client.get("/api/harnesses/slowacp/options").json()["cached"] is False
    second = client.get("/api/harnesses/slowacp/options").json()
    assert second["cached"] is True
    assert len(calls) == 1


def test_run_input_unknown_run_404(client):
    r = client.post("/api/runs/nope/input", json={"text": "hi"})
    assert r.status_code == 404


def test_run_input_finished_run_409(client, monkeypatch):
    from tests.webapp_helpers import _stub_spawn
    _stub_spawn(monkeypatch)
    import workagent.webapp as w
    r = client.post("/api/runs", json={"command": "cleanup",
                                       "args": ["IPG-1", "--dry-run"]})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    for _ in range(100):
        import time
        run = w.REGISTRY.get(run_id) if hasattr(w, "REGISTRY") else None
        if run is None or run.state != "running":
            break
        time.sleep(0.05)
    r2 = client.post(f"/api/runs/{run_id}/input", json={"text": "hi"})
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "not_running"


def test_run_input_empty_text_422(client, monkeypatch):
    run_id, gate = _seed_running(client, monkeypatch)
    gate.set()
    r = client.post(f"/api/runs/{run_id}/input", json={"text": "  "})
    assert r.status_code in (400, 422)


def test_run_input_appends_input_jsonl(client, monkeypatch, tmp_path):
    run_id, gate = _seed_running(client, monkeypatch)
    run = client.app.state.registry.get(run_id)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    run.run_dir = str(run_dir)
    r = client.post(f"/api/runs/{run_id}/input", json={"text": "go on"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    payload = json.loads((run_dir / "input.jsonl").read_text().strip())
    assert payload["text"] == "go on"
    gate.set()


def test_summary_carries_pending_question(client, monkeypatch):
    run_id, gate = _seed_running(client, monkeypatch)
    reg = client.app.state.registry
    run = reg.get(run_id)
    ev = {"t": "question", "qid": "q1", "text": "proceed?",
          "options": ["yes", "no"], "multi": False}
    run.append("ACP:" + json.dumps(ev))
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["pending_question"] == ev
    # A bare (non-waiting) turn_end from the same unanswered turn keeps
    # the pending slot; only an answer event clears it.
    run.append('ACP: {"t": "turn_end", "id": "s1"}')
    body2 = client.get(f"/api/runs/{run_id}").json()
    assert body2["pending_question"] == ev
    run.append('ACP: {"t": "answer", "qid": "q1", "text": "yes"}')
    body3 = client.get(f"/api/runs/{run_id}").json()
    assert body3["pending_question"] is None
    gate.set()


def test_worktree_terminal_accepts_model_effort(client, monkeypatch, tmp_path, isolated_config):
    from workagent import store
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").mkdir()
    key = "jira:IPG-9"
    store.record_link(key, {"worktree": str(wt), "branch": "feat/x",
                            "issue": "https://x/jira/IPG-9"})
    import workagent.web_runs_routes as rr
    monkeypatch.setattr(rr, "_open_terminal_watch",
                        lambda *a, **k: 4242)
    r = client.post(f"/api/worktrees/{key}/terminal",
                    json={"mode": "harness", "model": "m1", "effort": "hi"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "harness"


def test_rehydrate_recovers_pending_question(client, monkeypatch, tmp_path):
    """Backfilled tailer lines surface the ACP question via the summary."""
    import workagent.web_runs as w
    reg = client.app.state.registry
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "output.log").write_text('ACP: {"t": "question", "qid": "q9", "text": "go?", "options": [], "multi": false}\n')
    run = w.Run(id="r1", command="flow", args=["template", "list"],
                target="config", run_dir=str(run_dir))
    reg.runs["r1"] = run
    w._start_tailer(run, reg, backfill=True)
    import time
    for _ in range(50):
        body = client.get("/api/runs/r1").json()
        if body.get("pending_question"):
            break
        time.sleep(0.05)
    assert body["pending_question"]["qid"] == "q9"
