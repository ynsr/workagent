"""parse_verdict: tolerant last-line token parse + priority."""
from workagent import cli_validate as cv


def test_job_done_exact():
    token, mode = cv.parse_verdict("did the work\nJOB_DONE")
    assert (token, mode) == ("JOB_DONE", "exact")


def test_tolerant_quotes_and_case():
    token, mode = cv.parse_verdict('done!\n"job_done"')
    assert token == "JOB_DONE" and mode == "tolerant"


def test_fenced_token():
    token, _ = cv.parse_verdict("finishing\n```\nINCOMPLETE_OR_FAILED_JOB\n```")
    assert token == "INCOMPLETE_OR_FAILED_JOB"


def test_mid_transcript_token_ignored():
    token, mode = cv.parse_verdict("JOB_DONE\nthen I broke it again")
    assert (token, mode) == (None, "none")


def test_priority_waiting_over_done():
    assert cv.pick_verdict(["JOB_DONE", "WAITING_USER_RESPONSE"]) == "WAITING_USER_RESPONSE"


def test_all_five_tokens_known():
    assert set(cv.VERDICT_TOKENS) == {"JOB_DONE", "INCOMPLETE_OR_FAILED_JOB",
        "INTENTIONALLY_UNCOMMITTED_CHANGES", "WAITING_USER_RESPONSE",
        "MAX_RETRIES_EXCEEDED"}

def test_gates_pass_clean_pushed(tmp_path, monkeypatch):
    import subprocess
    from workagent import cli_validate as cv
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("x")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok(""),
                        raising=False)
    # stub git helpers via monkeypatched subprocess not needed: gates call
    # cv._git() seam; patch it directly.
    monkeypatch.setattr(cv, "_git", lambda *args, cwd="": (0, ""))
    monkeypatch.setattr(cv, "_git_porcelain", lambda wt: "")
    monkeypatch.setattr(cv, "_git_diff_stat", lambda wt: " f.txt | 1 +")
    monkeypatch.setattr(cv, "_branch_pushed", lambda wt: (True, "in sync"))
    res = cv.run_deterministic_gates(str(wt), 0, "work summary", True)
    assert res["passed"] is True
    assert res["reasons"] == []


def _ok(out=""):
    class R:
        returncode = 0
        stdout = out
        stderr = ""
    return R()


def test_gates_fail_dirty_when_changes_unexpected(tmp_path, monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "_git", lambda *a, **k: (0, ""))
    monkeypatch.setattr(cv, "_git_porcelain", lambda wt: " M f.txt")
    monkeypatch.setattr(cv, "_git_diff_stat", lambda wt: " f.txt | 1 +")
    monkeypatch.setattr(cv, "_branch_pushed", lambda wt: (False, "ahead by 1"))
    res = cv.run_deterministic_gates(str(tmp_path), 0, "summary", True)
    assert res["passed"] is False
    assert any("push" in r.lower() for r in res["reasons"])


def test_gates_fail_empty_transcript(tmp_path, monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "_git", lambda *a, **k: (0, ""))
    monkeypatch.setattr(cv, "_git_porcelain", lambda wt: "")
    monkeypatch.setattr(cv, "_git_diff_stat", lambda wt: "")
    monkeypatch.setattr(cv, "_branch_pushed", lambda wt: (True, "no upstream, clean"))
    res = cv.run_deterministic_gates(str(tmp_path), 0, "", False)
    assert res["passed"] is False


def test_dirty_gate_clean(tmp_path, monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "_git_porcelain", lambda wt: "")
    assert cv.dirty_gate(str(tmp_path), "feat/x") == {"dirty": False, "porcelain": ""}


def test_dirty_gate_dirty_lists_files(tmp_path, monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "_git_porcelain", lambda wt: " M a.txt\n?? b.txt")
    res = cv.dirty_gate(str(tmp_path), "feat/x")
    assert res["dirty"] is True and "a.txt" in res["porcelain"]


def test_continue_prompt_carries_token_rule():
    from workagent import cli_validate as cv
    p = cv.continue_prompt(" M a.txt", "feat/x")
    assert "INTENTIONALLY_UNCOMMITTED_CHANGES" in p and "feat/x" in p and "a.txt" in p


def test_judge_prompt_caps_context():
    from workagent import cli_validate as cv
    big = "x" * 20000
    p = cv.judge_prompt(big, big, {"passed": True, "checks": {}, "reasons": []}, "")
    assert len(p) < 16000 and "JOB_DONE" in p and "INCOMPLETE_OR_FAILED_JOB" in p


def _pass_gates(monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "dirty_gate",
                        lambda wt, br: {"dirty": False, "porcelain": ""})
    monkeypatch.setattr(cv, "run_deterministic_gates",
                        lambda wt, code, tx, exp: {"passed": True, "checks": {},
                                                  "reasons": []})


def test_loop_passes_first_try(monkeypatch):
    from workagent import cli_validate as cv
    _pass_gates(monkeypatch)
    calls = []
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync",
        run_once=lambda p: {"exit_code": 0, "transcript": "all good\nJOB_DONE"},
        run_judge=lambda p: "looks complete\nJOB_DONE")
    assert res["verdict"] == "JOB_DONE" and res["attempts"] == 1


def test_loop_retries_then_succeeds(monkeypatch):
    from workagent import cli_validate as cv
    _pass_gates(monkeypatch)
    outputs = [{"exit_code": 0, "transcript": "half done"},
               {"exit_code": 0, "transcript": "finished now\nJOB_DONE"}]
    judge_calls = []
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync",
        run_once=lambda p: outputs.pop(0),
        run_judge=lambda p: (judge_calls.append(p) or "still broken\nINCOMPLETE_OR_FAILED_JOB")
        if not judge_calls else "fixed\nJOB_DONE")
    assert res["verdict"] == "JOB_DONE" and res["attempts"] >= 2


def test_loop_terminal_after_cap(monkeypatch):
    from workagent import cli_validate as cv
    _pass_gates(monkeypatch)
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync", max_retries=3,
        run_once=lambda p: {"exit_code": 0, "transcript": "broken"},
        run_judge=lambda p: "nope\nINCOMPLETE_OR_FAILED_JOB")
    assert res["verdict"] == "MAX_RETRIES_EXCEEDED" and res["attempts"] == 4
    assert len(res["runs"]) == res["attempts"] == 4
    assert [("terminal" in r["trace"]) for r in res["runs"]] == [False, False, False, True]


def test_waiting_exits_without_retry(monkeypatch):
    from workagent import cli_validate as cv
    _pass_gates(monkeypatch)
    seen = []
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync",
        run_once=lambda p: (seen.append(p) or {"exit_code": 0, "transcript": "need input\nWAITING_USER_RESPONSE"}),
        run_judge=lambda p: "unreachable")
    assert res["verdict"] == "WAITING_USER_RESPONSE" and res["waiting"] is True
    assert len(seen) == 1


def test_nonzero_exit_skips_judge(monkeypatch):
    from workagent import cli_validate as cv
    _pass_gates(monkeypatch)
    judge_calls = []
    once = [{"exit_code": 2, "transcript": "boom\nstderr tail"},
            {"exit_code": 0, "transcript": "recovered\nJOB_DONE"}]
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync", max_retries=3,
        run_once=lambda p: once.pop(0),
        run_judge=lambda p: (judge_calls.append(p) or "ok\nJOB_DONE"))
    assert res["verdict"] == "JOB_DONE" and res["attempts"] == 2
    assert len(judge_calls) == 1
    assert res["runs"][0]["trace"]["phase"] == "gates"
    assert "exit code 2" in res["runs"][0]["trace"]["reasons"][0]
    assert len(res["runs"]) == 2 and res["runs"][1]["trace"]["terminal"] == "JOB_DONE"


def test_dirty_tree_triggers_continuation(monkeypatch):
    from workagent import cli_validate as cv
    states = [{"dirty": True, "porcelain": " M a.txt"},
              {"dirty": False, "porcelain": ""}]
    monkeypatch.setattr(cv, "dirty_gate", lambda wt, br: states.pop(0))
    monkeypatch.setattr(cv, "run_deterministic_gates",
                        lambda wt, code, tx, exp: {"passed": True, "checks": {},
                                                  "reasons": []})
    seen = []

    def fake_once(p):
        seen.append(p)
        if len(seen) == 1:
            return {"exit_code": 0, "transcript": "work in progress"}
        return {"exit_code": 0, "transcript": "committed\nJOB_DONE"}

    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="start", max_retries=3,
        run_once=fake_once,
        run_judge=lambda p: "ok\nJOB_DONE")
    assert res["verdict"] == "JOB_DONE" and res["attempts"] == 1
    assert len(seen) == 2
    assert "a.txt" in seen[1] and "origin/feat/x" in seen[1]
    assert [r["output"] for r in res["runs"]] == ["work in progress", "committed\nJOB_DONE"]
    assert [r["trace"]["phase"] for r in res["runs"]] == ["dirty-gate", "judge"]
    assert len(res["runs"]) == len(seen)


def test_dirty_continuation_intentional_accepts(monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "dirty_gate",
                        lambda wt, br: {"dirty": True, "porcelain": " M a.txt"})
    judge_calls = []
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="fix_comments",
        run_once=lambda p: {"exit_code": 0,
                            "transcript": ("keeping local\n"
                                           "INTENTIONALLY_UNCOMMITTED_CHANGES")}
        if "uncommitted" in p else {"exit_code": 0,
                                    "transcript": "work in progress"},
        run_judge=lambda p: (judge_calls.append(p) or "unreachable"))
    assert res["verdict"] == "INTENTIONALLY_UNCOMMITTED_CHANGES"
    assert judge_calls == []
    assert [r["trace"].get("terminal") for r in res["runs"]] == [None, "INTENTIONALLY_UNCOMMITTED_CHANGES"]


def test_gate_fail_skips_judge(monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "dirty_gate",
                        lambda wt, br: {"dirty": False, "porcelain": ""})
    gate_calls = []

    def fake_gates(wt, code, tx, exp):
        gate_calls.append((code, tx, exp))
        if len(gate_calls) == 1:
            return {"passed": False, "checks": {},
                    "reasons": ["not pushed: ahead by 1"]}
        return {"passed": True, "checks": {}, "reasons": []}

    monkeypatch.setattr(cv, "run_deterministic_gates", fake_gates)
    judge_calls = []
    once = [{"exit_code": 0, "transcript": "first try"},
            {"exit_code": 0, "transcript": "second try\nJOB_DONE"}]
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync", max_retries=3,
        run_once=lambda p: once.pop(0),
        run_judge=lambda p: (judge_calls.append(p) or "ok\nJOB_DONE"))
    assert res["verdict"] == "JOB_DONE" and res["attempts"] == 2
    assert len(judge_calls) == 1
    assert res["runs"][0]["trace"]["phase"] == "gates"
    assert gate_calls[0][2] is False

def test_runs_validation_column_roundtrip(isolated_config, monkeypatch):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES (?, ?, ?, NULL, '2026-01-01', '{}')", ("k", "/tmp/wt", "b"))
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp",
                            initiator_command="start", prompt="p",
                            file_path="/tmp/x.jsonl",
                            session_id="test-sid")
    rid = sq.insert_run(db, sid, "start", ["k"], 0, output=["hi"],
                        validation={"attempt": 1, "verdict": "JOB_DONE"})
    rows = sq.list_runs(db)
    row = next(r for r in rows if r["id"] == rid)
    assert row["validation"] == {"attempt": 1, "verdict": "JOB_DONE"}


def test_run_harness_headless_validates(monkeypatch, tmp_path, isolated_config, capsys):
    import json
    import pytest
    import typer
    from workagent import backend, cli_harness as hh, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES (?, ?, 'b', NULL, '2026-01-01', '{}')", ("k", str(tmp_path)))
    monkeypatch.setattr(hh.store, "record_harness_run", lambda *a: None)
    monkeypatch.setattr(hh.store, "clear_harness_run", lambda *a: None)
    launched = []

    def launch(name, prompt, workdir, no_tty, extra_args=None, **kwargs):
        launched.append(prompt)
        directory = extra_args[extra_args.index("--session-dir") + 1]
        from pathlib import Path
        Path(directory, "minted.jsonl").write_text(json.dumps({"message": {
            "role": "assistant", "content": [{"type": "text", "text":
                "Need a decision\nWAITING_USER_RESPONSE"}]}}) + "\n")
        return 0

    monkeypatch.setattr(backend, "launch", launch)
    result = {"command": "start", "branch": "b", "key": "k"}
    with pytest.raises(typer.Exit) as exc:
        hh._run_harness("omp", "do it", str(tmp_path), str(tmp_path), True, True,
                        result, True, run_key="k")
    assert exc.value.exit_code == 2
    assert len(launched) == 1 and launched[0].endswith(backend.VERDICT_SUFFIX)
    assert str(tmp_path) in capsys.readouterr().err
    rows = sq.list_runs(db)
    assert rows[0]["validation"]["verdict"] == "WAITING_USER_RESPONSE"
    assert result["session_file"].endswith("minted.jsonl")


def test_headless_retry_resumes_and_judge_is_fresh(monkeypatch, tmp_path, isolated_config):
    import json
    from pathlib import Path
    from workagent import backend, cli_harness as hh, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES (?, ?, 'b', NULL, '2026-01-01', '{}')", ("k", str(tmp_path)))
    monkeypatch.setattr(hh.store, "record_harness_run", lambda *a: None)
    monkeypatch.setattr(hh.store, "clear_harness_run", lambda *a: None)
    from workagent import cli_validate as cv
    # Wiring test (resume + fresh judge), not a gate test: stub gates clean.
    monkeypatch.setattr(cv, "dirty_gate",
                        lambda wt, br: {"dirty": False, "porcelain": ""})
    monkeypatch.setattr(cv, "run_deterministic_gates",
                        lambda wt, code, tx, exp: {"passed": True, "checks": {},
                                                  "reasons": []})
    calls = []

    def launch(name, prompt, workdir, no_tty, extra_args=None, **kwargs):
        calls.append(extra_args[:])
        if "--resume" in extra_args:
            path = Path(extra_args[extra_args.index("--resume") + 1])
        else:
            path = Path(extra_args[extra_args.index("--session-dir") + 1]) / "minted.jsonl"
        text = ("INCOMPLETE_OR_FAILED_JOB" if len(calls) == 2 else "JOB_DONE")
        path.write_text(json.dumps({"message": {"role": "assistant", "content":
                        [{"type": "text", "text": text}]}}) + "\n")
        return 0

    monkeypatch.setattr(backend, "launch", launch)
    result = {"command": "start", "branch": "b", "key": "k"}
    hh._run_harness("omp", "do it", str(tmp_path), str(tmp_path), True, True,
                    result, True, run_key="k")
    assert len(calls) == 4
    assert "--resume" in calls[2] and calls[2][-1] == result["session_file"]
    assert "--session-dir" in calls[1] and "--session-dir" in calls[3]
    assert calls[1][-1] != calls[3][-1]
    assert [row["validation"]["attempt"] for row in sq.list_runs(db)] == [2, 1]


def test_dirty_continuation_crash_never_judges(monkeypatch):
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "dirty_gate",
                        lambda wt, br: {"dirty": True, "porcelain": " M a.txt"})
    judge_calls = []
    once = [{"exit_code": 0, "transcript": "work in progress"},
            {"exit_code": 1, "transcript": "crashed\nJOB_DONE"}]
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="start", max_retries=0,
        run_once=lambda p: once.pop(0),
        run_judge=lambda p: (judge_calls.append(p) or "ok\nJOB_DONE"))
    assert judge_calls == []
    assert res["verdict"] == "MAX_RETRIES_EXCEEDED"
    assert len(res["runs"]) == 2
    assert res["runs"][1]["exit_code"] == 1
    assert res["runs"][1]["trace"]["phase"] == "gates"


def _headless_fake(monkeypatch, tmp_path, isolated_config, cont_text, cont_exit):
    """Fake harness for _run_harness headless: main run mints a transcript,
    --resume runs are continuations (cont_text / cont_exit), judge runs use a
    workagent-judge-* session dir and are counted separately."""
    import json
    from pathlib import Path
    from workagent import backend, cli_harness as hh, store_sqlite as sq

    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES (?, ?, 'b', NULL, '2026-01-01', '{}')", ("k", str(tmp_path)))
    monkeypatch.setattr(hh.store, "record_harness_run", lambda *a: None)
    monkeypatch.setattr(hh.store, "clear_harness_run", lambda *a: None)
    judge_calls = []
    main_calls = []

    def _write(target, text):
        target.write_text(json.dumps({"message": {"role": "assistant", "content":
                          [{"type": "text", "text": text}]}}) + "\n")

    def launch(name, prompt, workdir, no_tty, extra_args=None, **kwargs):
        if "--resume" in extra_args:
            _write(Path(extra_args[extra_args.index("--resume") + 1]), cont_text)
            return cont_exit
        directory = extra_args[extra_args.index("--session-dir") + 1]
        if "workagent-judge-" in directory:
            judge_calls.append(prompt)
            _write(Path(directory) / "judge.jsonl", "ok\nJOB_DONE")
            return 0
        main_calls.append(prompt)
        _write(Path(directory) / "minted.jsonl", "work in progress")
        return 0

    monkeypatch.setattr(backend, "launch", launch)
    return db, judge_calls, main_calls, hh


def test_dirty_continuation_pairs_db_rows(monkeypatch, tmp_path, isolated_config):
    from workagent import cli_validate as cv, store_sqlite as sq
    db, judge_calls, main_calls, hh = _headless_fake(
        monkeypatch, tmp_path, isolated_config, "committed\nJOB_DONE", 0)
    states = [{"dirty": True, "porcelain": " M a.txt"},
              {"dirty": False, "porcelain": ""}]
    monkeypatch.setattr(cv, "dirty_gate", lambda wt, br: states.pop(0))
    monkeypatch.setattr(cv, "run_deterministic_gates",
                        lambda wt, code, tx, exp: {"passed": True, "checks": {},
                                                  "reasons": []})
    result = {"command": "start", "branch": "b", "key": "k"}
    hh._run_harness("omp", "do it", str(tmp_path), str(tmp_path), True, True,
                    result, True, run_key="k")
    rows = sorted(sq.list_runs(db), key=lambda r: r["id"])
    assert len(main_calls) == 1 and len(judge_calls) == 1
    assert len(rows) == 2
    assert rows[0]["output"].endswith("work in progress")
    assert rows[1]["output"].endswith("committed\nJOB_DONE")
    assert rows[0]["validation"]["phase"] == "dirty-gate"
    assert rows[1]["validation"]["phase"] == "judge"
    assert rows[1]["validation"]["terminal"] == "JOB_DONE"
    assert rows[1]["exit_code"] == 0


def test_crashed_continuation_never_judges_db(monkeypatch, tmp_path, isolated_config):
    from workagent import cli_validate as cv, store_sqlite as sq
    import pytest
    import typer
    db, judge_calls, main_calls, hh = _headless_fake(
        monkeypatch, tmp_path, isolated_config, "crashed\nJOB_DONE", 1)
    monkeypatch.setattr(cv, "dirty_gate",
                        lambda wt, br: {"dirty": True, "porcelain": " M a.txt"})
    result = {"command": "start", "branch": "b", "key": "k"}
    with pytest.raises(typer.Exit) as exc:
        hh._run_harness("omp", "do it", str(tmp_path), str(tmp_path), True, True,
                        result, True, run_key="k")
    assert exc.value.exit_code == 1
    assert judge_calls == []
    rows = sorted(sq.list_runs(db), key=lambda r: r["id"])
    assert rows and rows[-1]["validation"]["terminal"] == "MAX_RETRIES_EXCEEDED"
    assert all(r["validation"]["phase"] != "judge" for r in rows)
    assert rows[-1]["exit_code"] == 1

def _git_seq(monkeypatch, mapping):
    """Stub cv._git by (args-tuple-prefix) -> (code, out)."""
    from workagent import cli_validate as cv

    def _fake(*args, cwd=""):
        for prefix, result in mapping.items():
            if tuple(args[:len(prefix)]) == prefix:
                return result
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(cv, "_git", _fake)


def test_diff_gate_passes_on_unpushed_commits(tmp_path, monkeypatch):
    """Finding 1: committed-but-unpushed work must pass the diff sub-gate.

    The full gate still fails on `pushed` until the push lands (by design:
    the harness retries after pushing); what must not happen is the diff
    gate ALSO failing on the empty working tree."""
    from workagent import cli_validate as cv
    _git_seq(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa"),
        ("rev-parse", "@{u}"): (0, "bbb"),
        ("rev-list", "--count", "@{u}..HEAD"): (0, "2"),
        ("diff", "--stat", "HEAD"): (0, ""),
    })
    res = cv.run_deterministic_gates(str(tmp_path), 0, "did work", True)
    assert res["checks"]["diff"]["ok"] is True
    # Only the pushed gate blocks, naming the unpushed commits.
    assert res["reasons"] == ["not pushed: ahead of upstream by 2 (unpushed)"]


def test_diff_gate_passes_on_uncommitted_changes(tmp_path, monkeypatch):
    """Finding 1: uncommitted changes still pass when nothing is pushed."""
    from workagent import cli_validate as cv
    _git_seq(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa"),
        ("rev-parse", "@{u}"): (0, "aaa"),
        ("rev-list", "--count", "@{u}..HEAD"): (0, "0"),
        ("diff", "--stat", "HEAD"): (0, " f.txt | 1 +"),
    })
    res = cv.run_deterministic_gates(str(tmp_path), 0, "did work", True)
    assert res["passed"] is True


def test_diff_gate_fails_when_pushed_clean(tmp_path, monkeypatch):
    """Finding 1: pushed+clean must still fail expect_changes."""
    from workagent import cli_validate as cv
    _git_seq(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa"),
        ("rev-parse", "@{u}"): (0, "aaa"),
        ("rev-list", "--count", "@{u}..HEAD"): (0, "0"),
        ("diff", "--stat", "HEAD"): (0, ""),
    })
    res = cv.run_deterministic_gates(str(tmp_path), 0, "did work", True)
    assert res["passed"] is False
    assert res["checks"]["diff"]["ok"] is False


def test_behind_upstream_reports_not_pushed(tmp_path, monkeypatch):
    """Finding 2: behind-upstream must fail the pushed gate distinctly."""
    from workagent import cli_validate as cv
    _git_seq(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa"),
        ("rev-parse", "@{u}"): (0, "bbb"),
        ("rev-list", "--count", "@{u}..HEAD"): (0, "0"),
        ("diff", "--stat", "HEAD"): (0, ""),
    })
    ok, detail = cv._branch_pushed(str(tmp_path))
    assert ok is False and detail == "behind upstream"
    res = cv.run_deterministic_gates(str(tmp_path), 0, "summary", False)
    assert res["passed"] is False
    assert "behind upstream" in res["reasons"][0]


def test_ahead_upstream_reports_unpushed(tmp_path, monkeypatch):
    """Finding 2: ahead-of-upstream reports the unpushed count."""
    from workagent import cli_validate as cv
    _git_seq(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa"),
        ("rev-parse", "@{u}"): (0, "bbb"),
        ("rev-list", "--count", "@{u}..HEAD"): (0, "3"),
        ("diff", "--stat", "HEAD"): (0, ""),
    })
    ok, detail = cv._branch_pushed(str(tmp_path))
    assert ok is False and "ahead of upstream by 3" in detail


def test_attempts_persist_across_calls(monkeypatch):
    """Finding 3: validation_attempts round-trips via the metadata seam."""
    from workagent import cli_validate as cv
    saved = {}
    monkeypatch.setattr(cv, "_attempt_store", lambda: (
        lambda db, sid, include_deleted_worktrees=False: {
            "metadata": {"validation_attempts": saved.get(sid, 0)}},
        lambda db, sid, patch: saved.update({sid: patch["validation_attempts"]})
        or patch))
    _pass_gates(monkeypatch)
    first = cv.validate_headless(
        worktree="/wt", branch="b", prompt="do it", session_type="sync",
        run_once=lambda p: {"exit_code": 0, "transcript": "broken"},
        run_judge=lambda p: "nope\nINCOMPLETE_OR_FAILED_JOB",
        max_retries=0, session_db="db", session_id="s1")
    assert first["attempts"] == 1 and saved["s1"] == 1
    second = cv.validate_headless(
        worktree="/wt", branch="b", prompt="do it", session_type="sync",
        run_once=lambda p: {"exit_code": 0, "transcript": "fixed\nJOB_DONE"},
        run_judge=lambda p: "ok\nJOB_DONE",
        max_retries=3, session_db="db", session_id="s1")
    # Prior counter (1) is preserved: the resumed loop continues at 2.
    assert second["attempts"] == 2 and saved["s1"] == 2


def test_suffix_carries_intentional_token():
    """Finding 4: headless prompts know the INTENTIONAL verdict token."""
    from workagent import backend
    assert "INTENTIONALLY_UNCOMMITTED_CHANGES" in backend.VERDICT_SUFFIX
    assert "WAITING_USER_RESPONSE" in backend.VERDICT_SUFFIX


def _tool_jsonl(path, results):
    import json
    lines = []
    for i, err in enumerate(results):
        content = [{"type": "text", "text": "error: boom"}] if err else [
            {"type": "text", "text": "ok"}]
        lines.append(json.dumps({"message": {"role": "toolResult",
            "toolCallId": f"c{i}", "content": content}}))
    # One unparseable line + one non-tool line: both must be skipped.
    lines += ["not json", json.dumps({"message": {"role": "assistant",
        "content": [{"type": "text", "text": "done"}]}})]
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_error_rate_gate_trips_at_half(tmp_path, monkeypatch):
    """Finding 5: >=50% tool errors fail; empty toolResults pass."""
    from workagent import cli_validate as cv
    monkeypatch.setattr(cv, "_git", lambda *a, **k: (0, "aaa"))
    monkeypatch.setattr(cv, "_branch_pushed", lambda wt: (True, "in sync"))
    bad = _tool_jsonl(tmp_path / "bad.jsonl", [True, True, False, False])
    res = cv.run_deterministic_gates(
        str(tmp_path), 0, "summary", False, transcript_path=bad)
    assert res["passed"] is False
    assert any("tool error rate 2/4" in r for r in res["reasons"])
    good = _tool_jsonl(tmp_path / "good.jsonl", [True, False, False, False])
    res = cv.run_deterministic_gates(
        str(tmp_path), 0, "summary", False, transcript_path=good)
    assert res["passed"] is True
    empty = tmp_path / "empty.jsonl"
    empty.write_text("not json\n")
    res = cv.run_deterministic_gates(
        str(tmp_path), 0, "summary", False, transcript_path=str(empty))
    assert res["passed"] is True
    assert res["checks"]["tool_errors"]["detail"] == "no toolResults to score"
