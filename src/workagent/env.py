"""Env-file sourcing for terminal/headless harness launches.

Terminal emulators and daemon-spawned headless runs miss the user's login
shell env (VISUAL/EDITOR/NO_PROXY/...). `env_file` (config.json key,
default `<config_dir>/env.sh`) points at a shell script sourced before
every harness command: `bash -lc 'source <file> && <cmd>'` for shell
strings (terminal/resume), and parsed `KEY=VAL` export for direct-exec
headless launches (subprocess env=).

The default file content is seeded on first launch (ensure_env_file).
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

DEFAULT_ENV_CONTENT = """\
export VISUAL="${VISUAL:-code --wait}"
export EDITOR="${EDITOR:-code --wait}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1},chat.jibit.cloud"
export PI_EDIT_VARIANT=hashline
"""

DEFAULT_ENV_NAME = "env.sh"


def default_env_path() -> Path:
    """Default env file location: <config_dir>/env.sh."""
    from . import store as _store
    return _store.config_dir() / DEFAULT_ENV_NAME


def ensure_env_file(path: str | Path | None = None) -> Path:
    """Create the env file with defaults when missing; return its path.

    Never overwrites an existing file (user edits are preserved). Empty
    string/None resolves to the configured default.
    """
    from . import store as _store
    raw = str(path or "") or str(_store.load_config().get("env_file", "") or "")
    target = Path(raw).expanduser() if raw else default_env_path()
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_ENV_CONTENT)
    return target


def resolve_env_file(explicit: str | None = None) -> Path | None:
    """Resolve the effective env file: explicit flag wins, else config.

    Returns None when the configured path is missing (opt-out by
    deleting the file is respected — no auto-create here; creation
    happens in ensure_env_file at launch time).
    """
    from . import store as _store
    if explicit:
        return Path(explicit).expanduser()
    raw = str(_store.load_config().get("env_file", "") or "")
    target = Path(raw).expanduser() if raw else default_env_path()
    return target if target.is_file() else None


def source_prefix(env_file: str | Path | None) -> str:
    """Shell prefix sourcing the env file, or "" when none.

    `source <file> && ` — caller prepends to the harness shell command.
    Quoted for safe embedding in `bash -lc '...'`.
    """
    if not env_file:
        return ""
    return f"source {shlex.quote(str(env_file))} && "


def wrap_command(cmd: str, env_file: str | Path | None) -> str:
    """Prepend env-file sourcing to a shell command string."""
    prefix = source_prefix(env_file)
    return f"{prefix}{cmd}" if prefix else cmd


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse `export KEY=VAL` / `KEY=VAL` lines into a dict.

    Handles `VAR="${VAR:-default}"` by expanding against the current
    environment (same semantics as sourcing for these simple lines).
    `#` comments and blank lines skipped. Values support $VAR/${VAR}/
    ${VAR:-default} expansion via os.path.expandvars after resolving
    the `:-` default form.
    """
    import re
    out: dict[str, str] = {}
    try:
        text = Path(path).expanduser().read_text()
    except OSError:
        return out
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        # ${VAR:-default} → env value or default (bash semantics for set-or-not)
        def _sub_default(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            current = os.environ.get(name, "")
            local = out.get(name, "")
            return current or local or default
        val = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}", _sub_default, val)
        val = os.path.expandvars(val)
        out[key] = val
    return out


def launch_env(env_file: str | Path | None) -> dict[str, str]:
    """Environ overlay for headless child launches (parsed env file)."""
    if not env_file:
        return {}
    try:
        if not Path(str(env_file)).expanduser().is_file():
            return {}
    except OSError:
        return {}
    return parse_env_file(env_file)
