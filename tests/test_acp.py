"""ACP transport + harness tests: AcpClient id correlation, chunks,
defensive request_permission auto-allow, SIGTERM cleanup, timeouts,
malformed lines, env.sh sourcing; option parsers; argv yolo/model flags;
env.sh paths; fake-agent e2e ACP run loop."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


def _spawn_args(mode: str = "chunks"):
    return [sys.executable, str(FIXTURE), mode]


def test_acp_client_chunk_flow_and_permission_auto_allow(isolated_config, tmp_path):
    """AcpClient: chunks stream in order, request_permission auto-allowed."""
    from workagent import acp_harness as _a
    from workagent import backend as _b
    updates = []
    client = _a.AcpClient(_spawn_args("chunks"), workdir=str(tmp_path))
    client.set_notification_handler(updates.append)
    try:
        assert client.request("initialize", {"protocolVersion": 1})["protocolVersion"] == 1
        assert client.request("authenticate", {"methodId": "agent"}) == {}
        new = client.request("session/new", {"cwd": str(tmp_path)})
        assert new["sessionId"]
        res = client.request("session/prompt",
                             {"sessionId": new["sessionId"], "prompt": [{"type": "text", "text": "hi"}]})
        assert res["stopReason"] == "end_turn"
    finally:
        client.close()
    kinds = [u.get("update", {}).get("sessionUpdate") for u in updates]
    assert "agent_thought_chunk" in kinds
    assert "agent_message_chunk" in kinds
    assert "usage_update" in kinds
    assert "end_turn" in kinds


def test_acp_client_kills_child_on_close(isolated_config, tmp_path):
    """No orphaned agent: close() SIGTERMs the child."""
    from workagent import acp_harness as _a
    client = _a.AcpClient(_spawn_args("chunks"), workdir=str(tmp_path))
    pid = client.pid
    assert pid and pid > 0
    client.close()
    import time
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("agent child survived close()")


def test_acp_client_timeout(isolated_config, tmp_path):
    """Hanging agent → HarnessTimeout (child already killed)."""
    from workagent import acp_harness as _a
    from workagent.errors import HarnessTimeout
    client = _a.AcpClient(_spawn_args("hang"), workdir=str(tmp_path), timeout_s=1)
    try:
        with pytest.raises(HarnessTimeout):
            client.request("initialize", {"protocolVersion": 1}, timeout_s=1)
    finally:
        client.close()


def test_acp_client_malformed_line_and_env_sh(isolated_config, tmp_path, monkeypatch):
    """Malformed lines are skipped; env.sh vars reach the spawned agent."""
    from workagent import acp_harness as _a
    env_file = tmp_path / "env.sh"
    env_file.write_text('export WORKAGENT_ACP_TEST_VAR="sourced-ok"\n')
    seen = {}

    real_popen = subprocess.Popen

    def _spy(*a, **k):
        seen["env"] = dict(k.get("env") or {})
        return real_popen(*a, **k)

    monkeypatch.setattr(_a.subprocess, "Popen", _spy)
    client = _a.AcpClient(_spawn_args("badline"), workdir=str(tmp_path),
                          env_file=str(env_file))
    try:
        assert client.request("initialize", {"protocolVersion": 1})["protocolVersion"] == 1
    finally:
        client.close()
    assert seen["env"].get("WORKAGENT_ACP_TEST_VAR") == "sourced-ok"


def test_option_parsers_use_recorded_payloads():
    """Per-harness session/new parsers map recorded omp/agy payloads."""
    from tests.fixtures.fake_acp_agent import AGY_SESSION_NEW, OMP_SESSION_NEW
    from workagent import acp_harness as _a
    omp_opts = _a.OmpAcpHarness().parse_options(OMP_SESSION_NEW)
    assert [m["id"] for m in omp_opts.models] == ["bifrost/cline-pass/cline-pass/muse-spark-1.2"]
    assert [e["id"] for e in omp_opts.efforts] == ["off", "low", "medium", "high", "xhigh"]
    assert omp_opts.current_effort == "medium"
    from workagent.agy_harness import AgyAcpHarness
    agy_opts = AgyAcpHarness().parse_options(AGY_SESSION_NEW)
    assert [m["id"] for m in agy_opts.models] == ["gemini-3.8-flash-low", "gemini-3.8-pro-high"]
    assert agy_opts.current_model == "gemini-3.8-flash-low"
    assert [e["id"] for e in agy_opts.efforts] == ["low", "medium", "high", "xhigh"]
    # Real omp acp payloads use "id" instead of "configId".
    omp_id_payload = {
        "sessionId": "s2",
        "configOptions": [
            {"id": "model", "currentValue": "m-omp",
             "options": [{"value": "m-omp", "name": "OMP Model"}]},
            {"id": "thinking", "currentValue": "high",
             "options": [{"value": "low", "name": "low"}, {"value": "high", "name": "high"}]},
        ],
    }
    omp_id_opts = _a.OmpAcpHarness().parse_options(omp_id_payload)
    assert [m["id"] for m in omp_id_opts.models] == ["m-omp"]
    assert omp_id_opts.current_model == "m-omp"
    assert [e["id"] for e in omp_id_opts.efforts] == ["low", "high"]
    assert omp_id_opts.current_effort == "high"


def test_discover_options_passes_mcp_servers(monkeypatch):
    """session/new must include mcpServers: [] for omp acp compatibility."""
    from workagent import acp_harness as _a
    seen = []

    class _FakeClient:
        def request(self, method, params=None, timeout_s=None):
            seen.append((method, params))
            if method == "session/new":
                return {"sessionId": "s1", "configOptions": []}
            return {}

        def close(self):
            pass

    h = _a.OmpAcpHarness()
    monkeypatch.setattr(_a, "check_binary", lambda b: None)
    monkeypatch.setattr(h, "_open_client", lambda *a, **k: _FakeClient())
    h.discover_options()
    new_call = next(p for m, p in seen if m == "session/new")
    assert new_call["mcpServers"] == []


def test_omp_argv_yolo_and_model_flags(isolated_config, tmp_path):
    """OmpAcpHarness: yolo on every argv + --model=/--thinking= mapping."""
    from workagent import acp_harness as _a
    h = _a.OmpAcpHarness()
    assert h.name == "omp" and h.supports_acp and h.binary == "omp"
    headless = h.command_argv("do it", no_tty=True, model="m1", effort="high")
    assert "--approval-mode=yolo" in headless and "--auto-approve" in headless
    assert "--model=m1" in headless and "--thinking=high" in headless
    assert h.acp_argv() == ["omp", "acp", "--approval-mode=yolo"]
    assert h.authenticate_method() == "agent"
    tty = h.command_argv("do it", no_tty=False)
    assert "--approval-mode=yolo" in tty


def test_agy_argv_yolo_and_model_flags(isolated_config):
    """AgyAcpHarness: dangerously-skip-permissions + --model/--effort."""
    from workagent.agy_harness import AgyAcpHarness
    h = AgyAcpHarness()
    assert h.name == "agy" and h.supports_acp and h.binary == "agy-guard"
    argv = h.command_argv("do it", no_tty=True, model="m1", effort="high")
    assert "--dangerously-skip-permissions" in argv
    assert "--model" in argv and "m1" in argv
    assert "--effort" in argv and "high" in argv
    assert h.acp_argv() == ["agy-guard", "acp", "--dangerously-skip-permissions"]
    assert h.authenticate_method() is None


def test_env_sh_everywhere(isolated_config, tmp_path, monkeypatch):
    """launch_env(None)/merged_env(None) source <config_dir>/env.sh."""
    from workagent import env as _env
    from workagent import store as _store
    env_path = tmp_path / "env.sh"
    env_path.write_text('export WORKAGENT_ENV_TEST="env-ok"\n')
    monkeypatch.setattr(_store, "config_dir", lambda: tmp_path)
    _env._SOURCED_CACHE.clear()
    assert _env.merged_env(None).get("WORKAGENT_ENV_TEST") == "env-ok"
    _env._SOURCED_CACHE.clear()
    assert _env.launch_env(None).get("WORKAGENT_ENV_TEST") == "env-ok"


def test_acp_run_loop_fake_agent_e2e(isolated_config, tmp_path, monkeypatch, capsys):
    """launch_acp streams ordered ACP: events to stdout, mirrors session_file."""
    from workagent import acp_harness as _a
    session = tmp_path / "run.jsonl"
    monkeypatch.setattr(_a.OmpAcpHarness, "acp_argv",
                        lambda self: _spawn_args("chunks"))
    monkeypatch.setattr(_a.OmpAcpHarness, "authenticate_method",
                        lambda self: "agent")
    h = _a.OmpAcpHarness()
    code = h.launch_acp("do work", workdir=str(tmp_path),
                        session_file=str(session),
                        input_stream=None, no_tty=True)
    assert code == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("ACP:")]
    assert lines, "no ACP: event lines emitted"
    kinds = [json.loads(ln[4:]).get("t") for ln in lines]
    assert kinds[0] in ("thought", "message")
    assert "turn_end" in kinds
    assert session.is_file()
    mirrored = [json.loads(ln) for ln in session.read_text().splitlines() if ln.strip()]
    assert mirrored and all("t" in ev for ev in mirrored)


def test_read_answer_accepts_text_key(isolated_config, tmp_path, monkeypatch):
    """_read_answer returns the `text` field the web route persists."""
    from workagent import acp_harness as _a
    run_dir = tmp_path / "rd"
    run_dir.mkdir()
    (run_dir / "input.jsonl").write_text('{"text": "go on", "qid": "q1"}\n')
    monkeypatch.setenv("WORKAGENT_RUN_DIR", str(run_dir))
    h = _a.OmpAcpHarness()
    assert h._read_answer({"qid": "q1"}, None, True) == "go on"


def test_agy_question_answer_roundtrip(isolated_config, tmp_path, monkeypatch, capsys):
    """Agy ask_question → input.jsonl text → second session/prompt with that text."""
    import threading
    import time
    from workagent import acp_harness as _a
    from workagent.agy_harness import AgyAcpHarness
    session = tmp_path / "agy.jsonl"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("WORKAGENT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(AgyAcpHarness, "acp_argv",
                        lambda self: _spawn_args("agy_once"))
    h = AgyAcpHarness()
    def _answer():
        # Wait for the question in the mirrored transcript, then append
        # the answer. The tail reads from offset 0, so pre/post timing
        # no longer matters — only the question ordering does.
        for _ in range(400):
            time.sleep(0.05)
            try:
                text = session.read_text()
            except OSError:
                continue
            if '"t": "question"' in text or '"t":"question"' in text:
                break
        (run_dir / "input.jsonl").write_text('{"text": "Yes", "qid": "call-ask-1"}\n')

    t = threading.Thread(target=_answer, daemon=True)
    t.start()
    code = h.launch_acp("do work", workdir=str(tmp_path),
                        session_file=str(session),
                        input_stream=None, no_tty=True)
    t.join(timeout=60)
    assert code == 0
    assert session.is_file()
    kinds = [json.loads(ln).get("t") for ln in session.read_text().splitlines() if ln.strip()]
    assert "question" in kinds
    assert "answer" in kinds
