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
                            transcript: str,
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
    has_text = bool((transcript or "").strip())
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


def validate_headless(*, worktree: str, branch: str, prompt: str,
                       session_type: str | None,
                       run_once, run_judge,
                       max_retries: int = 3) -> dict:
    """Retry loop. run_once(prompt)->{exit_code, transcript}; run_judge(prompt)->text.

    Order per attempt: run_once -> WAITING short-circuit -> exit-code gate
    (non-zero = failed attempt, no judge spent) -> Phase-1 dirty gate for
    start/fix_comments (dirty = continuation via run_once(continue_prompt);
    INTENTIONAL accepts, clean proceeds) -> Phase-2 deterministic gates
    (fail = retry without judge) -> judge. Never raises on judge failure
    (counts as a failed attempt with reasons). WAITING_USER_RESPONSE exits
    without consuming a retry. expect_changes is True for start/fix_comments
    (code-change sessions) and False otherwise.
    """
    expect_changes = session_type in ("start", "fix_comments")
    trace: list[dict] = []
    attempts = 0
    current_prompt = prompt
    while True:
        attempts += 1
        try:
            out = run_once(current_prompt) or {}
        except Exception as e:
            out = {"exit_code": 1, "transcript": f"harness error: {e}"}
        exit_code = out.get("exit_code", 0)
        try:
            exit_code = int(exit_code)
        except (TypeError, ValueError):
            exit_code = 1
        transcript = str(out.get("transcript", "") or "")
        tail = transcript[-2000:]
        token, mode = parse_verdict(transcript)
        if token == "WAITING_USER_RESPONSE":
            trace.append({"attempt": attempts, "phase": "main",
                          "verdict": token, "parse": mode,
                          "tail": tail})
            return {"verdict": token, "attempts": attempts,
                    "trace": trace, "waiting": True}

        def _retry(phase: str, verdict: str, reasons: list[str],
                   next_prompt: str) -> dict | None:
            """Append a failed-attempt trace entry; terminal verdict or None."""
            trace.append({"attempt": attempts, "phase": phase,
                          "verdict": verdict, "parse": mode,
                          "reasons": reasons, "tail": tail})
            if attempts > max_retries:
                trace.append({"attempt": attempts, "phase": "terminal",
                              "verdict": "MAX_RETRIES_EXCEEDED",
                              "parse": "none", "tail": ""})
                return {"verdict": "MAX_RETRIES_EXCEEDED",
                        "attempts": attempts, "trace": trace,
                        "waiting": False}
            return None

        if exit_code != 0:
            done = _retry("gates", "INCOMPLETE_OR_FAILED_JOB",
                          [f"harness exit code {exit_code}: {tail[-500:]}"],
                          (f"Continue the unfinished job. The previous run "
                           f"exited with code {exit_code}. Stderr/output tail:\n"
                           f"{tail[-2000:]}\n"
                           f"End your reply with only JOB_DONE as the last line."))
            if done is not None:
                return done
            current_prompt = (f"Continue the unfinished job. The previous run "
                              f"exited with code {exit_code}. Stderr/output tail:\n"
                              f"{tail[-2000:]}\n"
                              f"End your reply with only JOB_DONE as the last line.")
            continue
        if (session_type in ("start", "fix_comments")
                and token == "INTENTIONALLY_UNCOMMITTED_CHANGES"):
            trace.append({"attempt": attempts, "phase": "dirty-gate",
                          "verdict": token, "parse": mode,
                          "tail": tail})
            return {"verdict": token, "attempts": attempts,
                    "trace": trace, "waiting": False}
        if session_type in ("start", "fix_comments"):
            try:
                dirty = dirty_gate(worktree, branch)
            except Exception as e:
                dirty = {"dirty": False, "porcelain": "",
                         "error": f"dirty gate error: {e}"}
            if dirty.get("dirty"):
                porcelain = str(dirty.get("porcelain", "") or "")
                try:
                    cont = run_once(continue_prompt(porcelain, branch)) or {}
                except Exception as e:
                    cont = {"exit_code": 1,
                            "transcript": f"harness error: {e}"}
                ctranscript = str(cont.get("transcript", "") or "")
                ctoken, cmode = parse_verdict(ctranscript)
                if ctoken == "WAITING_USER_RESPONSE":
                    trace.append({"attempt": attempts, "phase": "dirty-gate",
                                  "verdict": ctoken, "parse": cmode,
                                  "tail": ctranscript[-2000:]})
                    return {"verdict": ctoken, "attempts": attempts,
                            "trace": trace, "waiting": True}
                if (ctoken == "INTENTIONALLY_UNCOMMITTED_CHANGES"
                        and session_type in ("start", "fix_comments")):
                    trace.append({"attempt": attempts, "phase": "dirty-gate",
                                  "verdict": ctoken, "parse": cmode,
                                  "tail": ctranscript[-2000:]})
                    return {"verdict": ctoken, "attempts": attempts,
                            "trace": trace, "waiting": False}
                try:
                    recheck = dirty_gate(worktree, branch)
                except Exception as e:
                    recheck = {"dirty": True,
                               "porcelain": f"dirty gate error: {e}"}
                transcript = ctranscript
                tail = transcript[-2000:]
                token, mode = ctoken, cmode
                if recheck.get("dirty"):
                    done = _retry(
                        "dirty-gate", "INCOMPLETE_OR_FAILED_JOB",
                        [f"uncommitted changes remain: "
                         f"{str(recheck.get('porcelain', ''))[-500:]}"],
                        (f"Continue the unfinished job. Uncommitted changes "
                         f"remain:\n{str(recheck.get('porcelain', ''))[-2000:]}\n"
                         f"Commit and push to origin/{branch}, then end your "
                         f"reply with only JOB_DONE as the last line."))
                    if done is not None:
                        return done
                    current_prompt = (
                        f"Continue the unfinished job. Uncommitted changes "
                        f"remain:\n{str(recheck.get('porcelain', ''))[-2000:]}\n"
                        f"Commit and push to origin/{branch}, then end your "
                        f"reply with only JOB_DONE as the last line.")
                    continue
        try:
            gates = run_deterministic_gates(worktree, exit_code,
                                            transcript[-4000:],
                                            expect_changes)
        except Exception as e:
            gates = {"passed": False, "checks": {},
                     "reasons": [f"deterministic gates crashed: {e}"]}
        if not gates.get("passed", False):
            reasons = [str(r) for r in gates.get("reasons", [])]
            done = _retry("gates", "INCOMPLETE_OR_FAILED_JOB", reasons,
                          (f"Continue the unfinished job. Deterministic gates "
                           f"failed: {'; '.join(reasons)[:2000]}\n"
                           f"End your reply with only JOB_DONE as the last line."))
            if done is not None:
                return done
            current_prompt = (
                f"Continue the unfinished job. Deterministic gates failed: "
                f"{'; '.join(reasons)[:2000]}\n"
                f"End your reply with only JOB_DONE as the last line.")
            continue
        diff_stat = ""
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
        trace.append({"attempt": attempts, "phase": "judge",
                      "verdict": jtoken or "INCOMPLETE_OR_FAILED_JOB",
                      "parse": jmode, "tail": tail})
        if jtoken == "JOB_DONE":
            return {"verdict": "JOB_DONE", "attempts": attempts,
                    "trace": trace, "waiting": False}
        if attempts > max_retries:
            return {"verdict": "MAX_RETRIES_EXCEEDED", "attempts": attempts,
                    "trace": trace, "waiting": False}
        current_prompt = (f"Continue the unfinished job. The validation judge "
                          f"reported: {(judge_text or '')[-2000:]}\n"
                          f"End your reply with only JOB_DONE as the last line.")
