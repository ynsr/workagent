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
    assert excinfo.value.argv == ["omp", "do things"]


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
    assert seen["argv"] == ["omp", "-p", "--auto-approve", "do things"]


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
    from workagent.backend import OmpHarness
    real = tmp_path / "x.jsonl"
    real.write_text('{"type":"session"}\n')
    assert OmpHarness().session_file_flag(str(real)) == ["--resume", str(real)]
    assert OmpHarness().session_file_flag("") == []
    # Fresh paths (missing or empty) route via --session-dir: omp v18+
    # rejects --resume on those (Session not found / holds no entries).
    missing = str(tmp_path / "missing.jsonl")
    assert OmpHarness().session_file_flag(missing) == [
        "--session-dir", str(tmp_path)]
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    assert OmpHarness().session_file_flag(str(empty)) == [
        "--session-dir", str(tmp_path)]
    argv = OmpHarness().command_argv("prompt", True, ["--auto-approve"])
    assert "--session-file" not in argv


def test_omp_judge_argv_is_bare():
    """Bare judge sessions skip skills/tools/rules/context + cheap model."""
    from workagent.backend import Harness, OmpHarness
    assert Harness.judge_argv == []
    flags = OmpHarness.judge_argv
    for want in ("--smol", "--no-skills", "--no-tools", "--no-rules",
                 "--no-context-files", "--no-lsp", "--no-extensions", "--no-pty"):
        assert want in flags
    argv = OmpHarness().command_argv("judge this", True, flags)
    assert argv[:3] == ["omp", "-p", "--auto-approve"]
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
    from workagent.backend import OmpHarness
    h = OmpHarness()
    assert h.binary == "omp"
    assert h.bare_terminal_argv("") == ["omp"]
    assert h.bare_terminal_argv("/tmp/scope") == ["omp", "--session-dir", "/tmp/scope"]
    missing = tmp_path / "fresh.jsonl"
    assert h.resume_argv(str(missing)) == ["omp", "--session-dir", str(tmp_path)]
    ready = tmp_path / "ready.jsonl"
    ready.write_text('{"type":"session"}\n', encoding="utf-8")
    assert h.resume_argv(str(ready)) == ["omp", "--resume", str(ready)]


def test_get_harness_default_and_explicit(isolated_config):
    from workagent.backend import get_harness, OmpHarness
    from workagent.agy_harness import AgyHarness
    # None or empty string resolves to default harness (agy)
    h = get_harness()
    assert isinstance(h, AgyHarness)
    h2 = get_harness("")
    assert isinstance(h2, AgyHarness)
    h3 = get_harness("omp")
    assert isinstance(h3, OmpHarness)
    h4 = get_harness("agy")
    assert isinstance(h4, AgyHarness)
    with pytest.raises(HarnessError) as excinfo:
        get_harness("unsupported_harness_xyz")
    assert excinfo.value.exit_code == 2


def test_check_binary(monkeypatch):
    from workagent.backend import check_binary
    monkeypatch.setattr(backend.shutil, "which", lambda name: "/bin/ok" if name == "ok" else None)
    check_binary("ok")
    with pytest.raises(HarnessError) as excinfo:
        check_binary("missing")
    assert "`missing` not found on PATH" in str(excinfo.value)


def test_omp_command_argv_omits_empty_prompt_in_tty():
    from workagent.backend import OmpHarness
    h = OmpHarness()
    assert h.command_argv("", no_tty=False) == ["omp"]
    assert h.command_argv("do something", no_tty=False) == ["omp", "do something"]
    assert h.command_argv("", no_tty=False, extra_args=["--flag"]) == ["omp", "--flag"]


def test_omp_harness_extract_and_error_rate(tmp_path):
    from workagent.backend import OmpHarness
    h = OmpHarness()
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
    from workagent.backend import OmpHarness
    import threading
    h = OmpHarness()
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
    from workagent.agy_harness import AgyHarness
    h = AgyHarness()
    assert h.binary == "agy-guard"
    argv_headless = h.command_argv("fix --flag bug", no_tty=True, extra_args=["--model", "gemini-3.8-flash-low"])
    assert argv_headless == [
        "agy-guard",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--model", "gemini-3.8-flash-low",
        "-p=fix --flag bug",
    ]
    assert h.command_argv("hello", no_tty=False) == ["agy-guard", "-i=hello"]
    assert h.command_argv("", no_tty=False) == ["agy-guard"]
    assert h.bare_terminal_argv("/tmp/ignored") == ["agy-guard"]
    assert h.judge_argv == ["--model", "gemini-3.8-flash-low", "--disable-slash-commands", "--mode", "plan"]


def test_agy_session_file_flag_and_resume_argv(tmp_path):
    import json
    from workagent.agy_harness import AgyHarness
    h = AgyHarness()
    fresh = tmp_path / "fresh.jsonl"
    assert h.session_file_flag(str(fresh)) == []
    assert h.resume_argv(str(fresh)) == ["agy-guard"]
    assert h.session_file_flag(str(fresh), metadata={"conversation_id": "cid-1"}) == ["--conversation", "cid-1"]
    fresh.write_text(json.dumps({"type": "agy_meta", "conversation_id": "cid-2"}) + "\n", encoding="utf-8")
    assert h.session_file_flag(str(fresh)) == ["--conversation", "cid-2"]
    assert h.resume_argv(str(fresh)) == ["agy-guard", "--conversation", "cid-2"]


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
    code = agy_harness.AgyHarness().launch(
        "do work", str(tmp_path), no_tty=True,
        session_file=str(session), on_conversation_id=seen_cid.append,
    )
    assert code == 0
    assert seen_cid == ["conv-123"]
    assert agy_harness.resolve_conversation_id(str(session)) == "conv-123"
    assert agy_harness.AgyHarness().extract_last_assistant_text(str(session), max_chars=None) == "Done\nJOB_DONE"


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
    assert agy_harness.AgyHarness().extract_last_assistant_text(path) == "reply-cid-a"


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
    assert agy_harness.AgyHarness().extract_last_assistant_text(path) == "fallback-reply"


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
        agy_harness.AgyHarness().watch_transcript(
            str(wt), tmp_path, None, run_id, state_db, result=res,
            session_file=str(dest_session), launched_at=1794866400.0, stop_event=stop,
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
    agy_harness.AgyHarness().watch_transcript(
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
        agy_harness.AgyHarness().launch("timed out prompt", str(tmp_path), no_tty=True, timeout_s=0.01)


def test_agy_launch_headless_non_success_exit(tmp_path, monkeypatch):
    import io, json
    from workagent import agy_harness
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")

    ndjson = json.dumps({"event": "result", "result": {"status": "ERROR", "response": "failed"}}) + "\n"

    class ErrProc:
        stdout = io.StringIO(ndjson)
        returncode = 0
        pid = 12345
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: ErrProc())
    rc = agy_harness.AgyHarness().launch("failing prompt", str(tmp_path), no_tty=True)
    assert rc == 1


def test_agy_launch_tty(tmp_path, monkeypatch):
    from workagent import agy_harness
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    seen = {}
    monkeypatch.setattr(agy_harness, "exec_tty", lambda argv, wd, env_file: seen.update(argv=argv, wd=wd) or 0)
    rc = agy_harness.AgyHarness().launch("hello", str(tmp_path), no_tty=False)
    assert rc == 0
    assert seen["argv"] == ["agy-guard", "-i=hello"]
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






