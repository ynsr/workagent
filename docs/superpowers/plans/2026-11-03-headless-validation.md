# Headless validation loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Headless harness runs self-validate (dirty-gate → deterministic gates → AI judge → resume-retry ×3) with per-attempt trace logging on the runs record.

**Architecture:** New `cli_validate.py` module owns verdict parsing, gates, judge prompt, and the retry loop; `_run_harness` calls it on the headless path only. Verdict tokens parsed tolerantly from the transcript's last assistant line. One additive `validation` JSON column on `runs`.

**Tech Stack:** Python, SQLite (WAL, `state.db`), Typer CLI, `omp` harness (`-p --auto-approve`, `--resume`/`--session-dir`), pytest with `isolated_config` fixture.

**Spec:** `docs/superpowers/specs/2026-11-03-headless-validation-design.md`

## Global Constraints

- Headless only (`no_tty and launch`); preview/terminal/interactive paths unchanged.
- Retry resumes the same session (`omp --resume` on the non-empty transcript); judge is always a fresh headless session.
- Max 3 re-runs (4 attempts total); counter `validation_attempts` in session metadata.
- Tolerant verdict parse: last non-empty assistant line, strip quotes/fences, case-insensitive substring.
- Token priority: `MAX_RETRIES_EXCEEDED` > `WAITING_USER_RESPONSE` > `INTENTIONALLY_UNCOMMITTED_CHANGES` > `JOB_DONE` / `INCOMPLETE_OR_FAILED_JOB`.
- New files/components ≤500 lines.
- stdout carries ONLY data; logs → stderr (`eprint`).
- Exit codes: 0 success, 1 general error, 2 usage/needs-human (`WAITING_USER_RESPONSE` → 2).
- `INTENTIONALLY_UNCOMMITTED_CHANGES` short-circuits the judge (Phase-1 only: `start`/`fix_comments`).
- Judge never emits `WAITING_USER_RESPONSE` (indecisive judge → `INCOMPLETE_OR_FAILED_JOB`).
- `runs.validation` additive column `TEXT NOT NULL DEFAULT '{}'`; old rows read as `{}`.
- No webapp SPECS drift: no new CLI flags in this plan (internal loop only), no API_CONTRACT change (`/api/runs` already returns full rows).
- CHANGELOG.md: one line per change, ≤150 chars, non-technical wording, ID at end (no ID → omit ID rather than invent).
- Tests mirror module names (`tests/test_cli_validate.py`).

## Review Focus

- A harness that prints `JOB_DONE` mid-transcript but ends with failure prose must be treated as failed (only the last line counts).
- A dirty tree containing only ignored/untracked scratch files must not force a retry loop (porcelain truth, not `git status` human text).
- A judge session that itself crashes must not hang the loop (judge failure → retry with reasons, consumes one attempt).
- `WAITING_USER_RESPONSE` buried above the last line must not trigger human-exit (last-line-only rule).
- Concurrent `_run_harness` calls on the same worktree must not double-retry (one-harness lock already held; validation runs inside it).

---

### Task 1: Verdict parser + token constants

**Files:**
- Create: `src/workagent/cli_validate.py` (skeleton: tokens + `parse_verdict` only)
- Test: `tests/test_cli_validate.py`

**Interfaces:**
- Consumes: `harness_summary.last_assistant_text(transcript: str | Path) -> str`
- Produces: `VERDICT_TOKENS: tuple[str, ...]`, `parse_verdict(text: str) -> tuple[str | None, str]` returning `(token, parse_mode)` where mode is `"exact" | "tolerant" | "none"`; `VERDICT_PRIORITY: list[str]`; `pick_verdict(candidates: list[str]) -> str | None` applying priority order.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_validate.py -v`
Expected: FAIL with "No module named 'workagent.cli_validate'" (or collection error).

- [ ] **Step 3: Write minimal implementation**

```python
"""Headless validation loop: verdict parse, gates, judge, retry (spec:
docs/superpowers/specs/2026-11-03-headless-validation-design.md)."""
from __future__ import annotations

VERDICT_TOKENS: tuple[str, ...] = (
    "JOB_DONE",
    "INCOMPLETE_OR_FAILED_JOB",
    "INTENTIONALLY_UNCOMMITTED_CHANGES",
    "WAITING_USER_RESPONSE",
    "MAX_RETRIES_EXCEEDED",
)

VERDICT_PRIORITY: list[str] = [
    "MAX_RETRIES_EXCEEDED",
    "WAITING_USER_RESPONSE",
    "INTENTIONALLY_UNCOMMITTED_CHANGES",
    "JOB_DONE",
    "INCOMPLETE_OR_FAILED_JOB",
]


def _clean_line(line: str) -> str:
    s = line.strip()
    for fence in ("```", "'''"):
        if s.startswith(fence) and s.endswith(fence) and len(s) > len(fence) * 2:
            s = s[len(fence):-len(fence)].strip()
    return s.strip().strip("\"'`").strip()


def parse_verdict(text: str) -> tuple[str | None, str]:
    """Last non-empty line → (token, mode). No token → (None, "none")."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None, "none"
    raw = lines[-1].strip()
    if raw in VERDICT_TOKENS:
        return raw, "exact"
    cleaned = _clean_line(raw).upper()
    for tok in VERDICT_TOKENS:
        if tok in cleaned:
            return tok, "tolerant"
    return None, "none"


def pick_verdict(candidates: list[str]) -> str | None:
    """Highest-priority token present (spec priority order)."""
    present = {c for c in candidates if c in VERDICT_TOKENS}
    for tok in VERDICT_PRIORITY:
        if tok in present:
            return tok
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_validate.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_validate.py tests/test_cli_validate.py
git commit -m "add headless verdict parser with tolerant token match"
```

### Task 2: Deterministic gates (exit code, pushed, transcript sanity)

**Files:**
- Modify: `src/workagent/cli_validate.py` (append gate functions)
- Test: `tests/test_cli_validate.py` (append)

**Interfaces:**
- Consumes: `parse_verdict` from Task 1; stdlib `subprocess` for git.
- Produces: `run_deterministic_gates(worktree: str, exit_code: int, transcript: str, expect_changes: bool) -> dict` returning `{"passed": bool, "checks": {"exit_code": {...}, "pushed": {...}, "transcript": {...}, "diff": {...}}, "reasons": list[str]}`. Each check dict: `{"ok": bool, "detail": str}`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_validate.py -v`
Expected: FAIL with "has no attribute 'run_deterministic_gates'".

- [ ] **Step 3: Write minimal implementation**

```python
def _git(*args: str, cwd: str = "") -> tuple[int, str]:
    import subprocess
    try:
        r = subprocess.run(["git", *args], cwd=cwd or None,
                           capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:
        return 128, f"git error: {e}"


def _git_porcelain(worktree: str) -> str:
    code, out = _git("status", "--porcelain", cwd=worktree)
    return out if code == 0 else f"GIT_ERROR:{out}"


def _git_diff_stat(worktree: str) -> str:
    code, out = _git("diff", "--stat", "HEAD", "--", cwd=worktree)
    return out if code == 0 else ""


def _branch_pushed(worktree: str) -> tuple[bool, str]:
    code, head = _git("rev-parse", "HEAD", cwd=worktree)
    if code != 0:
        return False, "no HEAD"
    code, upstream = _git("rev-parse", "@{u}", cwd=worktree)
    if code != 0:
        porcelain = _git_porcelain(worktree)
        return (not porcelain, "no upstream, clean" if not porcelain else "no upstream, dirty tree")
    code, base = _git("merge-base", "HEAD", "@{u}", cwd=worktree)
    if code != 0:
        return False, "no merge-base with upstream"
    return (base == head, "in sync" if base == head else "local differs from upstream")


def run_deterministic_gates(worktree: str, exit_code: int,
                            transcript_tail: str,
                            expect_changes: bool) -> dict:
    """Zero-LLM gates. Never raises; git failures are failed checks."""
    checks: dict = {}
    reasons: list[str] = []
    checks["exit_code"] = {"ok": exit_code == 0,
                           "detail": f"exit={exit_code}"}
    if exit_code != 0:
        reasons.append(f"harness exit code {exit_code}")
    pushed, pdetail = _branch_pushed(worktree)
    checks["pushed"] = {"ok": pushed, "detail": pdetail}
    if not pushed:
        reasons.append(f"not pushed: {pdetail}")
    has_text = bool((transcript_tail or "").strip())
    checks["transcript"] = {"ok": has_text,
                            "detail": "non-empty" if has_text else "empty transcript tail"}
    if not has_text:
        reasons.append("empty transcript tail")
    if expect_changes:
        stat = _git_diff_stat(worktree)
        ok = bool(stat.strip())
        checks["diff"] = {"ok": ok, "detail": stat[:500] if ok else "empty diff"}
        if not ok:
            reasons.append("expected code changes but diff is empty")
    else:
        checks["diff"] = {"ok": True, "detail": "no changes expected"}
    return {"passed": not reasons, "checks": checks, "reasons": reasons}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_validate.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_validate.py tests/test_cli_validate.py
git commit -m "add deterministic headless gates (exit/push/transcript/diff)"
```

### Task 3: Phase-1 dirty-tree gate + continuation prompt builder

**Files:**
- Modify: `src/workagent/cli_validate.py` (append `dirty_gate`, prompt builders)
- Test: `tests/test_cli_validate.py` (append)

**Interfaces:**
- Consumes: `_git_porcelain`, `parse_verdict` (Tasks 1–2).
- Produces: `dirty_gate(worktree: str, branch: str) -> dict` (`{"dirty": bool, "porcelain": str}`); `continue_prompt(porcelain: str, branch: str) -> str`; `judge_prompt(original: str, previous: str, gates: dict, diff_stat: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_validate.py::test_dirty_gate_clean -v`
Expected: FAIL with "has no attribute 'dirty_gate'".

- [ ] **Step 3: Write minimal implementation**

```python
def dirty_gate(worktree: str, branch: str) -> dict:
    """Phase-1 gate (start/fix_comments): dirty worktree → continuation."""
    porcelain = _git_porcelain(worktree)
    return {"dirty": bool(porcelain.strip()), "porcelain": porcelain}


def continue_prompt(porcelain: str, branch: str) -> str:
    """Resume-same-session prompt for uncommitted changes."""
    return (
        "The previous session ended with uncommitted changes:\n"
        f"{porcelain.strip()}\n\n"
        f"Continue the unfinished job: commit and push to origin/{branch}. "
        "Never create or push a different branch.\n"
        "If the remaining changes are intentional and must stay uncommitted, "
        "reply with only INTENTIONALLY_UNCOMMITTED_CHANGES as the last line and exit.\n"
        "Otherwise end your reply with only JOB_DONE as the last line."
    )


def judge_prompt(original: str, previous: str, gates: dict, diff_stat: str) -> str:
    """Fresh-session judge prompt: validate only, end with one token."""
    orig = (original or "")[:8000]
    prev = (previous or "")[:4000]
    stat = (diff_stat or "")[:2000]
    return (
        "Validate the previous harness session. Do not do the work again.\n\n"
        f"Original task:\n{orig}\n\n"
        f"Previous session output:\n{prev}\n\n"
        f"Deterministic gate results: {gates}\n"
        f"Worktree diff stat: {stat or '(empty)'}\n\n"
        "If the job is complete, end your reply with exactly one line: JOB_DONE\n"
        "If it is incomplete or failed, end with exactly one line: "
        "INCOMPLETE_OR_FAILED_JOB (put 1-3 reason lines directly above it)."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_validate.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_validate.py tests/test_cli_validate.py
git commit -m "add dirty-gate and judge prompt builders"
```

### Task 4: Retry loop orchestration (`validate_headless`)

**Files:**
- Modify: `src/workagent/cli_validate.py` (append `validate_headless`)
- Test: `tests/test_cli_validate.py` (append; stub harness launch + judge via monkeypatched seams)

**Interfaces:**
- Consumes: everything from Tasks 1–3; injected seams `run_once(prompt) -> dict{exit_code, transcript}` and `run_judge(prompt) -> str` so tests never spawn `omp`.
- Produces: `validate_headless(*, worktree: str, branch: str, prompt: str, session_type: str | None, run_once, run_judge, max_retries: int = 3) -> dict` returning `{"verdict": str, "attempts": int, "trace": list[dict], "waiting": bool}`. Terminal verdict one of `JOB_DONE | INTENTIONALLY_UNCOMMITTED_CHANGES | MAX_RETRIES_EXCEEDED | WAITING_USER_RESPONSE`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_validate.py::test_loop_passes_first_try -v`
Expected: FAIL with "has no attribute 'validate_headless'".

- [ ] **Step 3: Write minimal implementation**

```python
def validate_headless(*, worktree: str, branch: str, prompt: str,
                       session_type: str | None,
                       run_once, run_judge,
                       max_retries: int = 3) -> dict:
    """Retry loop. run_once(prompt)->{exit_code, transcript}; run_judge(prompt)->text.

    Never raises on judge failure (counts as a failed attempt with reasons).
    Judges Phase-1 continuations too: INTENTIONALLY_UNCOMMITTED_CHANGES
    short-circuits; WAITING_USER_RESPONSE exits 2 without consuming retry.
    """
    trace: list[dict] = []
    attempts = 0
    current_prompt = prompt
    while True:
        attempts += 1
        try:
            out = run_once(current_prompt) or {}
        except Exception as e:
            out = {"exit_code": 1, "transcript": f"harness error: {e}"}
        transcript = str(out.get("transcript", "") or "")
        token, mode = parse_verdict(transcript)
        if token == "WAITING_USER_RESPONSE":
            trace.append({"attempt": attempts, "phase": "main",
                          "verdict": token, "parse": mode,
                          "tail": transcript[-2000:]})
            return {"verdict": token, "attempts": attempts,
                    "trace": trace, "waiting": True}
        if (session_type in ("start", "fix_comments")
                and token == "INTENTIONALLY_UNCOMMITTED_CHANGES"):
            trace.append({"attempt": attempts, "phase": "dirty-gate",
                          "verdict": token, "parse": mode,
                          "tail": transcript[-2000:]})
            return {"verdict": token, "attempts": attempts,
                    "trace": trace, "waiting": False}
        try:
            judge_text = run_judge(judge_prompt(prompt, transcript, {}, ""))
        except Exception as e:
            judge_text = f"judge crashed: {e}\nINCOMPLETE_OR_FAILED_JOB"
        jtoken, jmode = parse_verdict(judge_text or "")
        trace.append({"attempt": attempts, "phase": "judge",
                      "verdict": jtoken or "INCOMPLETE_OR_FAILED_JOB",
                      "parse": jmode, "tail": transcript[-2000:]})
        if jtoken == "JOB_DONE":
            return {"verdict": "JOB_DONE", "attempts": attempts,
                    "trace": trace, "waiting": False}
        if attempts > max_retries:
            return {"verdict": "MAX_RETRIES_EXCEEDED", "attempts": attempts,
                    "trace": trace, "waiting": False}
        current_prompt = (f"Continue the unfinished job. The validation judge "
                          f"reported: {(judge_text or '')[-2000:]}\n"
                          f"End your reply with only JOB_DONE as the last line.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_validate.py -v`
Expected: PASS (17 tests).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_validate.py tests/test_cli_validate.py
git commit -m "add headless retry loop with judge and terminal verdicts"
```

### Task 5: `runs.validation` column + `GET /api/runs` passthrough

**Files:**
- Modify: `src/workagent/store_sqlite.py:49-54` (DDL), `src/workagent/store_sessions.py` (`insert_run`, `list_runs`)
- Test: `tests/test_cli_validate.py` (append) or extend existing runs test with `isolated_config`

**Interfaces:**
- Consumes: `validate_headless` trace entries (Task 4).
- Produces: `insert_run(..., validation: dict | None = None) -> int`; `list_runs` rows carry `validation: dict`. DDL: `validation TEXT NOT NULL DEFAULT '{}'` with `ALTER TABLE runs ADD COLUMN` migration for existing DBs.

- [ ] **Step 1: Write the failing test**

```python
def test_runs_validation_column_roundtrip(isolated_config, monkeypatch):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sid = sq.insert_session(db, worktree_ref="", harness_name="omp",
                            initiator_command="start", prompt="p",
                            file_path="/tmp/x.jsonl",
                            session_id="test-sid")
    rid = sq.insert_run(db, sid, "start", ["k"], 0, output=["hi"],
                        validation={"attempt": 1, "verdict": "JOB_DONE"})
    rows = sq.list_runs(db)
    row = next(r for r in rows if r["id"] == rid)
    assert row["validation"] == {"attempt": 1, "verdict": "JOB_DONE"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_validate.py::test_runs_validation_column_roundtrip -v`
Expected: FAIL with "unexpected keyword argument 'validation'".

- [ ] **Step 3: Write minimal implementation**

```python
# store_sqlite.py SCHEMA: runs DDL gains one line:
#   validation TEXT NOT NULL DEFAULT '{}',
# plus ensure_schema():
#   ALTER TABLE runs ADD COLUMN validation TEXT NOT NULL DEFAULT '{}'
#   (guard with PRAGMA table_info; ignore duplicate-column error).
```

```python
# store_sessions.py:
def insert_run(path, session_id, command, args, exit_code,
               output=None, truncated=False, validation=None) -> int:
    import json
    ...
    val = json.dumps(validation or {})
    # INSERT gains validation column; SELECT in list_runs gains
    # r.validation with json.loads fallback to {}.
```

(Full code written at implementation time following the existing `insert_run`/`list_runs` bodies verbatim; only the column is added.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_validate.py -v`
Expected: PASS (18 tests).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_sessions.py tests/test_cli_validate.py
git commit -m "persist per-attempt validation trace on runs rows"
```

### Task 6: Wire into `_run_harness` headless path + verdict prompt suffixes

**Files:**
- Modify: `src/workagent/cli_harness.py` (`_run_harness` headless block ~lines 403-448), `src/workagent/backend.py` (`prompt_for_issue`, `prompt_for_fix_comments`)
- Test: extend `tests/test_cli_validate.py` with a wiring test (monkeypatched `backend.launch` + transcript file) asserting `_run_harness` invokes validation and exits 2 on `WAITING_USER_RESPONSE`.

**Interfaces:**
- Consumes: `validate_headless`, `dirty_gate`, verdict suffix constant.
- Produces: headless prompts end with a verdict-instruction suffix; `_run_harness` maps terminal verdicts to exits (`JOB_DONE`/`INTENTIONALLY_UNCOMMITTED_CHANGES` → 0; `WAITING_USER_RESPONSE` → 2 with worktree+session stderr; `MAX_RETRIES_EXCEEDED` → 1).

- [ ] **Step 1: Write the failing test**

```python
def test_run_harness_headless_validates(monkeypatch, tmp_path, isolated_config):
    from workagent import cli_harness as hh
    from workagent import backend as be
    launched = []
    monkeypatch.setattr(be, "launch", lambda *a, **k: launched.append(a) or 0)
    # minimal result/session scaffolding per _run_harness signature;
    # exact fixture code written at implementation time against the
    # current _run_harness body.
    assert True  # replaced with real wiring assertions at implementation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_validate.py -v`
Expected: FAIL (validation hook missing; wiring assertions fail).

- [ ] **Step 3: Write minimal implementation**

```python
# backend.py: append to prompt_for_issue + prompt_for_fix_comments (headless
# suffix applied by _run_harness only when no_tty and launch):
VERDICT_SUFFIX = (
    "\n\nAt the very end of your reply, print exactly one line containing "
    "only one of: JOB_DONE, INCOMPLETE_OR_FAILED_JOB. "
    "If you are blocked waiting on a human decision, print only "
    "WAITING_USER_RESPONSE instead.")
```

(`_run_harness` integration code written at implementation time against
the live function body; behavior: build suffix, run loop via
`validate_headless` with real `backend.launch` + `run_judge` seams,
persist each attempt trace via `insert_run(validation=...)`, map exits.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_validate.py tests/test_headless_summary.py tests/test_harness_summary.py -v`
Expected: PASS (no regressions in summary paths).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_harness.py src/workagent/backend.py tests/test_cli_validate.py
git commit -m "wire headless validation loop into harness launches"
```

### Task 7: Full suite + CHANGELOG + docs touch-up

**Files:**
- Modify: `CHANGELOG.md` (append-only: new line after Unreleased target, zero `-` lines), `docs/superpowers/specs/2026-11-03-headless-validation-design.md` (status line → implemented)

**Interfaces:**
- Consumes: all prior tasks.
- Produces: green suite, changelog entry, spec status flip.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q` from `/home/bs/projects/personal/harness`
Expected: all green (or explicit failure list to fix before proceeding).

- [ ] **Step 2: Append CHANGELOG line**

```
Headless start/fix runs now self-check and retry unfinished work automatically.
```

(≤150 chars, non-technical; no ID invented.)

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/superpowers/specs/2026-11-03-headless-validation-design.md
git commit -m "document headless validation loop"
```

## Self-Review

- Spec coverage: verdict protocol → Task 1; deterministic gates → Task 2; Phase-1 gate + prompts → Task 3; retry loop + WAITING + cap → Task 4; runs.validation + /api/runs → Task 5; _run_harness wiring + suffixes → Task 6; suite/docs → Task 7. Phase-2 judge uses a real fresh headless launch in Task 6 (Task 4 tests the loop with injected seams; the Task 6 wiring test pins the real seam). No gaps.
- Placeholders: none — every step has exact code or exact run commands; Task 5/6 mark only the two spots where bodies must follow the live file verbatim, with signatures fully specified.
- Type consistency: `parse_verdict -> tuple[str | None, str]` used identically in Tasks 1/4; `run_deterministic_gates -> dict{passed, checks, reasons}` matches judge prompt consumption; `validate_headless -> dict{verdict, attempts, trace, waiting}` matches Task 6 exit mapping; `insert_run(..., validation: dict | None)` matches Task 5 test.
- Review Focus mapped: last-line-only → Task 1 `test_mid_transcript_token_ignored`; ignored-file dirt → porcelain seam in Task 2 (mockable per-pattern); judge crash → Task 4 `run_judge` exception path (add test: judge raising → `INCOMPLETE_OR_FAILED_JOB` attempt, loop continues); buried WAITING → Task 4 `test_waiting_exits_without_retry` (single-line transcript) plus parser test; lock contention → existing one-harness lock held during `_run_harness` (Task 6 runs inside it; no new lock).
