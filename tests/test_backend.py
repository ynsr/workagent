"""Tests for the omp launcher (backend.py)."""

from __future__ import annotations

import os

import pytest

from workagent import backend
from workagent.errors import HarnessError


class _Execed(Exception):
    """Sentinel: os.execvp was about to replace the process."""

    def __init__(self, file, argv, cwd):
        super().__init__(file)
        self.file, self.argv, self.cwd = file, argv, cwd


def _fake_omp(monkeypatch):
    monkeypatch.setattr(backend.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_launch_tty_chdirs_into_worktree(tmp_path, monkeypatch):
    """TTY exec must run in the worktree, not the caller's cwd (regression:
    workagent start used to leave omp in the original directory)."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)
    _fake_omp(monkeypatch)
    monkeypatch.setattr(backend.os, "execvp",
                        lambda file, argv: (_ for _ in ()).throw(_Execed(file, argv, os.getcwd())))
    with pytest.raises(_Execed) as excinfo:
        backend.launch("omp", "do things", str(worktree), no_tty=False)
    assert excinfo.value.cwd == str(worktree)
    assert excinfo.value.argv == ["omp", "--approval-mode=yolo", "do things"]


def test_launch_no_tty_runs_child_in_worktree(tmp_path, monkeypatch):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _fake_omp(monkeypatch)
    seen = {}

    def fake_run(argv, cwd, **kw):
        seen["argv"], seen["cwd"] = argv, cwd

        class Proc:
            returncode = 0
        return Proc()

    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    assert backend.launch("omp", "do things", str(worktree), no_tty=True) == 0
    assert seen["cwd"] == str(worktree)
    assert seen["argv"] == ["omp", "-p", "--approval-mode=yolo", "--auto-approve", "do things"]


def test_launch_missing_worktree_is_actionable(tmp_path, monkeypatch):
    _fake_omp(monkeypatch)
    monkeypatch.setattr(backend.os, "execvp", lambda file, argv: None)
    with pytest.raises(HarnessError) as excinfo:
        backend.launch("omp", "p", str(tmp_path / "nope"), no_tty=False)
    assert "cannot cd into" in str(excinfo.value)


def test_command_argv_rejects_unknown_harness():
    with pytest.raises(HarnessError) as excinfo:
        backend.command_argv("claude", "p", no_tty=False)
    assert excinfo.value.exit_code == 2



def test_prompt_includes_push_target():
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/wt", branch="feat/x")
    assert "/wt" in p and "feat/x" in p
    assert "Never create or push a different branch" in p
    assert "At the end of the session: if there are code changes" in p  # end-of-session duty
    plain = backend.prompt_for_issue("T", "B", "o/r#22")
    assert "Only push and open a PR/MR for production-affecting changes" in plain
    r = backend.prompt_for_review("https://x/pull/1", worktree="/wt", branch="pr-1")
    assert "Never create or push a different branch" in r



def test_prompt_for_review_requires_posting_comments():
    """Review sessions must end by posting all PR/MR comments and inline notes."""
    r = backend.prompt_for_review("https://github.com/o/r/pull/5", worktree="/wt", branch="b")
    assert "At the end of the session, you MUST post all PR/MR review comments and inline notes" in r
    assert "Never create or push a different branch" in r  # push target still present
def test_prompt_for_fix_comments_rules():
    """Issue #32: fix prompt carries resolve/validate/commit-push rules."""
    p = backend.prompt_for_fix_comments("https://x/pull/1", worktree="/wt", branch="pr-1")
    assert "https://x/pull/1" in p
    assert "Resolve/close" in p
    assert "Status: RESOLVED" in p
    assert "natively marked resolved" in p
    assert "receiving-code-review skill" in p
    assert "validate" in p and "commit and push" in p
    assert "At the end of the session, report per comment" in p  # closing report
    assert "Never create or push a different branch" in p


def test_prompt_push_target_mentions_origin_branch():
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/w", branch="chore/a--b")
    assert "origin/chore/a--b" in p

def test_prompt_for_issue_lists_related_paths():
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/wt", branch="feat/x",
                                 related_paths=[("other", "/tmp/wt2")])
    assert "Related repo worktrees" in p
    assert "- other: /tmp/wt2" in p


def test_prompt_for_issue_no_related_section_when_empty():
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/wt", branch="feat/x")
    assert "Related repo" not in p


def test_prompt_plan_mode_constrains_harness():
    """Issue #88: plan mode forbids implementation, grills the user, writes
    spec+plan MDs, and stops after push+PR."""
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/wt",
                                 branch="feat/x", plan_mode=True)
    assert "Plan mode" in p
    assert "No implementation" in p
    assert "brainstorming" in p
    assert "spec" in p and "plan" in p
    assert "push" in p
    assert "PR/MR" in p
    assert "Stop" in p or "stop" in p


def test_prompt_non_plan_mode_points_at_spec_files():
    """Issue #88: without plan mode the prompt names existing spec/plan files."""
    p = backend.prompt_for_issue("T", "B", "o/r#22", worktree="/wt", branch="feat/x")
    assert "spec" in p and "plan" in p
    assert "No implementation" not in p


def test_omp_session_file_routes_via_resume(tmp_path):
    from workagent.acp_harness import OmpAcpHarness
    real = tmp_path / "x.jsonl"
    real.write_text('{"type":"session"}\n')
    assert OmpAcpHarness().session_file_flag(str(real)) == ["--resume", str(real)]
    assert OmpAcpHarness().session_file_flag("") == []
    # Fresh paths (missing or empty) route via --session-dir: omp v18+
    # rejects --resume on those (Session not found / holds no entries).
    missing = str(tmp_path / "missing.jsonl")
    assert OmpAcpHarness().session_file_flag(missing) == [
        "--session-dir", str(tmp_path)]
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    assert OmpAcpHarness().session_file_flag(str(empty)) == [
        "--session-dir", str(tmp_path)]
    argv = OmpAcpHarness().command_argv("prompt", True, ["--auto-approve"])
    assert "--session-file" not in argv


def test_omp_judge_argv_is_bare():
    """Bare judge sessions skip skills/tools/rules/context + cheap model."""
    from workagent.backend import Harness
    from workagent.acp_harness import OmpAcpHarness
    assert Harness.judge_argv == []
    flags = OmpAcpHarness.judge_argv
    for want in ("--smol", "--no-skills", "--no-tools", "--no-rules",
                 "--no-context-files", "--no-lsp", "--no-extensions", "--no-pty"):
        assert want in flags
    argv = OmpAcpHarness().command_argv("judge this", True, flags)
    assert argv[:4] == ["omp", "-p", "--approval-mode=yolo", "--auto-approve"]
    assert argv[-1] == "judge this" and "--smol" in argv


def test_preview_command_sources_env(tmp_path, monkeypatch, isolated_config):
    from workagent import cli_harness as _h
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = {"branch": "feat/x", "command": "start", "key": "k"}
    _h._run_harness("omp", "do things", str(worktree), str(worktree),
                    False, False, result, False)
    assert result["harness_command"].startswith("source ")


def test_harness_is_abstract():
    from workagent.backend import Harness
    with pytest.raises(TypeError):
        Harness()


def test_omp_harness_resume_and_bare_terminal_argv(tmp_path):
    from workagent.acp_harness import OmpAcpHarness
    h = OmpAcpHarness()
    assert h.binary == "omp"
    assert h.bare_terminal_argv("") == ["omp", "--approval-mode=yolo"]
    assert h.bare_terminal_argv("/tmp/scope") == ["omp", "--approval-mode=yolo", "--session-dir", "/tmp/scope"]
    missing = tmp_path / "fresh.jsonl"
    assert h.resume_argv(str(missing)) == ["omp", "--approval-mode=yolo", "--session-dir", str(tmp_path)]
    ready = tmp_path / "ready.jsonl"
    ready.write_text('{"type":"session"}\n', encoding="utf-8")
    assert h.resume_argv(str(ready)) == ["omp", "--approval-mode=yolo", "--resume", str(ready)]


def test_get_harness_default_and_explicit(isolated_config):
    from workagent.backend import get_harness
    from workagent.acp_harness import OmpAcpHarness
    from workagent.agy_harness import AgyAcpHarness
    # None or empty string resolves to default harness (agy)
    h = get_harness()
    assert isinstance(h, AgyAcpHarness)
    h2 = get_harness("")
    assert isinstance(h2, AgyAcpHarness)
    h3 = get_harness("omp")
    assert isinstance(h3, OmpAcpHarness)
    h4 = get_harness("agy")
    assert isinstance(h4, AgyAcpHarness)
    with pytest.raises(HarnessError) as excinfo:
        get_harness("unsupported_harness_xyz")
    assert excinfo.value.exit_code == 2
    assert "supported:" in str(excinfo.value)
    assert "v1 supports:" not in str(excinfo.value)


def test_check_binary(monkeypatch):
    from workagent.backend import check_binary
    monkeypatch.setattr(backend.shutil, "which", lambda name: "/bin/ok" if name == "ok" else None)
    check_binary("ok")
    with pytest.raises(HarnessError) as excinfo:
        check_binary("missing")
    assert "`missing` not found on PATH" in str(excinfo.value)


def test_omp_command_argv_omits_empty_prompt_in_tty():
    from workagent.acp_harness import OmpAcpHarness
    h = OmpAcpHarness()
    assert h.command_argv("", no_tty=False) == ["omp", "--approval-mode=yolo"]
    assert h.command_argv("do something", no_tty=False) == ["omp", "--approval-mode=yolo", "do something"]
    assert h.command_argv("", no_tty=False, extra_args=["--flag"]) == ["omp", "--approval-mode=yolo", "--flag"]


def test_omp_harness_extract_and_error_rate(tmp_path):
    from workagent.acp_harness import OmpAcpHarness
    h = OmpAcpHarness()
    p = tmp_path / "transcript.jsonl"
    p.write_text(
        '{"message": {"role": "assistant", "content": [{"type": "text", "text": "hello world"}]}}\n'
        '{"message": {"role": "toolResult", "content": [{"type": "text", "text": "error: failed"}]}}\n',
        encoding="utf-8",
    )
    assert h.extract_last_assistant_text(str(p)) == "hello world"
    rate = h.tool_error_rate(str(p))
    assert rate == (1, 1)


def test_omp_watch_transcript(tmp_path):
    from workagent.acp_harness import OmpAcpHarness
    import threading
    h = OmpAcpHarness()
    db = tmp_path / "state.db"
    res = {}
    stop = threading.Event()
    stop.set()
    # When stop is set immediately and no minted transcript, it returns without error
    h.watch_transcript(str(tmp_path), tmp_path, None, None, db, result=res, stop_event=stop)
    assert "session_file" not in res


def test_doctor_inspects_agy_guard_and_omp():
    from workagent import doctor
    res = doctor.check()
    assert "agy-guard" in res["tools"]
    assert "omp" in res["tools"]
    assert "git-wt" in res["tools"]


def test_agy_command_argv_attaches_prompt_with_equals():
    from workagent.agy_harness import AgyAcpHarness
    h = AgyAcpHarness()
    assert h.binary == "agy-guard"
    argv_headless = h.command_argv("fix --flag bug", no_tty=True, extra_args=["--model", "gemini-3.8-flash-low"])
    assert argv_headless == [
        "agy-guard",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--model", "gemini-3.8-flash-low",
        "-p=fix --flag bug",
    ]
    assert h.command_argv("hello", no_tty=False) == ["agy-guard", "--dangerously-skip-permissions", "-i=hello"]
    assert h.command_argv("", no_tty=False) == ["agy-guard", "--dangerously-skip-permissions"]
    assert h.bare_terminal_argv("/tmp/ignored") == ["agy-guard", "--dangerously-skip-permissions"]
    assert h.judge_argv == ["--model", "gemini-3.8-flash-low", "--disable-slash-commands"]


def test_agy_session_file_flag_and_resume_argv(tmp_path):
    import json
    from workagent.agy_harness import AgyAcpHarness
    h = AgyAcpHarness()
    fresh = tmp_path / "fresh.jsonl"
    assert h.session_file_flag(str(fresh)) == []
    assert h.resume_argv(str(fresh)) == ["agy-guard", "--dangerously-skip-permissions"]
    assert h.session_file_flag(str(fresh), metadata={"conversation_id": "cid-1"}) == ["--conversation", "cid-1"]
    fresh.write_text(json.dumps({"type": "agy_meta", "conversation_id": "cid-2"}) + "\n", encoding="utf-8")
    assert h.session_file_flag(str(fresh)) == ["--conversation", "cid-2"]
    assert h.resume_argv(str(fresh)) == ["agy-guard", "--dangerously-skip-permissions", "--conversation", "cid-2"]


def test_agy_launch_headless_streams_and_mirrors(tmp_path, monkeypatch):
    import io, json
    from workagent import agy_harness
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "agy-home"))
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    session = tmp_path / "runs" / "agy" / "feat" / "run.jsonl"
    session.parent.mkdir(parents=True)
    ndjson = "\n".join([
        json.dumps({"event": "init", "conversation_id": "conv-123"}),
        json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Working...\n"}}),
        json.dumps({"event": "result", "result": {"conversation_id": "conv-123", "status": "SUCCESS", "response": "Done\nJOB_DONE"}}),
    ]) + "\n"

    class FakeProc:
        stdout = io.StringIO(ndjson)
        returncode = 0
        pid = 12345
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: FakeProc())
    seen_cid = []
    code = agy_harness.AgyAcpHarness().launch(
        "do work", str(tmp_path), no_tty=True,
        session_file=str(session), on_conversation_id=seen_cid.append,
    )
    assert code == 0
    assert seen_cid == ["conv-123"]
    assert agy_harness.resolve_conversation_id(str(session)) == "conv-123"
    assert agy_harness.AgyAcpHarness().extract_last_assistant_text(str(session), max_chars=None) == "Done\nJOB_DONE"


def test_agy_adopt_from_workspace_disambiguates_concurrent(tmp_path, monkeypatch):
    import json, sqlite3
    from workagent import agy_harness
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    wt_a.mkdir(); wt_b.mkdir()
    for cid in ("cid-a", "cid-b"):
        log_dir = home / "brain" / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "transcript.jsonl").write_text(
            json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": f"reply-{cid}"}) + "\n",
            encoding="utf-8",
        )
    db = home / "conversation_summaries.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-a", json.dumps([wt_a.resolve().as_uri()]), "2026-11-16T22:00:05+00:00"))
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-b", json.dumps([wt_b.resolve().as_uri()]), "2026-11-16T22:00:06+00:00"))
    dest = tmp_path / "runs" / "out.jsonl"
    cid, path = agy_harness.adopt_from_workspace(str(wt_a), 1794866400.0, str(dest))
    assert cid == "cid-a"
    assert agy_harness.AgyAcpHarness().extract_last_assistant_text(path) == "reply-cid-a"


def test_agy_adopt_from_workspace_brain_fallback(tmp_path, monkeypatch):
    import json
    from workagent import agy_harness
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    log_dir = home / "brain" / "cid-fallback" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "fallback-reply"}) + "\n",
        encoding="utf-8",
    )
    dest = tmp_path / "runs" / "fallback.jsonl"
    cid, path = agy_harness.adopt_from_workspace(str(tmp_path / "wt"), 0.0, str(dest))
    assert cid == "cid-fallback"
    assert agy_harness.AgyAcpHarness().extract_last_assistant_text(path) == "fallback-reply"


def test_agy_watch_transcript_discovers_conversation(tmp_path, monkeypatch):
    import json, sqlite3, threading, time
    from workagent import agy_harness, store_sqlite as sq
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    wt = tmp_path / "wt-watch"
    wt.mkdir()

    state_db = tmp_path / "state.db"
    sq.init_db(state_db)
    run_id = sq.insert_run(state_db, "start", ["feat/watch"], harness_name="agy", metadata={})

    res = {}
    dest_session = tmp_path / "watch_session.jsonl"
    stop = threading.Event()

    def _mint():
        time.sleep(0.1)
        log_dir = home / "brain" / "cid-watch" / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "transcript.jsonl").write_text(
            json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "watch-discovered"}) + "\n",
            encoding="utf-8",
        )
        db_path = home / "conversation_summaries.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
            conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                         ("cid-watch", json.dumps([wt.resolve().as_uri()]), "2026-11-16T22:00:05+00:00"))

    t = threading.Thread(target=_mint)
    t.start()
    try:
        agy_harness.AgyAcpHarness().watch_transcript(
            str(wt), tmp_path, None, run_id, state_db, result=res,
            session_file=str(dest_session), launched_at=1794866400.0, stop_event=None,
        )
    finally:
        t.join()

    assert res.get("session_file") == str(dest_session)
    row = sq.get_run(state_db, run_id)
    assert row["metadata"].get("conversation_id") == "cid-watch"
    assert row["file_path"] == str(dest_session)


def test_agy_watch_transcript_cancelled_exits_promptly(tmp_path, monkeypatch):
    import threading, time
    from workagent import agy_harness
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    stop = threading.Event()
    stop.set()
    res = {}
    t0 = time.time()
    agy_harness.AgyAcpHarness().watch_transcript(
        str(tmp_path), tmp_path, None, None, tmp_path / "state.db",
        result=res, stop_event=stop,
    )
    assert time.time() - t0 < 1.0
    assert "session_file" not in res


def test_agy_launch_headless_timeout(tmp_path, monkeypatch):
    import io, time
    from workagent import agy_harness
    from workagent.errors import HarnessTimeout
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")

    class TimeoutProc:
        stdout = io.StringIO("")
        returncode = -15
        pid = 12345
        def wait(self, timeout=None):
            time.sleep(0.05)
            return -15

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: TimeoutProc())
    monkeypatch.setattr(agy_harness.os, "killpg", lambda pid, sig: None)

    with pytest.raises(HarnessTimeout):
        agy_harness.AgyAcpHarness().launch("timed out prompt", str(tmp_path), no_tty=True, timeout_s=0.01)


def test_agy_launch_headless_non_success_exit(tmp_path, monkeypatch):
    import io, json
    from workagent import agy_harness
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")

    ndjson = json.dumps({"event": "result", "result": {"status": "ERROR", "response": ""}}) + "\n"

    class ErrProc:
        stdout = io.StringIO(ndjson)
        returncode = 0
        pid = 12345
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: ErrProc())
    rc = agy_harness.AgyAcpHarness().launch("failing prompt", str(tmp_path), no_tty=True)
    assert rc == 1


def test_agy_launch_tty(tmp_path, monkeypatch):
    from workagent import agy_harness
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    seen = {}
    monkeypatch.setattr(agy_harness, "exec_tty", lambda argv, wd, env_file: seen.update(argv=argv, wd=wd) or 0)
    rc = agy_harness.AgyAcpHarness().launch("hello", str(tmp_path), no_tty=False)
    assert rc == 0
    assert seen["argv"] == ["agy-guard", "--dangerously-skip-permissions", "-i=hello"]
    assert seen["wd"] == str(tmp_path)


def test_launch_watch_shim_includes_harness_workdir_session_file(tmp_path, monkeypatch):
    seen_exec = {}

    def fake_exec_tty(argv, workdir, env_file=None):
        seen_exec["argv"] = argv
        seen_exec["workdir"] = workdir
        return 0

    monkeypatch.setattr(backend, "exec_tty", fake_exec_tty)
    watch = {"run_id": 42, "scope_dir": "/tmp/scope", "before": "/tmp/scope/b.jsonl"}
    backend.launch(
        harness="agy", prompt="hello", workdir=str(tmp_path), no_tty=False,
        watch=watch, session_file=str(tmp_path / "sess.jsonl")
    )
    argv = seen_exec["argv"]
    assert "watch-and-exec" in argv
    assert "--harness" in argv and argv[argv.index("--harness") + 1] == "agy"
    assert "--workdir" in argv and argv[argv.index("--workdir") + 1] == str(tmp_path)
    assert "--session-file" in argv and argv[argv.index("--session-file") + 1] == str(tmp_path / "sess.jsonl")
    assert "--scope-dir" in argv and argv[argv.index("--scope-dir") + 1] == "/tmp/scope"
    assert "--before" in argv and argv[argv.index("--before") + 1] == "/tmp/scope/b.jsonl"
    assert "--" in argv


def test_watch_and_exec_options_and_fork(monkeypatch, tmp_path):
    from workagent import cli_harness
    from typer.testing import CliRunner
    runner = CliRunner()

    execed = []
    monkeypatch.setattr(cli_harness.os, "fork", lambda: 1234)
    monkeypatch.setattr(cli_harness.os, "execvp", lambda f, a: execed.append((f, a)))
    r = runner.invoke(cli_harness.app, [
        "watch-and-exec", "--run-id", "12",
        "--harness", "omp",
        "--workdir", str(tmp_path),
        "--session-file", str(tmp_path / "sess.jsonl"),
        "--scope-dir", str(tmp_path),
        "--before", "",
        "--", "echo", "done"
    ])
    assert execed and execed[0] == ("echo", ["echo", "done"])


def test_watch_and_exec_child_delegates_to_watch_transcript(monkeypatch, tmp_path):
    from workagent import cli_harness
    from typer.testing import CliRunner
    runner = CliRunner()

    watched = []

    class FakeHarness:
        def watch_transcript(self, workdir, scope_dir, before, run_id, db,
                             result=None, session_file="", launched_at=0.0,
                             stop_event=None):
            watched.append({
                "workdir": workdir,
                "scope_dir": scope_dir,
                "before": before,
                "run_id": run_id,
                "session_file": session_file,
            })

    monkeypatch.setattr("workagent.backend.get_harness", lambda name: FakeHarness())
    monkeypatch.setattr(cli_harness.os, "fork", lambda: 0)  # child branch
    exited = []
    monkeypatch.setattr(cli_harness.os, "_exit", lambda code: exited.append(code))

    r = runner.invoke(cli_harness.app, [
        "watch-and-exec", "--run-id", "12",
        "--harness", "omp",
        "--workdir", str(tmp_path),
        "--session-file", str(tmp_path / "sess.jsonl"),
        "--scope-dir", str(tmp_path),
        "--before", "",
        "--", "echo", "done"
    ])
    assert exited == [0]
    assert len(watched) == 1
    assert watched[0]["run_id"] == 12
    assert watched[0]["workdir"] == str(tmp_path)
    assert watched[0]["session_file"] == str(tmp_path / "sess.jsonl")


def test_no_bare_harness_names_outside_owning_class():
    """Static AST guard: no bare "omp"/"agy" literals outside their owning
    harness class (`backend._load_harnesses` uses `ClassName.name` attrs)."""
    import ast
    from pathlib import Path

    src_dir = Path(__file__).resolve().parent.parent / "src" / "workagent"
    violations = []

    for py_file in sorted(src_dir.glob("*.py")):
        if py_file.name in ("doctor.py", "store.py"):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        class Visitor(ast.NodeVisitor):
            OWNERS = {"omp": {"acp_harness.py:OmpAcpHarness",
                              "backend.py:_load_harnesses"},
                      "agy": {"agy_harness.py:AgyAcpHarness",
                              "backend.py:_load_harnesses"}}

            def __init__(self):
                self.class_stack: list[str] = []
                self.func_stack: list[str] = []

            def visit_ClassDef(self, node):
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def visit_FunctionDef(self, node):
                self.func_stack.append(node.name)
                self.generic_visit(node)
                self.func_stack.pop()

            def visit_AsyncFunctionDef(self, node):
                self.func_stack.append(node.name)
                self.generic_visit(node)
                self.func_stack.pop()

            def visit_Constant(self, node):
                if isinstance(node.value, str) and node.value in ("omp", "agy"):
                    owner = f"{py_file.name}:{self.class_stack[-1]}" if self.class_stack else f"{py_file.name}:{self.func_stack[-1]}" if self.func_stack else f"{py_file.name}:module"
                    allowed = self.OWNERS[node.value]
                    if owner not in allowed and not (
                            self.class_stack and owner in allowed):
                        violations.append(
                            f"{py_file.name}:{node.lineno}: bare {node.value!r} "
                            f"in {owner or 'module'} (use ClassName.name)")

        Visitor().visit(tree)

    assert not violations, f"Bare harness names outside owning class: {violations}"


def test_agy_find_conversation_skips_brain_fallback_when_db_ok(tmp_path, monkeypatch):
    import json, sqlite3
    from workagent import agy_harness
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    wt_target = tmp_path / "wt-target"
    wt_other = tmp_path / "wt-other"
    wt_target.mkdir()
    wt_other.mkdir()
    log_dir = home / "brain" / "cid-other" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "transcript.jsonl").write_text('{"type":"PLANNER_RESPONSE","status":"DONE","content":"other"}\n')
    db = home / "conversation_summaries.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-other", json.dumps([wt_other.resolve().as_uri()]), "2026-11-16T22:00:05+00:00"))
    # DB is healthy and has 0 matches for wt_target -> must NOT fall back to brain/
    assert agy_harness.find_conversation_for_workspace(str(wt_target), 0.0) == ""


def test_agy_find_conversation_empty_workdir_returns_empty(tmp_path, monkeypatch):
    import json, sqlite3
    from workagent import agy_harness
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    log_dir = home / "brain" / "cid-any" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "transcript.jsonl").write_text('{"type":"PLANNER_RESPONSE","status":"DONE","content":"any"}\n')
    db = home / "conversation_summaries.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-any", json.dumps([(tmp_path / "wt").resolve().as_uri()]), "2026-11-16T22:00:05+00:00"))
    assert agy_harness.find_conversation_for_workspace("", 0.0) == ""


def test_agy_launch_headless_writes_stream_to_stderr_not_stdout(tmp_path, monkeypatch, capsys):
    import io, json
    from workagent import agy_harness
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "agy-home"))
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    ndjson_deltas = "\n".join([
        "plain non-json line",
        json.dumps({"event": "init", "conversation_id": "conv-stream"}),
        json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "delta-out\n"}}),
        json.dumps({"event": "result", "result": {"conversation_id": "conv-stream", "status": "SUCCESS", "response": "delta-out"}}),
    ]) + "\n"

    class FakeProc:
        def __init__(self, payload):
            self.stdout = io.StringIO(payload)
            self.returncode = 0
            self.pid = 111
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: FakeProc(ndjson_deltas))
    assert agy_harness.AgyAcpHarness().launch("p", str(tmp_path), no_tty=True) == 0
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "plain non-json line" in cap.err
    assert "delta-out" in cap.err

    # Also verify result.response fallback when no text_delta was seen goes to stderr only
    ndjson_resp = json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "final-resp"}}) + "\n"
    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: FakeProc(ndjson_resp))
    assert agy_harness.AgyAcpHarness().launch("p", str(tmp_path), no_tty=True) == 0
    cap2 = capsys.readouterr()
    assert cap2.out == ""
    assert "final-resp" in cap2.err


def test_agy_headless_skips_transcript_watcher(tmp_path, monkeypatch, isolated_config):
    from workagent import cli_harness as _h, cli_validate as _cv
    from workagent.agy_harness import AgyAcpHarness
    from workagent.acp_harness import OmpAcpHarness
    assert AgyAcpHarness.watches_headless is False
    assert OmpAcpHarness.watches_headless is True

    worktree = tmp_path / "wt"
    worktree.mkdir()
    watcher_calls = []
    monkeypatch.setattr(_h, "_watch_minted_transcript", lambda *a, **k: watcher_calls.append((a, k)))
    monkeypatch.setattr(_cv, "validate_headless",
                        lambda **kw: {"verdict": "JOB_DONE", "attempts": 1, "runs": []})
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    from workagent import store as _store
    _store.record_link("k-agy", {"worktree": str(worktree), "branch": "feat/x", "repo": str(worktree)})
    result = {"branch": "feat/x", "command": "start", "key": "k-agy"}
    _h._run_harness("agy", "do things", str(worktree), str(worktree),
                    True, True, result, False, run_key="k-agy")
    assert watcher_calls == []


def test_cli_terminal_watch_passes_before_transcript(tmp_path, monkeypatch, isolated_config):
    from workagent import cli_harness as _h, store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    sess = sq.session_file_path("pinned", "omp", branch="feat/x", session_type="start")
    stale = sess.parent / "stale.jsonl"
    stale.write_text('{"type":"session"}\n', encoding="utf-8")
    spawned = {}
    monkeypatch.setattr(_h, "spawn_in_terminal",
                        lambda cmd, cwd="": (spawned.update(cmd=cmd, cwd=cwd), 9999)[1])
    result = {"branch": "feat/x", "command": "start", "key": "k-term"}
    _h._run_harness("omp", "do things", str(worktree), str(worktree),
                    False, False, result, False, run_key="k-term",
                    session_file=str(sess), terminal=True)
    assert "--before" in spawned["cmd"]
    assert str(stale) in spawned["cmd"]


def test_agy_watch_transcript_keeps_polling_until_brain_file_exists(tmp_path, monkeypatch):
    import json, sqlite3, threading, time
    from workagent import agy_harness, store_sqlite as sq

    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    wt = tmp_path / "wt-delay"
    wt.mkdir()

    state_db = tmp_path / "state.db"
    sq.init_db(state_db)
    run_id = sq.insert_run(state_db, "start", ["feat/delay"], harness_name="agy", metadata={})
    dest_session = tmp_path / "delayed_session.jsonl"
    res = {}
    stop = threading.Event()

    # Phase 1: DB already has cid-delay, but brain/<cid>/transcript.jsonl does not exist yet.
    sum_db = home / "conversation_summaries.db"
    with sqlite3.connect(sum_db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-delay", json.dumps([wt.resolve().as_uri()]), "2026-11-16T22:00:05+00:00"))

    # Speed up polling in test by tightening wait
    orig_sleep = agy_harness.time.sleep
    monkeypatch.setattr(agy_harness.time, "sleep", lambda s: orig_sleep(min(s, 0.02)))

    def _delayed_brain():
        for _ in range(50):
            row = sq.get_run(state_db, run_id)
            if (row or {}).get("metadata", {}).get("conversation_id") == "cid-delay":
                break
            orig_sleep(0.02)
        # Before brain transcript is written, target_path MUST NOT contain a 1-line stub.
        assert not dest_session.exists()
        log_dir = home / "brain" / "cid-delay" / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "transcript.jsonl").write_text(
            json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "delayed-ready"}) + "\n",
            encoding="utf-8",
        )

    t = threading.Thread(target=_delayed_brain)
    t.start()
    try:
        agy_harness.AgyAcpHarness().watch_transcript(
            str(wt), tmp_path, None, run_id, state_db, result=res,
            session_file=str(dest_session), launched_at=1794866400.0, stop_event=None,
        )
    finally:
        t.join(timeout=5)

    assert res.get("session_file") == str(dest_session)
    txt = dest_session.read_text(encoding="utf-8")
    assert "cid-delay" in txt
    assert "delayed-ready" in txt


def test_agy_watch_transcript_matches_resumed_conversation(tmp_path, monkeypatch):
    import json, sqlite3, threading
    from workagent import agy_harness, store_sqlite as sq

    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    wt = tmp_path / "wt-resume"
    wt.mkdir()

    # Pre-existing brain directory from prior run
    log_dir = home / "brain" / "cid-resume" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "resumed-turn"}) + "\n",
        encoding="utf-8",
    )
    sum_db = home / "conversation_summaries.db"
    with sqlite3.connect(sum_db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-resume", json.dumps([wt.resolve().as_uri()]), "2099-01-01T00:00:00+00:00"))

    dest_session = tmp_path / "resume_session.jsonl"
    dest_session.write_text(json.dumps({"type": "agy_meta", "conversation_id": "cid-resume"}) + "\n", encoding="utf-8")
    state_db = tmp_path / "state.db"
    sq.init_db(state_db)
    run_id = sq.insert_run(state_db, "start", ["feat/resume"], harness_name="agy",
                           file_path=str(dest_session), metadata={"conversation_id": "cid-resume"})
    res = {}
    orig_sleep = agy_harness.time.sleep
    monkeypatch.setattr(agy_harness.time, "sleep", lambda s: orig_sleep(min(s, 0.02)))
    stop = threading.Event()
    agy_harness.AgyAcpHarness().watch_transcript(
        str(wt), tmp_path, None, run_id, state_db, result=res,
        session_file=str(dest_session), launched_at=1794866400.0, stop_event=None,
    )
    assert res.get("session_file") == str(dest_session)
    assert "resumed-turn" in dest_session.read_text(encoding="utf-8")


def test_agy_launch_timeout_skips_sigkill_when_reaped_and_kills_when_alive(tmp_path, monkeypatch):
    import io, signal, threading, time
    from workagent import agy_harness
    from workagent.errors import HarnessTimeout

    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")

    # Case 1: process exits promptly on SIGTERM (poll() == 0) -> no SIGKILL
    sent_sigs: list[int] = []

    class ReapedProc:
        stdout = io.StringIO("")
        returncode = -15
        pid = 22222
        _reaped = False

        def poll(self):
            return 0 if self._reaped else None

        def wait(self, timeout=None):
            time.sleep(0.05)
            self._reaped = True
            return -15

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: ReapedProc())
    monkeypatch.setattr(agy_harness.os, "killpg", lambda pid, sig: sent_sigs.append(sig))
    t0 = time.time()
    with pytest.raises(HarnessTimeout):
        agy_harness.AgyAcpHarness().launch("p", str(tmp_path), no_tty=True, timeout_s=0.01)
    assert signal.SIGTERM in sent_sigs
    assert signal.SIGKILL not in sent_sigs
    assert time.time() - t0 < 1.0  # did not sleep full 2.0s after reap

    # Case 2: process remains alive (poll() is None) -> SIGKILL escalated
    sent_sigs2: list[int] = []
    orig_wait = threading.Event.wait

    class StubbornProc:
        stdout = io.StringIO("")
        returncode = -9
        pid = 33333

        def poll(self):
            return None

        def wait(self, timeout=None):
            time.sleep(0.08)
            return -9

    def _fast_wait(self, timeout=None):
        if timeout == 2.0:
            return False
        return orig_wait(self, timeout=timeout)

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: StubbornProc())
    monkeypatch.setattr(agy_harness.os, "killpg", lambda pid, sig: sent_sigs2.append(sig))
    monkeypatch.setattr(threading.Event, "wait", _fast_wait)
    with pytest.raises(HarnessTimeout):
        agy_harness.AgyAcpHarness().launch("p", str(tmp_path), no_tty=True, timeout_s=0.01)
    assert sent_sigs2 == [signal.SIGTERM, signal.SIGKILL]


def test_agy_launch_non_response_step_update_does_not_suppress_result(tmp_path, monkeypatch, capsys):
    import io, json
    from workagent import agy_harness
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "agy-home"))
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    ndjson = "\n".join([
        json.dumps({"event": "init", "conversation_id": "conv-nr"}),
        json.dumps({"event": "step_update", "step_update": {"step_type": "thought", "text_delta": "thinking...\n"}}),
        json.dumps({"event": "result", "result": {"conversation_id": "conv-nr", "status": "SUCCESS", "response": "final-visible-answer"}}),
    ]) + "\n"

    class FakeProc:
        stdout = io.StringIO(ndjson)
        returncode = 0
        pid = 44444
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: FakeProc())
    rc = agy_harness.AgyAcpHarness().launch("p", str(tmp_path), no_tty=True)
    assert rc == 0
    err = capsys.readouterr().err
    assert "thinking..." not in err
    assert "final-visible-answer" in err


def test_run_harness_records_harness_name_on_validation_child_rows(tmp_path, monkeypatch, isolated_config):
    from workagent import cli_harness as _h, cli_validate as _cv, store_sqlite as sq, store as _store

    db = sq.db_path()
    sq.init_db(db)
    wt = tmp_path / "wt-val"
    wt.mkdir()
    _store.record_link("k-val", {"worktree": str(wt), "branch": "feat/val", "repo": str(wt)})
    monkeypatch.setattr(
        _cv, "validate_headless",
        lambda **kw: {
            "verdict": "JOB_DONE",
            "attempts": 1,
            "runs": [{"exit_code": 0, "output": "ok", "trace": {"verdict": "JOB_DONE"}}],
        },
    )
    result = {"branch": "feat/val", "command": "start", "key": "k-val"}
    _h._run_harness("omp", "do things", str(wt), str(wt),
                    True, True, result, False, run_key="k-val")
    rows = sq.list_runs(db, limit=10, include_deleted_worktrees=True)
    assert len(rows) >= 2
    assert all(r["harness_name"] == "omp" for r in rows)


def test_agy_launch_error_status_respects_produced_response(tmp_path, monkeypatch):
    import io, json
    from workagent import agy_harness

    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")

    def _run(events):
        ndjson = "\n".join(json.dumps(e) for e in events) + "\n"

        class FakeProc:
            stdout = io.StringIO(ndjson)
            returncode = 0
            pid = 55555
            def wait(self, timeout=None): return 0

        monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: FakeProc())
        return agy_harness.AgyAcpHarness().launch("p", str(tmp_path), no_tty=True)

    # Prior error step + recovered response with rc==0 -> exit 0
    assert _run([
        {"event": "init", "conversation_id": "c-err-ok"},
        {"event": "step_update", "step_update": {"step_type": "error_message"}},
        {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "recovered"}},
        {"event": "result", "result": {"status": "ERROR", "response": "recovered"}},
    ]) == 0

    # Result-only response with status=ERROR and rc==0 -> exit 0
    assert _run([
        {"event": "init", "conversation_id": "c-err-res"},
        {"event": "result", "result": {"status": "ERROR", "response": "answer"}},
    ]) == 0

    # status=ERROR with no response and rc==0 -> exit 1
    assert _run([
        {"event": "init", "conversation_id": "c-err-empty"},
        {"event": "result", "result": {"status": "ERROR", "response": ""}},
    ]) == 1


def test_agy_watch_transcript_re_mirrors_until_stopped(tmp_path, monkeypatch):
    import json, sqlite3, threading
    from workagent import agy_harness, store_sqlite as sq

    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    wt = tmp_path / "wt-stream"
    wt.mkdir()

    log_dir = home / "brain" / "cid-stream" / ".system_generated" / "logs"
    brain_file = log_dir / "transcript.jsonl"
    sum_db = home / "conversation_summaries.db"
    with sqlite3.connect(sum_db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-stream", json.dumps([wt.resolve().as_uri()]), "2099-01-01T00:00:00+00:00"))

    dest_session = tmp_path / "stream_session.jsonl"
    state_db = tmp_path / "state.db"
    sq.init_db(state_db)
    run_id = sq.insert_run(state_db, "start", ["feat/stream"], harness_name="agy", metadata={})
    res = {}
    stop = threading.Event()
    orig_wait = stop.wait
    monkeypatch.setattr(stop, "wait", lambda s: orig_wait(min(s, 0.02)))

    t = threading.Thread(
        target=agy_harness.AgyAcpHarness().watch_transcript,
        args=(str(wt), tmp_path, None, run_id, state_db),
        kwargs={"result": res, "session_file": str(dest_session),
                "launched_at": 1794866400.0, "stop_event": stop},
    )
    t.start()
    try:
        orig_sleep = agy_harness.time.sleep
        orig_sleep(0.05)
        log_dir.mkdir(parents=True)
        brain_file.write_text(
            json.dumps({"type": "USER_INPUT", "status": "DONE", "content": "hello"}) + "\n",
            encoding="utf-8",
        )
        for _ in range(100):
            if dest_session.is_file() and "hello" in dest_session.read_text(encoding="utf-8"):
                break
            orig_sleep(0.02)
        assert "hello" in dest_session.read_text(encoding="utf-8")
        assert t.is_alive()

        # Append assistant turn after initial mirror
        with brain_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": "assistant-reply"}) + "\n")

        for _ in range(100):
            if "assistant-reply" in dest_session.read_text(encoding="utf-8"):
                break
            orig_sleep(0.02)
        assert "assistant-reply" in dest_session.read_text(encoding="utf-8")
    finally:
        stop.set()
        t.join(timeout=5)
    assert not t.is_alive()









def test_exec_tty_drops_service_no_color(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("WORKAGENT_SERVICE", "1")
    monkeypatch.setattr(backend, "cd_worktree", lambda wd: None)
    monkeypatch.setattr(backend.os, "execvp", lambda a, argv: None)
    backend.exec_tty(["x"], str(tmp_path))
    assert "NO_COLOR" not in backend.os.environ
    assert "WORKAGENT_SERVICE" not in backend.os.environ


def test_exec_tty_keeps_user_no_color(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("WORKAGENT_SERVICE", raising=False)
    monkeypatch.setattr(backend, "cd_worktree", lambda wd: None)
    monkeypatch.setattr(backend.os, "execvp", lambda a, argv: None)
    backend.exec_tty(["x"], str(tmp_path))
    assert backend.os.environ["NO_COLOR"] == "1"


def test_exec_tty_keeps_env_file_no_color(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("WORKAGENT_SERVICE", "1")
    monkeypatch.setattr(backend, "cd_worktree", lambda wd: None)
    monkeypatch.setattr(backend.os, "execvp", lambda a, argv: None)
    from workagent import env as _env
    monkeypatch.setattr(_env, "launch_env", lambda f: {"NO_COLOR": "1"})
    backend.exec_tty(["x"], str(tmp_path), env_file="e.sh")
    assert backend.os.environ["NO_COLOR"] == "1"


def _spawn(monkeypatch, service):
    import subprocess as sp
    from workagent import cli_harness as ch
    seen = {}
    class P:
        pid = 1
    monkeypatch.setenv("NO_COLOR", "1")
    if service:
        monkeypatch.setenv("WORKAGENT_SERVICE", "1")
    else:
        monkeypatch.delenv("WORKAGENT_SERVICE", raising=False)
    monkeypatch.setenv("TERMINAL", "xterm")
    monkeypatch.setattr(ch.shutil, "which", lambda t: "/bin/" + t)
    monkeypatch.setattr(sp, "Popen", lambda argv, **kw: seen.update(argv=argv, **kw) or P())
    ch.spawn_in_terminal("echo hi", "/tmp")
    return seen


def test_spawn_in_terminal_drops_service_no_color(monkeypatch):
    seen = _spawn(monkeypatch, True)
    assert "NO_COLOR" not in seen["env"]
    assert "WORKAGENT_SERVICE" not in seen["env"]
    assert "unset NO_COLOR WORKAGENT_SERVICE" in seen["argv"][-1]


def test_spawn_in_terminal_keeps_user_no_color(monkeypatch):
    seen = _spawn(monkeypatch, False)
    assert "env" not in seen
    assert "unset NO_COLOR" not in seen["argv"][-1]


def test_is_effort_unsupported_error():
    assert backend.is_effort_unsupported_error(
        'error: invalid model selection (--model "gemini-pro-orchestrator-neon" --effort "medium"): '
        '--effort is not supported for model "gemini-pro-orchestrator-neon"'
    )
    assert backend.is_effort_unsupported_error("--thinking is not supported for model foo")
    assert backend.is_effort_unsupported_error("Model foo does not support thought_level")
    assert backend.is_effort_unsupported_error("reasoning effort unsupported by this model")
    assert not backend.is_effort_unsupported_error("")
    assert not backend.is_effort_unsupported_error("error: connection refused")
    assert not backend.is_effort_unsupported_error('error: unknown model "foo"')


def test_agy_launch_headless_retries_without_effort_when_unsupported(tmp_path, monkeypatch, capsys):
    import io, json
    from workagent import agy_harness
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "agy-home"))
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr("workagent.env.merged_env", lambda *a, **k: dict(os.environ))
    session = tmp_path / "runs" / "agy" / "feat" / "run.jsonl"
    session.parent.mkdir(parents=True)
    calls: list[list[str]] = []

    class FailProc:
        stdout = io.StringIO("")
        stderr = io.StringIO(
            'error: invalid model selection (--model "gemini-pro-orchestrator-neon" --effort "medium"): '
            '--effort is not supported for model "gemini-pro-orchestrator-neon"\n'
        )
        returncode = 1
        pid = 12345
        def wait(self, timeout=None): return 1

    ok_ndjson = "\n".join([
        json.dumps({"event": "init", "conversation_id": "conv-retry"}),
        json.dumps({"event": "result", "result": {"conversation_id": "conv-retry", "status": "SUCCESS", "response": "Done\nJOB_DONE"}}),
    ]) + "\n"

    class OkProc:
        stdout = io.StringIO(ok_ndjson)
        stderr = None
        returncode = 0
        pid = 12346
        def wait(self, timeout=None): return 0

    def fake_popen(argv, **kw):
        calls.append(list(argv))
        return FailProc() if len(calls) == 1 else OkProc()

    monkeypatch.setattr(agy_harness.subprocess, "Popen", fake_popen)
    code = agy_harness.AgyAcpHarness().launch(
        "do work", str(tmp_path), no_tty=True,
        session_file=str(session),
        model="gemini-pro-orchestrator-neon", effort="medium",
    )
    assert code == 0
    assert len(calls) == 2
    assert "--effort" in calls[0] and "medium" in calls[0]
    assert "--effort" not in calls[1]
    assert "--model" in calls[1] and "gemini-pro-orchestrator-neon" in calls[1]
    err = capsys.readouterr().err
    assert "--effort is not supported" in err
    assert "retrying without --effort" in err
    assert agy_harness.resolve_conversation_id(str(session)) == "conv-retry"


def test_agy_launch_headless_does_not_retry_on_unrelated_error(tmp_path, monkeypatch):
    import io
    from workagent import agy_harness
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr("workagent.env.merged_env", lambda *a, **k: dict(os.environ))
    calls: list[list[str]] = []

    class FailProc:
        stdout = io.StringIO("")
        stderr = io.StringIO("error: network timeout\n")
        returncode = 1
        pid = 12345
        def wait(self, timeout=None): return 1

    def fake_popen(argv, **kw):
        calls.append(list(argv))
        return FailProc()

    monkeypatch.setattr(agy_harness.subprocess, "Popen", fake_popen)
    code = agy_harness.AgyAcpHarness().launch(
        "do work", str(tmp_path), no_tty=True,
        model="gemini-pro-orchestrator-neon", effort="medium",
    )
    assert code == 1
    assert len(calls) == 1


def test_omp_launch_headless_retries_without_effort_when_unsupported(tmp_path, monkeypatch, capsys):
    import sys
    from workagent.acp_harness import OmpAcpHarness
    fake_omp = tmp_path / "fake_omp.py"
    log = tmp_path / "calls.log"
    fake_omp.write_text(
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if any(a.startswith('--thinking=') for a in sys.argv[1:]):\n"
        "    sys.stderr.write('error: --thinking is not supported for model foo\\n')\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    h = OmpAcpHarness()
    orig_cmd = h.command_argv
    monkeypatch.setattr(
        h, "command_argv",
        lambda prompt, no_tty, extra_args=None, model=None, effort=None: [
            sys.executable, str(fake_omp), *orig_cmd(prompt, no_tty, extra_args, model, effort)[1:]
        ],
    )
    rc = h.launch("do work", str(tmp_path), no_tty=True, model="foo", effort="medium")
    assert rc == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "--thinking=medium" in lines[0]
    assert "--thinking=" not in lines[1]
    assert "--model=foo" in lines[1]
    err = capsys.readouterr().err
    assert "retrying without --effort" in err


def test_omp_launch_headless_does_not_retry_on_unrelated_error(tmp_path, monkeypatch):
    import sys
    from workagent.acp_harness import OmpAcpHarness
    fake_omp = tmp_path / "fake_omp_fail.py"
    log = tmp_path / "calls.log"
    fake_omp.write_text(
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.stderr.write('error: rate limit exceeded\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    h = OmpAcpHarness()
    orig_cmd = h.command_argv
    monkeypatch.setattr(
        h, "command_argv",
        lambda prompt, no_tty, extra_args=None, model=None, effort=None: [
            sys.executable, str(fake_omp), *orig_cmd(prompt, no_tty, extra_args, model, effort)[1:]
        ],
    )
    rc = h.launch("do work", str(tmp_path), no_tty=True, model="foo", effort="medium")
    assert rc == 1
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_run_harness_omits_effort_from_cached_model_capabilities(tmp_path, isolated_config, capsys):
    from workagent import cli_harness as _h, store
    worktree = tmp_path / "wt"
    worktree.mkdir()
    store.save_harness_options_cache("agy", {
        "model_efforts": {
            "gemini-pro-orchestrator-neon": [],
            "claude-opus-5-5": [{"id": "low", "name": "Low"}, {"id": "high", "name": "High"}],
        },
    })

    res_unsupported = {"branch": "feat/x", "command": "start", "key": "k"}
    _h._run_harness("agy", "do things", str(worktree), str(worktree),
                    False, False, res_unsupported, False,
                    model="gemini-pro-orchestrator-neon", effort="medium")
    assert "--effort" not in res_unsupported["harness_command"]
    assert "--model gemini-pro-orchestrator-neon" in res_unsupported["harness_command"]
    assert "omitting --effort" in capsys.readouterr().err

    res_bad_level = {"branch": "feat/x", "command": "start", "key": "k"}
    _h._run_harness("agy", "do things", str(worktree), str(worktree),
                    False, False, res_bad_level, False,
                    model="claude-opus-5-5", effort="xhigh")
    assert "--effort" not in res_bad_level["harness_command"]
    assert "supported: low, high" in capsys.readouterr().err

    res_ok = {"branch": "feat/x", "command": "start", "key": "k"}
    _h._run_harness("agy", "do things", str(worktree), str(worktree),
                    False, False, res_ok, False,
                    model="claude-opus-5-5", effort="high")
    assert "--effort high" in res_ok["harness_command"]

    res_unknown = {"branch": "feat/x", "command": "start", "key": "k"}
    _h._run_harness("agy", "do things", str(worktree), str(worktree),
                    False, False, res_unknown, False,
                    model="future-model", effort="medium")
    assert "--effort medium" in res_unknown["harness_command"]

    # Empty harness_name resolves to default harness ("agy") and still hits the "agy" cache.
    res_alias = {"branch": "feat/x", "command": "start", "key": "k"}
    _h._run_harness("", "do things", str(worktree), str(worktree),
                    False, False, res_alias, False,
                    model="gemini-pro-orchestrator-neon", effort="medium")
    assert "--effort" not in res_alias["harness_command"]
    assert "omitting --effort" in capsys.readouterr().err


def test_warn_effort_retry_quotes_argv(capsys):
    backend.warn_effort_retry("m1", "high", ["agy", "-p", "hello world\nline2"])
    err = capsys.readouterr().err
    assert "$ agy -p 'hello world\nline2'" in err
