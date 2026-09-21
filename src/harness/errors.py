"""Errors and subprocess helpers."""

from __future__ import annotations

import subprocess


class HarnessError(Exception):
    """Fatal harness error with a process exit code."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def run_cmd(*args: str, cwd=None, check: bool = True, timeout: int = 60,
            echo: bool = True) -> str | None:
    """Run a command, return stripped stdout. Raises HarnessError on failure.

    The full argv is echoed to stderr first (suppressible via echo=False)
    so CLI output and web run logs capture exactly what ran.
    """
    import sys
    if echo:
        print(f"$ {' '.join(args)}", file=sys.stderr, flush=True)
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise HarnessError(f"command not found: {args[0]}")
    except subprocess.TimeoutExpired:
        raise HarnessError(f"command timed out: {' '.join(args)}")
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise HarnessError(f"{' '.join(args)} failed: {err}" or f"{' '.join(args)} failed")
    out = (proc.stdout or "").strip()
    return out or None
