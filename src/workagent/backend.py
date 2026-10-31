"""Backend launcher: run the configured AI harness (omp for v1)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .errors import HarnessError


class Harness:
    """AI harness: argv, launch, transcript-file flag."""
    name: str = ""

    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None) -> list[str]:
        raise NotImplementedError

    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None,
               env_file: str | None = None) -> int:
        raise NotImplementedError

    def session_file_flag(self, path: str) -> list[str]:
        """Argv fragment routing a transcript path to the harness.

        Fresh (missing/empty) paths MUST NOT use --resume: omp v18+
        requires an existing non-empty transcript there and errors
        (`Session ... not found` / `holds no entries`). Use
        --session-dir on the parent dir instead; omp mints the file.
        Only a non-empty existing transcript resumes via --resume.
        """
        if not path:
            return []
        from pathlib import Path
        try:
            p = Path(path)
            if p.is_file() and p.stat().st_size > 0:
                return ["--resume", path]
        except OSError:
            pass
        return ["--session-dir", str(Path(path).parent)]


class OmpHarness(Harness):
    name = "omp"

    def command_argv(self, prompt, no_tty, extra_args=None):
        if no_tty:
            return ["omp", "-p", "--auto-approve", *(extra_args or []), prompt]
        return ["omp", *(extra_args or []), prompt]

    def launch(self, prompt, workdir, no_tty, extra_args=None, env_file=None):
        from . import env as _env
        argv = self.command_argv(prompt, no_tty, extra_args)
        if shutil.which(argv[0]) is None:
            raise HarnessError(f"`{argv[0]}` not found on PATH")
        if no_tty:
            merged = _env.merged_env(env_file)
            proc = subprocess.run(argv, cwd=workdir, env=merged)
            return proc.returncode
        # TTY exec replaces this process: apply the parsed overlay to our
        # own environ first so the harness inherits it (same values the
        # headless child and the sourced shell command would see).
        os.environ.update(_env.launch_env(env_file))
        cd_worktree(workdir)
        os.execvp(argv[0], argv)
        return 0  # unreachable; keeps type checkers quiet
    def session_file_flag(self, path: str) -> list[str]:
        # Fresh (missing/empty) paths route via --session-dir (omp v18+
        # rejects --resume on those); non-empty transcripts resume.
        return super().session_file_flag(path)


HARNESSES: dict[str, Harness] = {"omp": OmpHarness()}


def get_harness(name: str) -> Harness:
    try:
        return HARNESSES[name]
    except KeyError:
        raise HarnessError(
            f"unsupported harness: {name} (v1 supports: {', '.join(sorted(HARNESSES))})",
            exit_code=2) from None


def command_argv(harness: str, prompt: str, no_tty: bool,
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


def launch(harness: str, prompt: str, workdir: str, no_tty: bool,
           extra_args: list[str] | None = None,
           env_file: str | None = None,
           watch: dict | None = None) -> int:
    """Exec the harness in the worktree. Returns its exit code.

    TTY mode: chdirs into the worktree and replaces this process (os.execvp)
    so the user gets a real interactive session rooted in the worktree
    (env already sourced by the caller into the shell command). With
    *watch* ({"sid": ..., "scope_dir": ..., "before": ...}), exec goes
    through the `watch-and-exec` shim instead: it records the minted
    transcript ASAP, then execs the harness — same UX, signals forwarded
    by exec semantics.
    Non-TTY (--no-tty): runs `omp -p <prompt>` as a child and waits, with
    the env file parsed into the child environment.
    The full argv is echoed to stderr first so run logs capture it.
    """
    argv = get_harness(harness).command_argv(prompt, no_tty, extra_args)
    print(f"$ {' '.join(argv)}", file=sys.stderr, flush=True)
    if watch and not no_tty:
        from . import env as _env
        shim = [sys.executable, "-m", "workagent", "watch-and-exec",
                "--sid", str(watch["sid"]),
                "--scope-dir", str(watch["scope_dir"]),
                "--before", str(watch.get("before") or ""),
                "--", *argv]
        # Shim exec replaces this process: apply the overlay first so the
        # harness inherits it (mirrors OmpHarness.launch TTY path).
        os.environ.update(_env.launch_env(env_file))
        cd_worktree(workdir)
        os.execvp(shim[0], shim)
        return 0  # unreachable; keeps type checkers quiet
    return get_harness(harness).launch(prompt, workdir, no_tty, extra_args,
                                       env_file=env_file)


def _push_target_lines(worktree: str, branch: str) -> str:
    """Explicit worktree/branch contract so the AI harness pushes to the
    existing branch instead of creating a new one."""
    return (f"\n\nWorktree: {worktree}\nBranch: {branch} — commit here and push "
            f"to origin/{branch}. Never create or push a different branch.")


def prompt_for_issue(title: str, body: str, issue_ref: str,
                     worktree: str = "", branch: str = "",
                     related_paths: list[tuple[str, str]] | None = None) -> str:
    prompt = f"Work on issue {issue_ref}: {title}\n\n{body}".strip()
    prompt += ("\n\nAt the end of the session: if there are code changes, "
               "commit, push to this branch, and open a PR/MR to the default "
               "branch. If there are no code changes, do not commit — just report that.")
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
