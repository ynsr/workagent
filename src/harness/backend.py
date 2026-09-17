"""Backend launcher: run the configured AI harness (omp for v1)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .errors import HarnessError


def launch(harness: str, prompt: str, workdir: str, no_tty: bool,
           extra_args: list[str] | None = None) -> int:
    """Exec the harness in the worktree. Returns its exit code.

    TTY mode: replaces this process (os.execvp) so the user gets a real
    interactive session. Non-TTY (--no-tty): runs `omp -p <prompt>` as a
    child and waits.
    """
    if harness != "omp":
        raise HarnessError(f"unsupported harness: {harness} (v1 supports: omp)", exit_code=2)
    if shutil.which("omp") is None:
        raise HarnessError("`omp` not found on PATH")
    argv = ["omp", *(extra_args or []), prompt]
    if no_tty:
        # -p prints-and-exits; --auto-approve skips interactive approval
        # prompts (otherwise the child blocks forever on tool approval).
        argv = ["omp", "-p", "--auto-approve", *(extra_args or []), prompt]
        proc = subprocess.run(argv, cwd=workdir)
        return proc.returncode
    os.execvp("omp", argv)
    return 0  # unreachable; keeps type checkers quiet


def prompt_for_issue(title: str, body: str, issue_ref: str) -> str:
    return f"Work on issue {issue_ref}: {title}\n\n{body}".strip()


def prompt_for_review(pr_url: str) -> str:
    return f"Review this PR/MR using pr-reviewer skill: {pr_url}"
