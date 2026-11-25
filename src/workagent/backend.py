"""Backend launcher: run the configured AI harness."""

from __future__ import annotations

import abc
import collections
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from .errors import HarnessError, HarnessTimeout

_EFFORT_UNSUPPORTED_RE = re.compile(
    r"(?:"
    r"(?:--?(?:effort|thinking)|\b(?:effort|thinking|thought_level)\b|reasoning[\s_-]+effort)"
    r"[^\n]{0,80}"
    r"(?:is\s+not\s+supported|not\s+supported|unsupported|does\s+not\s+support|doesn't\s+support)"
    r"|"
    r"(?:does\s+not\s+support|doesn't\s+support|not\s+supported|unsupported)"
    r"[^\n]{0,80}"
    r"(?:--?(?:effort|thinking)|\b(?:effort|thinking|thought_level)\b|reasoning[\s_-]+effort)"
    r")",
    re.IGNORECASE,
)


def is_effort_unsupported_error(text: str) -> bool:
    """True when harness error text indicates the model does not support --effort/--thinking."""
    return bool(text and _EFFORT_UNSUPPORTED_RE.search(text))


def warn_effort_retry(model: str | None, effort: str,
                      retry_argv: list[str] | None = None) -> None:
    target = f"model {model!r}" if model else "selected model"
    print(f"warning: {target} does not support --effort {effort!r}; "
          "retrying without --effort", file=sys.stderr, flush=True)
    if retry_argv:
        quoted = " ".join(shlex.quote(a) for a in retry_argv)
        print(f"$ {quoted}", file=sys.stderr, flush=True)


def start_stderr_tee(stream, max_lines: int = 64) -> tuple[threading.Thread | None, collections.deque[str]]:
    """Forward *stream* lines to sys.stderr while keeping the last *max_lines*."""
    captured: collections.deque[str] = collections.deque(maxlen=max_lines)
    if stream is None:
        return None, captured

    def _pump() -> None:
        try:
            for raw in stream:
                sys.stderr.write(raw)
                sys.stderr.flush()
                line = raw.rstrip("\r\n")
                if line:
                    captured.append(line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t = threading.Thread(target=_pump, name="harness-stderr-tee", daemon=True)
    t.start()
    return t, captured

VERDICT_SUFFIX = (
    "\n\nAt the very end of your reply, print exactly one line containing "
    "only one of: JOB_DONE, INCOMPLETE_OR_FAILED_JOB, "
    "INTENTIONALLY_UNCOMMITTED_CHANGES. INTENTIONALLY_UNCOMMITTED_CHANGES "
    "is only valid when uncommitted changes remain that are intentional "
    "and must stay uncommitted; otherwise use JOB_DONE (done) or "
    "INCOMPLETE_OR_FAILED_JOB (not done). "
    "If you are blocked waiting on a human decision, print only "
    "WAITING_USER_RESPONSE instead.")


def check_binary(binary: str) -> None:
    if shutil.which(binary) is None:
        raise HarnessError(f"`{binary}` not found on PATH")


def run_blocking_cmd(argv: list[str], workdir: str, env_file: str | None = None,
                     timeout_s: int | None = None) -> int:
    from . import env as _env
    merged = _env.merged_env(env_file)
    if timeout_s is None:
        return subprocess.run(argv, cwd=workdir, env=merged).returncode
    proc = subprocess.Popen(argv, cwd=workdir, env=merged, start_new_session=True)
    try:
        proc.wait(timeout=timeout_s)
        return proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        raise HarnessTimeout(f"command timed out: {' '.join(argv)}")


def run_blocking_cmd_tee_stderr(argv: list[str], workdir: str,
                                env_file: str | None = None,
                                timeout_s: int | None = None) -> tuple[int, str]:
    """Run *argv* blocking, streaming stderr to sys.stderr while capturing it."""
    from . import env as _env
    merged = _env.merged_env(env_file)
    proc = subprocess.Popen(
        argv, cwd=workdir, env=merged,
        stderr=subprocess.PIPE, text=True, bufsize=1,
        start_new_session=(timeout_s is not None),
    )
    t, captured = start_stderr_tee(getattr(proc, "stderr", None))
    try:
        if timeout_s is None:
            rc = proc.wait()
        else:
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                raise HarnessTimeout(f"command timed out: {' '.join(argv)}")
    finally:
        if t is not None:
            t.join(timeout=2.0)
    return rc, "\n".join(captured)


def exec_tty(argv: list[str], workdir: str, env_file: str | None = None) -> int:
    from . import env as _env
    # Only the serve unit's NO_COLOR is dropped (marker set by service.py);
    # a NO_COLOR from the user's shell or env.sh (applied below) is kept.
    if os.environ.get("WORKAGENT_SERVICE"):
        os.environ.pop("NO_COLOR", None)
        os.environ.pop("WORKAGENT_SERVICE", None)
    os.environ.update(_env.launch_env(env_file))
    cd_worktree(workdir)
    os.execvp(argv[0], argv)
    return 0


class Harness(abc.ABC):
    """Abstract AI harness interface."""
    name: str = ""
    label: str = ""
    binary: str = ""
    judge_argv: list[str] = []
    watches_headless: bool = True

    @abc.abstractmethod
    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None,
                     model: str | None = None,
                     effort: str | None = None) -> list[str]: ...

    @abc.abstractmethod
    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None,
               env_file: str | None = None,
               timeout_s: int | None = None,
               session_file: str | None = None,
               on_conversation_id: Callable[[str], None] | None = None,
               model: str | None = None,
               effort: str | None = None) -> int: ...

    @abc.abstractmethod
    def session_file_flag(self, path: str, metadata: dict | None = None) -> list[str]: ...

    @abc.abstractmethod
    def resume_argv(self, session_file: str, metadata: dict | None = None,
                    model: str | None = None,
                    effort: str | None = None) -> list[str]: ...

    @abc.abstractmethod
    def bare_terminal_argv(self, scope_dir: str = "",
                           model: str | None = None,
                           effort: str | None = None) -> list[str]: ...

    @abc.abstractmethod
    def watch_transcript(self, workdir: str, scope_dir: Path | None,
                         before: Path | None, run_id: int | None,
                         db: Path, result: dict | None = None,
                         session_file: str = "", launched_at: float = 0.0,
                         stop_event=None) -> None: ...

    @abc.abstractmethod
    def extract_last_assistant_text(self, transcript_path: str,
                                    max_chars: int | None = 4000) -> str: ...

    @abc.abstractmethod
    def tool_error_rate(self, transcript_path: str,
                        since_line: int = 0) -> tuple[int, int] | None: ...

    def discover_options(self, env_file: str | None = None):
        """ACP-discovered model/effort selection (empty for non-ACP)."""
        from .acp_harness import HarnessOptions as _Options
        return _Options()

    def model_effort_cli_args(self, model: str | None,
                              effort: str | None) -> list[str]:
        """Non-ACP CLI flags for --model/--effort (default: none)."""
        return []


HARNESSES: dict[str, Harness] = {}


def _load_harnesses() -> None:
    try:
        from .acp_harness import OmpAcpHarness as _Omp
        if _Omp.name not in HARNESSES:
            HARNESSES[_Omp.name] = _Omp()
    except ImportError:
        # Partial init during the acp_harness import cycle: that module
        # self-registers at its bottom once its classes are defined.
        pass
    try:
        from .agy_harness import AgyAcpHarness as _Agy
        if _Agy.name not in HARNESSES:
            HARNESSES[_Agy.name] = _Agy()
    except ImportError:
        pass


_load_harnesses()


def get_harness(name: str | None = None) -> Harness:
    from . import store as _store
    _load_harnesses()
    key = name or _store.default_harness()
    if key in HARNESSES:
        return HARNESSES[key]
    raise HarnessError(
        f"unknown harness {(name or key)!r} (supported: {', '.join(sorted(HARNESSES))})",
        exit_code=2)


def command_argv(harness: str | None = None, prompt: str = "", no_tty: bool = False,
                 extra_args: list[str] | None = None,
                 model: str | None = None, effort: str | None = None) -> list[str]:
    """Full harness argv — shared by launch() and the preview (no --launch)."""
    return get_harness(harness).command_argv(prompt, no_tty, extra_args,
                                             model=model, effort=effort)


def cd_worktree(workdir: str) -> None:
    """chdir into the worktree in-place.

    TTY mode replaces this process (os.execvp), so the harness inherits the
    cwd only if we change it here — a child-style cwd= argument is impossible.
    """
    try:
        os.chdir(workdir)
    except OSError as e:
        raise HarnessError(f"cannot cd into {workdir}: {e}") from e


def launch(harness: str | None = None, prompt: str = "", workdir: str = "", no_tty: bool = False,
           extra_args: list[str] | None = None, model: str | None = None,
           effort: str | None = None,
           env_file: str | None = None,
           watch: dict | None = None,
           timeout_s: int | None = None,
           session_file: str | None = None,
           on_conversation_id: Callable[[str], None] | None = None) -> int:
    """Exec the harness in the worktree. Returns its exit code.

    TTY mode: chdirs into the worktree and replaces this process (os.execvp)
    so the user gets a real interactive session rooted in the worktree
    (env already sourced by the caller into the shell command). With
    *watch* ({"run_id": ..., "scope_dir": ..., "before": ...}), exec goes
    through the `watch-and-exec` shim instead: it records the minted
    transcript ASAP, then execs the harness — same UX, signals forwarded
    by exec semantics.
    Non-TTY (--no-tty): runs harness in headless mode as a child and waits, with
    the env file parsed into the child environment.
    The full argv is echoed to stderr first so run logs capture it.
    """
    argv = get_harness(harness).command_argv(prompt, no_tty, extra_args,
                                                model=model, effort=effort)
    print(f"$ {' '.join(argv)}", file=sys.stderr, flush=True)
    if watch and not no_tty:
        h_name = get_harness(harness).name
        shim = [sys.executable, "-m", "workagent", "watch-and-exec",
                "--run-id", str(watch["run_id"]),
                "--harness", h_name,
                "--workdir", workdir,
                "--session-file", str(session_file or watch.get("session_file") or ""),
                "--scope-dir", str(watch.get("scope_dir") or ""),
                "--before", str(watch.get("before") or ""),
                "--", *argv]
        # Shim exec replaces this process: apply the overlay first so the
        # harness inherits it (mirrors Harness.launch TTY path).
        return exec_tty(shim, workdir, env_file=env_file)
    return get_harness(harness).launch(prompt, workdir, no_tty, extra_args,
                                       env_file=env_file, timeout_s=timeout_s,
                                       session_file=session_file,
                                       on_conversation_id=on_conversation_id,
                                       model=model, effort=effort)



def _push_target_lines(worktree: str, branch: str) -> str:
    """Explicit worktree/branch contract so the AI harness pushes to the
    existing branch instead of creating a new one."""
    return (f"\n\nWorktree: {worktree}\nBranch: {branch} — commit here and push "
            f"to origin/{branch}. Never create or push a different branch.")


def prompt_for_issue(title: str, body: str, issue_ref: str,
                     worktree: str = "", branch: str = "",
                     related_paths: list[tuple[str, str]] | None = None,
                     plan_mode: bool = False) -> str:
    prompt = f"Work on issue {issue_ref}: {title}\n\n{body}".strip()
    prompt += ("\n\nAt the end of the session: if there are code changes, "
               "commit, push to this branch, and open a PR/MR to the default "
               "branch. If there are no code changes, do not commit — just report that.")
    prompt += ("\nOnly push and open a PR/MR for production-affecting changes "
               "(prod source, docs, or config); for non-prod work (testing released "
               "features/bugs, reports, analysis, ops tasks) do not push or open a PR/MR.")
    if plan_mode:
        prompt += ("\n\n## Plan mode\nNo implementation. Use the brainstorming "
                   "skill to brainstorm and grill the user against the "
                   "request/prompt. After writing the spec and plan MD files, "
                   "push and open a PR/MR. Stop (no implementation).")
    else:
        prompt += ("\nIf any spec/plan files exist in the worktree or repo, "
                   "read and use them before implementing.")
    if worktree and branch:
        prompt += _push_target_lines(worktree, branch)
    if related_paths:
        lines = "\n".join(f"- {name}: {path}" for name, path in related_paths)
        prompt += ("\n\nRelated repo worktrees (apply cross-repo code changes"
                   f" here too):\n{lines}")
    return prompt


def prompt_for_review(pr_url: str, worktree: str = "", branch: str = "") -> str:
    prompt = f"Review this PR/MR using auto-pr-reviewer skill: {pr_url}"
    prompt += ("\n\nAt the end of the session, you MUST post all PR/MR review "
               "comments and inline notes for every issue identified.")
    if worktree and branch:
        prompt += _push_target_lines(worktree, branch)
    return prompt


def prompt_for_fix_comments(pr_url: str, worktree: str = "", branch: str = "") -> str:
    """Apply/fix open PR/MR review findings (issue #32)."""
    prompt = (
        f"Fix all open (not-resolved) review comments on this PR/MR: {pr_url}\n\n"
        "Use the receiving-code-review skill before implementing any feedback.\n\n"
        "Fetch every open/unresolved thread and every plain PR comment that is not "
        "marked resolved. Review each comment and validate it against the code and "
        "the PR/MR description — apply only the findings that hold up; briefly note "
        "any you skip and why.\n\n"
        "Rules:\n"
        "- Resolve/close every comment you fixed or verified as already addressed.\n"
        "- A plain `# Code Review` bot comment counts as resolved when it is "
        "natively marked resolved OR its second non-empty line (directly below the header) matches "
        "`Status:\\s+RESOLVED` (case-insensitive); skip comments already resolved either way and, "
        "for each bot comment you resolve, both resolve it natively (where the host supports it) and "
        "insert `Status: RESOLVED` as the new second line.\n"
        "- After fixing/applying the required changes, commit and push the code.\n\n"
        "At the end of the session, report per comment: fixed, already-addressed, "
        "or skipped with reason — and confirm every thread is resolved or closed."
    )
    if worktree and branch:
        prompt += _push_target_lines(worktree, branch)
    return prompt
