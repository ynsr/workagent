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


def test_launch_acp_passes_mcp_servers(isolated_config, tmp_path, monkeypatch):
    """launch_acp session/new must include mcpServers: [] for omp acp compatibility."""
    from workagent import acp_harness as _a
    seen = []

    class _FakeClient:
        def set_notification_handler(self, handler):
            pass

        def request(self, method, params=None, timeout_s=None):
            seen.append((method, params))
            if method == "session/new":
                return {"sessionId": "s1"}
            if method == "session/prompt":
                return {"stopReason": "end_turn"}
            return {}

        def close(self):
            pass

    h = _a.OmpAcpHarness()
    monkeypatch.setattr(_a, "check_binary", lambda b: None)
    monkeypatch.setattr(h, "_open_client", lambda *a, **k: _FakeClient())
    code = h.launch_acp("do work", workdir=str(tmp_path), no_tty=True)
    assert code == 0
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


def test_run_supervised_exports_workagent_run_dir(tmp_path):
    """run_supervised sets WORKAGENT_RUN_DIR=<run_dir> in child env."""
    import sys
    from workagent.run_supervised import run_supervised

    run_dir = tmp_path / "run-128"
    out_file = tmp_path / "seen_env.txt"
    code_str = (
        "import os, pathlib; "
        f"pathlib.Path({str(out_file)!r}).write_text(os.environ.get('WORKAGENT_RUN_DIR', ''))"
    )
    rc = run_supervised(run_dir, [sys.executable, "-c", code_str])
    assert rc == 0
    assert out_file.read_text() == str(run_dir)


def test_acp_client_captures_stderr_tail_on_init_failure(tmp_path, isolated_config):
    """Init failure surfaces stderr tail in HarnessError (no AttributeError)."""
    import sys
    import pytest
    from workagent.acp_harness import OmpAcpHarness
    from workagent.errors import HarnessError

    bad_agent = tmp_path / "bad_agent.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stderr.write('boom line 1\\nboom line 2\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(1)\n"
    )
    h = OmpAcpHarness()
    h.acp_argv = lambda: [sys.executable, str(bad_agent)]
    with pytest.raises(HarnessError) as exc_info:
        h.launch_acp("hi", str(tmp_path), no_tty=True)
    msg = str(exc_info.value)
    assert "boom line 2" in msg


def test_read_answer_unattended_no_tty_returns_waiting_exit_2(tmp_path, isolated_config, monkeypatch):
    """no_tty=True without WORKAGENT_RUN_DIR/input_stream returns None -> WAITING_USER_RESPONSE exit 2."""
    import io
    from workagent.agy_harness import AgyAcpHarness

    monkeypatch.delenv("WORKAGENT_RUN_DIR", raising=False)
    h = AgyAcpHarness()
    assert h._read_answer({"qid": "q1"}, input_stream=None, no_tty=True) is None
    # no_tty=False without input_stream reads sys.stdin
    monkeypatch.setattr("sys.stdin", io.StringIO("yes please\n"))
    assert h._read_answer({"qid": "q1"}, input_stream=None, no_tty=False) == "yes please"


def test_launch_acp_sigterm_kills_child_process_group(tmp_path, isolated_config):
    """SIGTERM to child running launch_acp kills <bin> acp with no orphan."""
    import os
    import signal
    import subprocess
    import sys
    import time

    pid_file = tmp_path / "agent.pid"
    slow_agent = tmp_path / "slow_agent.py"
    slow_agent.write_text(
        "import json, os, pathlib, sys, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "for raw in sys.stdin:\n"
        "    msg = json.loads(raw)\n"
        "    m = msg.get('method')\n"
        "    if m == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':1}}), flush=True)\n"
        "    elif m == 'authenticate':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{}}), flush=True)\n"
        "    elif m == 'session/new':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'sessionId':'s1'}}), flush=True)\n"
        "    elif m == 'session/prompt':\n"
        "        time.sleep(30)\n"
    )
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys\n"
        "from workagent.acp_harness import OmpAcpHarness\n"
        "h = OmpAcpHarness()\n"
        f"h.acp_argv = lambda: [sys.executable, {str(slow_agent)!r}]\n"
        f"h.launch_acp('hi', {str(tmp_path)!r}, no_tty=True)\n"
    )
    proc = subprocess.Popen([sys.executable, str(runner)], start_new_session=True)
    deadline = time.time() + 5.0
    while not pid_file.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert pid_file.exists()
    agent_pid = int(pid_file.read_text().strip())
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)
    time.sleep(0.2)
    try:
        os.kill(agent_pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, f"agent pid {agent_pid} was orphaned after SIGTERM"

def test_harness_summary_parses_acp_mirrored_transcript(tmp_path):
    """last_assistant_text and tool_error_rate parse ACP-mirrored JSONL."""
    import json
    from workagent import harness_summary as hs

    p = tmp_path / "acp.jsonl"
    events = [
        {"t": "thought", "id": "s1", "text": "thinking..."},
        {"t": "message", "id": "s1", "text": "Turn 1 question?"},
        {"t": "question", "id": "q1", "qid": "q1", "text": "Proceed?"},
        {"t": "turn_end", "id": "s1"},
        {"t": "answer", "id": "s1", "qid": "q1", "text": "yes"},
        {"t": "tool_call", "id": "tc1", "name": "read", "status": "running", "output": ""},
        {"t": "tool_call", "id": "tc1", "name": "read", "status": "completed", "output": "ok"},
        {"t": "tool_call", "id": "tc2", "name": "bash", "status": "failed", "output": "error: boom"},
        {"t": "message", "id": "s1", "text": "All done.\n"},
        {"t": "message", "id": "s1", "text": "JOB_DONE"},
        {"t": "turn_end", "id": "s1"},
    ]
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    assert hs.last_assistant_text(p, max_chars=None) == "All done.\nJOB_DONE"
    assert hs.tool_error_rate(p) == (1, 2)


def test_run_harness_acp_no_tty_runs_validate_headless(tmp_path, isolated_config, monkeypatch):
    """_run_harness(acp=True, launch=False, no_tty=True) does NOT preview; runs validate_headless + VERDICT_SUFFIX."""
    from workagent import backend, cli_harness, cli_validate

    calls = {}
    h = backend.get_harness("omp")

    def fake_launch_acp(prompt, workdir, **kw):
        calls["prompt"] = prompt
        calls["kw"] = kw
        sf = kw.get("session_file")
        if sf:
            import json
            from pathlib import Path
            Path(sf).write_text(json.dumps({"t": "message", "text": "Done.\nJOB_DONE"}) + "\n")
        return 0

    monkeypatch.setattr(h, "launch_acp", fake_launch_acp)
    monkeypatch.setattr(cli_validate, "validate_headless",
                        lambda **kw: (calls.setdefault("validated", True),
                                      {"verdict": "JOB_DONE", "attempts": 1,
                                       "runs": [kw["run_once"](kw["prompt"])]})[1])
    res = {"command": "start", "branch": "feat/128", "key": "github:o/r#128"}
    cli_harness._run_harness("omp", "Fix bug", str(tmp_path), str(tmp_path),
                             no_tty=True, launch=False, result=res, json_output=True,
                             acp=True)
    assert calls.get("validated") is True
    assert backend.VERDICT_SUFFIX.strip() in calls["prompt"]
    assert calls["kw"]["input_stream"] is None
    assert calls["kw"]["no_tty"] is True


def test_run_harness_acp_tty_runs_launch_acp_interactively(tmp_path, isolated_config, monkeypatch):
    """_run_harness(acp=True, launch=False, no_tty=False) calls launch_acp with sys.stdin (no validate_headless)."""
    import sys
    from workagent import backend, cli_harness

    calls = {}
    h = backend.get_harness("omp")

    def fake_launch_acp(prompt, workdir, **kw):
        calls["prompt"] = prompt
        calls["kw"] = kw
        return 0

    monkeypatch.setattr(h, "launch_acp", fake_launch_acp)
    res = {"command": "start", "branch": "feat/128", "key": "github:o/r#128"}
    cli_harness._run_harness("omp", "Fix bug", str(tmp_path), str(tmp_path),
                             no_tty=False, launch=False, result=res, json_output=True,
                             acp=True)
    assert calls["kw"]["no_tty"] is False
    assert calls["kw"]["input_stream"] is sys.stdin
    assert backend.VERDICT_SUFFIX.strip() not in calls["prompt"]


def test_cli_action_resolve_mode_accepts_acp():
    from workagent.cli_action import _resolve_mode
    assert _resolve_mode({}, launch=False, terminal=False, acp=True) == "acp"
    assert _resolve_mode({"default_options": {"run_mode": "acp"}}, launch=False, terminal=False) == "acp"
    assert _resolve_mode({}, launch=False, terminal=False, run_mode="acp") == "acp"


def test_cli_adhoc_acp_does_not_collide_with_launch(tmp_path, isolated_config, monkeypatch):
    from workagent import cli_adhoc, store, worktrees
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(store, "load_links", lambda: {"k": {"worktree": str(wt), "repo": str(wt), "active": 1}})
    monkeypatch.setattr(worktrees, "resolve_any", lambda ref, links: ("k", links["k"], None))
    monkeypatch.setattr(worktrees, "is_valid_worktree", lambda p: True)
    seen = {}
    monkeypatch.setattr(cli_adhoc, "_guard_harness", lambda *a, **kw: None)
    monkeypatch.setattr(cli_adhoc, "_run_harness", lambda *a, **kw: seen.update({"args": a, "kw": kw}))
    cli_adhoc.adhoc.__wrapped__(["k"], prompt="hello", launch=False, terminal=False,
                                harness="omp", acp=True, run_mode=None, model="m1", effort="high",
                                extra_prompt=None, env_file=None, session_file=None,
                                dry_run=False, yes=True, json_output=True)
    # positional arg index 5 is `launch` — must be False when acp=True
    assert seen["args"][5] is False
    assert seen["kw"]["acp"] is True
    assert seen["kw"]["model"] == "m1"


def test_cli_review_child_argv_forwards_acp_model_effort():
    from workagent import cli_review
    argv = cli_review._child_argv("github:o/r#1", fix=False, harness="omp",
                                  post_comments=False, acp=True, run_mode="acp",
                                  model="m1", effort="high")
    assert "--acp" in argv
    assert "--model" in argv and "m1" in argv
    assert "--effort" in argv and "high" in argv


def test_acp_client_stderr_tail_includes_bytes_written_before_reader_start(tmp_path, isolated_config):
    """Risk 1 RED: stderr drained synchronously even if reader thread never ran.

    Simulates the init-failure race: process wrote stderr and exited before
    the daemon reader consumed it. stderr_tail() must still surface it.
    """
    import sys
    from workagent.acp_harness import AcpClient

    agent = tmp_path / "instant_exit.py"
    agent.write_text("import sys\nsys.stderr.write('instant boom\\n')\nsys.stderr.flush()\n")
    client = AcpClient([sys.executable, str(agent)])
    # Simulate worst case: reader thread did not consume before exit.
    client._proc.wait(timeout=10)
    client._stderr_reader.join(timeout=5)
    # Drain whatever is left without relying on the thread having run.
    tail = client.stderr_tail()
    client.close()
    assert "instant boom" in tail


def test_acp_client_drain_fd_captures_available_pipe_bytes():
    """_drain_fd captures available fd bytes without blocking."""
    import os
    from workagent.acp_harness import AcpClient

    r, w = os.pipe()
    os.write(w, b"late boom\n")
    os.close(w)
    lines: list = []
    n = AcpClient._drain_fd(r, lines, None)
    os.close(r)
    assert n == 1
    assert lines == ["late boom"]


def test_run_harness_non_1_exit_marks_run_failed(tmp_path, isolated_config, monkeypatch):
    """Non-zero ACP exit codes other than 2 (e.g. 143 on SIGTERM) must mark DB run failed."""
    import pytest
    from workagent import backend, cli_harness, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)
    wt = tmp_path / "wt"
    wt.mkdir()
    h = backend.get_harness("omp")
    monkeypatch.setattr(h, "launch_acp", lambda *a, **kw: 143)

    res = {"command": "start", "branch": "feat/128", "key": "github:o/r#128"}
    with pytest.raises(cli_harness.HarnessError) as exc:
        cli_harness._run_harness(
            "omp", "hi", str(wt), str(wt),
            no_tty=False, launch=False, result=res, json_output=True,
            run_key="github:o/r#128", acp=True,
        )
    assert exc.value.exit_code == 143

    runs = sq.list_runs(db)
    assert len(runs) == 1
    assert runs[0]["state"] == "failed"


def test_launch_acp_session_load_and_on_session_id(tmp_path):
    """T07.1: launch_acp calls on_session_id and uses session/load when advertised."""
    import sys
    from workagent.agy_harness import AgyAcpHarness

    fake_agent = tmp_path / "load_agent.py"
    fake_agent.write_text(
        "import json, sys\n"
        "for raw in sys.stdin:\n"
        "    msg = json.loads(raw)\n"
        "    m = msg.get('method')\n"
        "    if m == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':1,'agentCapabilities':{'loadSession':True}}}), flush=True)\n"
        "    elif m == 'session/load':\n"
        "        sid = msg['params']['sessionId']\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'sessionId':sid}}), flush=True)\n"
        "    elif m == 'session/prompt':\n"
        "        sid = msg['params']['sessionId']\n"
        "        print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':sid,'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'resumed ok'}}}}), flush=True)\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'stopReason':'end_turn'}}), flush=True)\n"
    )
    h = AgyAcpHarness()
    h.acp_argv = lambda: [sys.executable, str(fake_agent)]
    captured_sids: list[str] = []
    rc = h.launch_acp(
        "follow up", str(tmp_path), no_tty=True,
        resume_session_id="sess-prev-1",
        on_session_id=lambda s: captured_sids.append(s),
    )
    assert rc == 0
    assert captured_sids == ["sess-prev-1"]


def test_launch_acp_falls_back_to_session_new_with_resume_context(tmp_path):
    """T07.1: when loadSession is not advertised, fall back to session/new + resume_context."""
    import sys
    from workagent.agy_harness import AgyAcpHarness

    prompt_log = tmp_path / "prompt.txt"
    fake_agent = tmp_path / "fallback_agent.py"
    fake_agent.write_text(
        "import json, sys\n"
        "for raw in sys.stdin:\n"
        "    msg = json.loads(raw)\n"
        "    m = msg.get('method')\n"
        "    if m == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':1}}), flush=True)\n"
        "    elif m == 'session/new':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'sessionId':'sess-new-2'}}), flush=True)\n"
        "    elif m == 'session/prompt':\n"
        f"        open({str(prompt_log)!r}, 'w').write(msg['params']['prompt'][0]['text'])\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'stopReason':'end_turn'}}), flush=True)\n"
    )
    h = AgyAcpHarness()
    h.acp_argv = lambda: [sys.executable, str(fake_agent)]
    captured_sids: list[str] = []
    rc = h.launch_acp(
        "please fix tests", str(tmp_path), no_tty=True,
        resume_session_id="sess-old-1",
        resume_context="[Previous summary] built feature X",
        on_session_id=lambda s: captured_sids.append(s),
    )
    assert rc == 0
    assert captured_sids == ["sess-new-2"]
    sent = prompt_log.read_text()
    assert "[Previous summary] built feature X" in sent
    assert "please fix tests" in sent


def test_launch_acp_uses_resume_context_without_resume_session_id(tmp_path):
    """T07.1: resume_context is still prepended when resume_session_id is None."""
    import sys
    from workagent.agy_harness import AgyAcpHarness

    prompt_log = tmp_path / "prompt.txt"
    fake_agent = tmp_path / "no_sid_agent.py"
    fake_agent.write_text(
        "import json, sys\n"
        "for raw in sys.stdin:\n"
        "    msg = json.loads(raw)\n"
        "    m = msg.get('method')\n"
        "    if m == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':1}}), flush=True)\n"
        "    elif m == 'session/new':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'sessionId':'sess-new-3'}}), flush=True)\n"
        "    elif m == 'session/prompt':\n"
        f"        open({str(prompt_log)!r}, 'w').write(msg['params']['prompt'][0]['text'])\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'stopReason':'end_turn'}}), flush=True)\n"
    )
    h = AgyAcpHarness()
    h.acp_argv = lambda: [sys.executable, str(fake_agent)]
    rc = h.launch_acp(
        "continue from headless run", str(tmp_path), no_tty=True,
        resume_session_id=None,
        resume_context="[Previous summary] headless output",
    )
    assert rc == 0
    sent = prompt_log.read_text()
    assert "[Previous summary] headless output" in sent
    assert "continue from headless run" in sent
