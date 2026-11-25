"""Backend launcher: run the configured AI harness."""

from __future__ import annotations

import abc
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .errors import HarnessError, HarnessTimeout

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


def resolve_model_effort(harness: str | None = None,
                         model: str | None = None,
                         effort: str | None = None) -> tuple[str | None, str | None]:
    """Fall back to configured default_model/default_effort when targeting default_harness."""
    from . import store as _store
    def_h = _store.default_harness()
    eff_h = harness or def_h
    if eff_h == def_h:
        if not model:
            model = _store.default_model() or None
        if not effort:
            effort = _store.default_effort() or None
    return model, effort


def command_argv(harness: str | None = None, prompt: str = "", no_tty: bool = False,
                 extra_args: list[str] | None = None,
                 model: str | None = None, effort: str | None = None) -> list[str]:
    """Full harness argv — shared by launch() and the preview (no --launch)."""
    model, effort = resolve_model_effort(harness, model, effort)
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
    model, effort = resolve_model_effort(harness, model, effort)
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
