"""Errors and subprocess helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: Fixed secrets file parsed (never logged) for `jira` invocations.
_SECRETS_ENV_FILE = ".secrets/.env"

#: Cache of the parsed token: (mtime_ns, value).
_jira_token_cache: tuple = (None, "")


def _jira_api_token() -> str:
    """Read JIRA_API_TOKEN: exported env wins, else ~/.secrets/.env.

    Parsed lazily and cached by mtime; "" when absent/unreadable.
    The value is never printed or logged.
    """
    if os.environ.get("JIRA_API_TOKEN"):
        return os.environ["JIRA_API_TOKEN"]
    global _jira_token_cache
    try:
        mtime = (Path.home() / _SECRETS_ENV_FILE).stat().st_mtime_ns
    except OSError:
        return ""
    cached_mtime, cached_value = _jira_token_cache
    if cached_mtime == mtime:
        return cached_value
    value = ""
    try:
        for raw in (Path.home() / _SECRETS_ENV_FILE).read_text().splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if not line.startswith("JIRA_API_TOKEN"):
                continue
            _, sep, val = line.partition("=")
            if not sep:
                continue
            value = val.strip().strip("'").strip('"')
            break
    except OSError:
        value = ""
    _jira_token_cache = (mtime, value)
    return value


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
    `jira` invocations additionally inherit JIRA_API_TOKEN from
    ~/.secrets/.env when it is not already exported (never logged).
    """
    import sys
    if echo:
        print(f"$ {' '.join(args)}", file=sys.stderr, flush=True)
    env = None
    if args and Path(str(args[0])).name == "jira":
        token = _jira_api_token()
        if token and not os.environ.get("JIRA_API_TOKEN"):
            env = dict(os.environ)
            env["JIRA_API_TOKEN"] = token
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
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
