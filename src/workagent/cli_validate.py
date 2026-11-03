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


def _match_verdict_line(raw: str) -> tuple[str | None, str]:
    if raw in VERDICT_TOKENS:
        return raw, "exact"
    cleaned = _clean_line(raw).upper()
    for tok in VERDICT_TOKENS:
        if tok in cleaned:
            return tok, "tolerant"
    return None, "none"


def parse_verdict(text: str) -> tuple[str | None, str]:
    """Last non-empty line → (token, mode). No token → (None, "none")."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None, "none"
    token, mode = _match_verdict_line(lines[-1].strip())
    if token is not None:
        return token, mode
    # Trailing fence closer (``` / '''): the verdict may sit inside the
    # block, e.g. "```\nINCOMPLETE_OR_FAILED_JOB\n```". Scan back to the
    # opening marker without looking past it (mid-transcript tokens stay
    # ignored).
    if lines[-1].strip() in ("```", "'''"):
        fence = lines[-1].strip()
        for ln in reversed(lines[:-1]):
            s = ln.strip()
            if s.startswith(fence):
                break
            token, mode = _match_verdict_line(s)
            if token is not None:
                return token, mode
    return None, "none"


def pick_verdict(candidates: list[str]) -> str | None:
    """Highest-priority token present (spec priority order)."""
    present = {c for c in candidates if c in VERDICT_TOKENS}
    for tok in VERDICT_PRIORITY:
        if tok in present:
            return tok
    return None


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


def _unpushed_count(worktree: str) -> int | None:
    """Commits on HEAD not on @{u} (None when no upstream / git fails)."""
    code, out = _git("rev-list", "--count", "@{u}..HEAD", cwd=worktree)
    if code != 0:
        return None
    try:
        return max(0, int(out.strip()))
    except (TypeError, ValueError):
        return None


def _branch_pushed(worktree: str) -> tuple[bool, str]:
    code, head = _git("rev-parse", "HEAD", cwd=worktree)
    if code != 0:
        return False, "no HEAD"
    code, upstream = _git("rev-parse", "@{u}", cwd=worktree)
    if code != 0:
        porcelain = _git_porcelain(worktree)
        return (not porcelain, "no upstream, clean" if not porcelain else "no upstream, dirty tree")
    if head == upstream:
        return True, "in sync"
    ahead = _unpushed_count(worktree)
    if ahead is None:
        code, base = _git("merge-base", "HEAD", "@{u}", cwd=worktree)
        if code != 0:
            return False, "no merge-base with upstream"
        # merge-base==HEAD means strictly behind; anything else diverged.
        return False, ("behind upstream" if base == head else "diverged from upstream")
    if ahead > 0:
        return False, f"ahead of upstream by {ahead} (unpushed)"
    return False, "behind upstream"


def _tool_error_rate(transcript_path: str | None) -> tuple[int, int] | None:
    """(errors, total) over toolResult lines; None when none/unreadable."""
    try:
        from .harness_summary import tool_error_rate as _scan
    except Exception:
        return None
    try:
        return _scan(transcript_path) if transcript_path else None
    except Exception:
        return None



def run_deterministic_gates(worktree: str, exit_code: int,
                            transcript: str,
                            expect_changes: bool,
                            transcript_path: str | None = None) -> dict:
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
    has_text = bool((transcript or "").strip())
    checks["transcript"] = {"ok": has_text,
                            "detail": "non-empty" if has_text else "empty transcript tail"}
    if not has_text:
        reasons.append("empty transcript tail")
    err_rate = _tool_error_rate(transcript_path) if transcript_path else None
    if err_rate is not None and err_rate[1] > 0:
        errors, total = err_rate
        ok = errors * 2 < total
        checks["tool_errors"] = {"ok": ok, "detail": f"{errors}/{total} toolResults errored"}
        if not ok:
            reasons.append(f"tool error rate {errors}/{total} >= 50%")
    else:
        checks["tool_errors"] = {"ok": True, "detail": "no toolResults to score"}
    if expect_changes:
        # Semantics: committed+pushed trees end clean, so the check must not
        # demand uncommitted/unpushed work on top of a verified push — the
        # pushed gate already owns delivery verification. Fail only when
        # there is no sign of work at all (behind/diverged, or clean with
        # nothing delivered).
        ahead = _unpushed_count(worktree)
        stat = _git_diff_stat(worktree)
        dirty = bool(stat.strip())
        if ahead is None:
            # No upstream: clean tree means nothing verifiable was delivered.
            ok = dirty
            detail = stat[:500] if dirty else "no upstream; no uncommitted changes"
        elif pushed and not dirty:
            ok = True
            detail = "pushed and clean; work delivered"
        else:
            ok = ahead > 0 or dirty
            detail = stat[:500] if ok else "behind/diverged; no unpushed or uncommitted changes"
        checks["diff"] = {"ok": ok, "detail": detail}
        if not ok:
            reasons.append("expected code changes but none found (no unpushed commits, no uncommitted changes)")
    else:
        checks["diff"] = {"ok": True, "detail": "no changes expected"}
    return {"passed": not reasons, "checks": checks, "reasons": reasons}



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

def _judge_may_skip(token: str | None, mode: str, gates: dict,
                    expect_changes: bool) -> bool:
    """Cost-cap: skip the LLM judge when evidence is already conclusive.

    Skip only when ALL hold: gates pass, the worker self-attests JOB_DONE
    as an exact token, and (for code-change sessions) the diff check shows
    delivered work. WORKAGENT_ALWAYS_JUDGE=1 forces the old always-judge
    behavior. Skips are recorded as phase "judge-skipped" so the audit
    trace shows the judge was bypassed, not run."""
    import os as _os
    if _os.environ.get("WORKAGENT_ALWAYS_JUDGE") == "1":
        return False
    if token != "JOB_DONE" or mode != "exact":
        return False
    if not gates.get("passed", False):
        return False
    if expect_changes:
        try:
            diff_detail = ((gates.get("checks", {}) or {}).get("diff", {})
                           or {}).get("detail", "")
        except Exception:
            return False
        if "no upstream" in str(diff_detail):
            return False
    return True

def _attempt_store():
    """Session-metadata seam: (load, save) or (None, None) when unavailable."""
    try:
        from . import store_sessions as _ss
        return _ss.get_session, _ss.set_session_metadata
    except Exception:
        return None, None


def validate_headless(*, worktree: str, branch: str, prompt: str,
                      session_type: str | None,
                      run_once, run_judge,
                      max_retries: int = 3,
                      transcript_path: str | None = None,
                      session_db=None, session_id: str | None = None) -> dict:
    """Retry loop. run_once(prompt)->{exit_code, transcript[, transcript_path]}.

    Every run_once call (main attempt or dirty continuation) appends exactly one
    record to ``runs``: {"exit_code", "output", "trace"}, where ``trace`` is the
    decision taken on that run's output. The terminal verdict is stored as
    ``trace["terminal"]`` on the last record; no extra entry is appended.

    ``session_db``/``session_id`` persist ``validation_attempts`` into session
    metadata (best effort, via store_sessions.set_session_metadata) so a
    WAITING resume restarts validation where it stopped. ``run_once`` may
    return ``transcript_path`` to retarget the tool-error scan per attempt.

    Order per attempt: run_once -> WAITING short-circuit -> exit-code gate
    (non-zero = failed attempt, no judge spent) -> Phase-1 dirty gate for
    start/fix_comments (dirty = continuation via run_once(continue_prompt),
    which takes the same WAITING / exit-code / INTENTIONAL checks) -> Phase-2
    deterministic gates (fail = retry without judge) -> judge. Judge failure
    never raises (counts as a failed attempt). WAITING_USER_RESPONSE exits
    without consuming a retry. expect_changes is True for start/fix_comments
    (code-change sessions) and False otherwise.
    """
    expect_changes = session_type in ("start", "fix_comments")
    runs: list[dict] = []
    attempts = 0

    _get_session, _set_meta = _attempt_store()
    if session_db is not None and session_id and _get_session is not None:
        try:
            row = _get_session(session_db, session_id, include_deleted_worktrees=True)
            prior = ((row or {}).get("metadata") or {}).get("validation_attempts", 0)
            attempts = max(0, int(prior))
        except Exception:
            pass

    def _save_attempts() -> None:
        if session_db is None or not session_id or _set_meta is None:
            return
        try:
            _set_meta(session_db, session_id, {"validation_attempts": attempts})
        except Exception:
            pass

    def _run(run_prompt: str) -> tuple[int, str]:
        # Fatal harness errors (missing binary, spawn failure) propagate:
        # callers translate them into documented exit codes and bookkeeping
        # (e.g. review clears the reviewed mark). Only unexpected breakage
        # inside run_once degrades to a failed attempt.
        from .errors import HarnessError as _HarnessError
        try:
            out = run_once(run_prompt) or {}
        except _HarnessError:
            raise
        except Exception as e:
            out = {"exit_code": 1, "transcript": f"harness error: {e}"}
        try:
            code = int(out.get("exit_code", 0))
        except (TypeError, ValueError):
            code = 1
        text = str(out.get("transcript", "") or "")
        path = out.get("transcript_path") or transcript_path
        runs.append({"exit_code": code, "output": text, "trace": None,
                     "transcript_path": path})
        return code, text

    def _decide(phase: str, verdict: str, parse: str, text: str,
                reasons: list[str] | None = None) -> None:
        """Attach this decision to the latest run's record (one per run)."""
        entry = {"attempt": attempts, "phase": phase, "verdict": verdict,
                 "parse": parse, "tail": text[-2000:]}
        if reasons is not None:
            entry["reasons"] = reasons
        runs[-1]["trace"] = entry

    def _finish(verdict: str, waiting: bool = False) -> dict:
        runs[-1]["trace"]["terminal"] = verdict
        _save_attempts()
        return {"verdict": verdict, "attempts": attempts,
                "runs": runs, "waiting": waiting}


    def _retry(phase: str, parse: str, text: str, reasons: list[str]) -> dict | None:
        """Counted failed attempt on the latest run; terminal dict once capped."""
        _decide(phase, "INCOMPLETE_OR_FAILED_JOB", parse, text, reasons)
        return _finish("MAX_RETRIES_EXCEEDED") if attempts > max_retries else None

    def _exit_prompt(code: int, transcript: str) -> str:
        return (f"Continue the unfinished job. The previous run "
                f"exited with code {code}. Stderr/output tail:\n"
                f"{transcript[-2000:]}\n"
                f"End your reply with only JOB_DONE as the last line.")

    current_prompt = prompt
    while True:
        attempts += 1
        code, transcript = _run(current_prompt)
        token, mode = parse_verdict(transcript)
        if token == "WAITING_USER_RESPONSE":
            _decide("main", token, mode, transcript)
            return _finish(token, waiting=True)
        if code != 0:
            done = _retry("gates", mode, transcript,
                          [f"harness exit code {code}: {transcript[-500:]}"])
            if done is not None:
                return done
            current_prompt = _exit_prompt(code, transcript)
            continue
        if expect_changes and token == "INTENTIONALLY_UNCOMMITTED_CHANGES":
            _decide("dirty-gate", token, mode, transcript)
            return _finish(token)
        if expect_changes:
            try:
                dirty = dirty_gate(worktree, branch)
            except Exception as e:
                dirty = {"dirty": False, "porcelain": "",
                         "error": f"dirty gate error: {e}"}
            if dirty.get("dirty"):
                porcelain = str(dirty.get("porcelain", "") or "")
                _decide("dirty-gate", "INCOMPLETE_OR_FAILED_JOB", mode, transcript,
                        [f"uncommitted changes; continuation issued: {porcelain[-500:]}"])
                code, transcript = _run(continue_prompt(porcelain, branch))
                token, mode = parse_verdict(transcript)
                if token == "WAITING_USER_RESPONSE":
                    _decide("dirty-gate", token, mode, transcript)
                    return _finish(token, waiting=True)
                if code != 0:
                    done = _retry("gates", mode, transcript,
                                  [f"harness exit code {code}: {transcript[-500:]}"])
                    if done is not None:
                        return done
                    current_prompt = _exit_prompt(code, transcript)
                    continue
                if token == "INTENTIONALLY_UNCOMMITTED_CHANGES":
                    _decide("dirty-gate", token, mode, transcript)
                    return _finish(token)
                try:
                    recheck = dirty_gate(worktree, branch)
                except Exception as e:
                    recheck = {"dirty": True, "porcelain": f"dirty gate error: {e}"}
                if recheck.get("dirty"):
                    remaining = str(recheck.get("porcelain", "") or "")
                    done = _retry("dirty-gate", mode, transcript,
                                  [f"uncommitted changes remain: {remaining[-500:]}"])
                    if done is not None:
                        return done
                    current_prompt = (
                        f"Continue the unfinished job. Uncommitted changes "
                        f"remain:\n{remaining[-2000:]}\n"
                        f"Commit and push to origin/{branch}, then end your "
                        f"reply with only JOB_DONE as the last line.")
                    continue
        try:
            import inspect as _inspect
            try:
                _takes_path = len(_inspect.signature(run_deterministic_gates).parameters) >= 5
            except (TypeError, ValueError):
                _takes_path = True
            if _takes_path:
                gates = run_deterministic_gates(
                    worktree, code, transcript[-4000:], expect_changes,
                    runs[-1].get("transcript_path"))
            else:
                # Test fakes with the legacy 4-arg shape: same result, no scan.
                gates = run_deterministic_gates(worktree, code, transcript[-4000:],
                                                expect_changes)
        except Exception as e:
            gates = {"passed": False, "checks": {},
                     "reasons": [f"deterministic gates crashed: {e}"]}
        if not gates.get("passed", False):
            reasons = [str(r) for r in gates.get("reasons", [])]
            done = _retry("gates", mode, transcript, reasons)
            if done is not None:
                return done
            current_prompt = (
                f"Continue the unfinished job. Deterministic gates failed: "
                f"{'; '.join(reasons)[:2000]}\n"
                f"End your reply with only JOB_DONE as the last line.")
            continue
        if _judge_may_skip(token, mode, gates, expect_changes):
            _decide("judge-skipped", "JOB_DONE", mode, transcript)
            return _finish("JOB_DONE")
        try:
            checks = gates.get("checks", {}) or {}
            diff_detail = (checks.get("diff", {}) or {}).get("detail", "")
            diff_stat = str(diff_detail or "")
        except Exception:
            diff_stat = ""
        try:
            judge_text = run_judge(judge_prompt(prompt, transcript,
                                                gates, diff_stat))
        except Exception as e:
            judge_text = f"judge crashed: {e}\nINCOMPLETE_OR_FAILED_JOB"
        jtoken, jmode = parse_verdict(judge_text or "")
        _decide("judge", jtoken or "INCOMPLETE_OR_FAILED_JOB", jmode, transcript)
        if jtoken == "JOB_DONE":
            return _finish("JOB_DONE")
        if attempts > max_retries:
            return _finish("MAX_RETRIES_EXCEEDED")
        current_prompt = (f"Continue the unfinished job. The validation judge "
                          f"reported: {(judge_text or '')[-2000:]}\n"
                          f"End your reply with only JOB_DONE as the last line.")
