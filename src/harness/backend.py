"""Backend launcher: run the configured AI harness (omp for v1)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .errors import HarnessError


def command_argv(harness: str, prompt: str, no_tty: bool,
                 extra_args: list[str] | None = None) -> list[str]:
    """Full harness argv — shared by launch() and the --no-harness preview."""
    if harness != "omp":
        raise HarnessError(f"unsupported harness: {harness} (v1 supports: omp)", exit_code=2)
    if no_tty:
        # -p prints-and-exits; --auto-approve skips interactive approval
        # prompts (otherwise the child blocks forever on tool approval).
        return ["omp", "-p", "--auto-approve", *(extra_args or []), prompt]
    return ["omp", *(extra_args or []), prompt]


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
    """
    argv = command_argv(harness, prompt, no_tty, extra_args)
    if shutil.which(argv[0]) is None:
        raise HarnessError(f"`{argv[0]}` not found on PATH")
    if no_tty:
        proc = subprocess.run(argv, cwd=workdir)
        return proc.returncode
    cd_worktree(workdir)
    os.execvp(argv[0], argv)
    return 0  # unreachable; keeps type checkers quiet


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
