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


def test_loop_passes_first_try():
    from workagent import cli_validate as cv
    calls = []
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync",
        run_once=lambda p: {"exit_code": 0, "transcript": "all good\nJOB_DONE"},
        run_judge=lambda p: "looks complete\nJOB_DONE")
    assert res["verdict"] == "JOB_DONE" and res["attempts"] == 1


def test_loop_retries_then_succeeds():
    from workagent import cli_validate as cv
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


def test_loop_terminal_after_cap():
    from workagent import cli_validate as cv
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync", max_retries=3,
        run_once=lambda p: {"exit_code": 0, "transcript": "broken"},
        run_judge=lambda p: "nope\nINCOMPLETE_OR_FAILED_JOB")
    assert res["verdict"] == "MAX_RETRIES_EXCEEDED" and res["attempts"] == 4


def test_waiting_exits_without_retry():
    from workagent import cli_validate as cv
    seen = []
    res = cv.validate_headless(
        worktree="/wt", branch="feat/x", prompt="do it",
        session_type="sync",
        run_once=lambda p: (seen.append(p) or {"exit_code": 0, "transcript": "need input\nWAITING_USER_RESPONSE"}),
        run_judge=lambda p: "unreachable")
    assert res["verdict"] == "WAITING_USER_RESPONSE" and res["waiting"] is True
    assert len(seen) == 1


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
