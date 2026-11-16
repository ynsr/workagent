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
    os.environ.update(_env.launch_env(env_file))
    cd_worktree(workdir)
    os.execvp(argv[0], argv)
    return 0


class Harness(abc.ABC):
    """Abstract AI harness interface."""
    name: str = ""
    binary: str = ""
    judge_argv: list[str] = []

    @abc.abstractmethod
    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None) -> list[str]: ...

    @abc.abstractmethod
    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None,
               env_file: str | None = None,
               timeout_s: int | None = None,
               session_file: str | None = None,
               on_conversation_id: Callable[[str], None] | None = None) -> int: ...

    @abc.abstractmethod
    def session_file_flag(self, path: str, metadata: dict | None = None) -> list[str]: ...

    @abc.abstractmethod
    def resume_argv(self, session_file: str, metadata: dict | None = None) -> list[str]: ...

    @abc.abstractmethod
    def bare_terminal_argv(self, scope_dir: str = "") -> list[str]: ...

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


class OmpHarness(Harness):
    name = "omp"
    binary = "omp"

    # Bare judge sessions: cheap model, no repo-touching surface. Verified
    # against `omp -h`: each flag exists (--smol bare selects the configured
    # cheap model). Judge prompts carry task + transcript + gates inline.
    judge_argv: list[str] = ["--smol", "--no-skills", "--no-tools",
                             "--no-rules", "--no-context-files",
                             "--no-lsp", "--no-extensions", "--no-pty"]

    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None) -> list[str]:
        if no_tty:
            return ["omp", "-p", "--auto-approve", *(extra_args or []), prompt]
        args = ["omp", *(extra_args or [])]
        if prompt:
            args.append(prompt)
        return args

    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None,
               env_file: str | None = None,
               timeout_s: int | None = None,
               session_file: str | None = None,
               on_conversation_id: Callable[[str], None] | None = None) -> int:
        check_binary(self.binary)
        argv = self.command_argv(prompt, no_tty, extra_args)
        if no_tty:
            return run_blocking_cmd(argv, workdir, env_file=env_file, timeout_s=timeout_s)
        return exec_tty(argv, workdir, env_file=env_file)

    def session_file_flag(self, path: str, metadata: dict | None = None) -> list[str]:
        """Argv fragment routing a transcript path to the harness.

        Fresh (missing/empty) paths MUST NOT use --resume: omp v18+
        requires an existing non-empty transcript there and errors
        (`Session ... not found` / `holds no entries`). Use
        --session-dir on the parent dir instead; omp mints the file.
        Only a non-empty existing transcript resumes via --resume.
        """
        if not path:
            return []
        try:
            p = Path(path)
            if p.is_file() and p.stat().st_size > 0:
                return ["--resume", path]
        except OSError:
            pass
        return ["--session-dir", str(Path(path).parent)]

    def resume_argv(self, session_file: str, metadata: dict | None = None) -> list[str]:
        return ["omp", *self.session_file_flag(session_file, metadata)]

    def bare_terminal_argv(self, scope_dir: str = "") -> list[str]:
        if scope_dir:
            return ["omp", "--session-dir", scope_dir]
        return ["omp"]

    def watch_transcript(self, workdir: str, scope_dir: Path | None,
                         before: Path | None, run_id: int | None,
                         db: Path, result: dict | None = None,
                         session_file: str = "", launched_at: float = 0.0,
                         stop_event=None) -> None:
        if not scope_dir:
            return
        import time
        from . import store_sqlite as _sq
        time.sleep(1.0)
        while stop_event is None or not stop_event.is_set():
            try:
                minted = _sq.newest_transcript(scope_dir)
            except Exception:
                return
            if minted is not None and minted != before:
                if result is not None:
                    result["session_file"] = str(minted)
                if run_id is not None and db is not None:
                    try:
                        _sq.set_run_file_path(db, run_id, str(minted))
                    except Exception:
                        pass
                return
            if stop_event is not None:
                if stop_event.wait(2.0):
                    return
            else:
                time.sleep(2.0)

    def extract_last_assistant_text(self, transcript_path: str,
                                    max_chars: int | None = 4000) -> str:
        from . import harness_summary as _hs
        return _hs.last_assistant_text(transcript_path, max_chars=max_chars)

    def tool_error_rate(self, transcript_path: str,
                        since_line: int = 0) -> tuple[int, int] | None:
        from . import harness_summary as _hs
        return _hs.tool_error_rate(transcript_path, since_line=since_line)


HARNESSES: dict[str, Harness] = {"omp": OmpHarness()}


def _load_harnesses() -> None:
    if "agy" not in HARNESSES:
        try:
            from .agy_harness import AgyHarness
            HARNESSES["agy"] = AgyHarness()
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
        f"unsupported harness: {name or key} (v1 supports: {', '.join(sorted(HARNESSES))})",
        exit_code=2)


def command_argv(harness: str | None = None, prompt: str = "", no_tty: bool = False,
                 extra_args: list[str] | None = None) -> list[str]:
    """Full harness argv — shared by launch() and the preview (no --launch)."""
    return get_harness(harness).command_argv(prompt, no_tty, extra_args)


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
           extra_args: list[str] | None = None,
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
    argv = get_harness(harness).command_argv(prompt, no_tty, extra_args)
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
                                       on_conversation_id=on_conversation_id)



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
