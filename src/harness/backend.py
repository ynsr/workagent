"""Backend launcher: run the configured AI harness (omp for v1)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .errors import HarnessError


class Runtime:
    """AI harness runtime: argv, launch, transcript-file flag."""
    name: str = ""

    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None) -> list[str]:
        raise NotImplementedError

    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None) -> int:
        raise NotImplementedError

    def session_file_flag(self, path: str) -> list[str]:
        return []


class OmpRuntime(Runtime):
    name = "omp"

    def command_argv(self, prompt, no_tty, extra_args=None):
        if no_tty:
            return ["omp", "-p", "--auto-approve", *(extra_args or []), prompt]
        return ["omp", *(extra_args or []), prompt]

    def launch(self, prompt, workdir, no_tty, extra_args=None):
        argv = self.command_argv(prompt, no_tty, extra_args)
        if shutil.which(argv[0]) is None:
            raise HarnessError(f"`{argv[0]}` not found on PATH")
        if no_tty:
            proc = subprocess.run(argv, cwd=workdir)
            return proc.returncode
        cd_worktree(workdir)
        os.execvp(argv[0], argv)
        return 0  # unreachable; keeps type checkers quiet

    def session_file_flag(self, path: str) -> list[str]:
        # omp resumes/writes the given transcript path: --resume <path>
        # creates it when missing, appends when present (verified).
        return ["--resume", path] if path else []


RUNTIMES: dict[str, Runtime] = {"omp": OmpRuntime()}


def get_runtime(name: str) -> Runtime:
    try:
        return RUNTIMES[name]
    except KeyError:
        raise HarnessError(
            f"unsupported harness: {name} (v1 supports: {', '.join(sorted(RUNTIMES))})",
            exit_code=2) from None


def command_argv(harness: str, prompt: str, no_tty: bool,
                 extra_args: list[str] | None = None) -> list[str]:
    """Full harness argv — shared by launch() and the --no-harness preview."""
    return get_runtime(harness).command_argv(prompt, no_tty, extra_args)


def cd_worktree(workdir: str) -> None:
    """chdir into the worktree in-place.

    TTY mode replaces this process (os.execvp), so the harness inherits the
    cwd only if we change it here — a child-style cwd= argument is impossible.
    """
    try:
        os.chdir(workdir)
    except OSError as e:
        raise HarnessError(f"cannot cd into {workdir}: {e}") from e


def launch(harness: str, prompt: str, workdir: str, no_tty: bool,
           extra_args: list[str] | None = None) -> int:
    """Exec the harness in the worktree. Returns its exit code.

    TTY mode: chdirs into the worktree and replaces this process (os.execvp)
    so the user gets a real interactive session rooted in the worktree.
    Non-TTY (--no-tty): runs `omp -p <prompt>` as a child and waits.
    The full argv is echoed to stderr first so run logs capture it.
    """
    argv = get_runtime(harness).command_argv(prompt, no_tty, extra_args)
    print(f"$ {' '.join(argv)}", file=sys.stderr, flush=True)
    return get_runtime(harness).launch(prompt, workdir, no_tty, extra_args)


def _push_target_lines(worktree: str, branch: str) -> str:
    """Explicit worktree/branch contract so the AI harness pushes to the
    existing branch instead of creating a new one."""
    return (f"\n\nWorktree: {worktree}\nBranch: {branch} — commit here and push "
            f"to origin/{branch}. Never create or push a different branch.")


def prompt_for_issue(title: str, body: str, issue_ref: str,
                     worktree: str = "", branch: str = "") -> str:
    prompt = f"Work on issue {issue_ref}: {title}\n\n{body}".strip()
    if worktree and branch:
        prompt += _push_target_lines(worktree, branch)
    return prompt


def prompt_for_review(pr_url: str, worktree: str = "", branch: str = "") -> str:
    prompt = f"Review this PR/MR using pr-reviewer skill: {pr_url}"
    if worktree and branch:
        prompt += _push_target_lines(worktree, branch)
    return prompt
