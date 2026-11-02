"""Env-file sourcing for terminal/headless/interactive harness launches.

Terminal emulators and daemon-spawned headless runs miss the user's login
shell env (VISUAL/EDITOR/NO_PROXY/...). `env_file` (config.json key,
default `<config_dir>/env.sh`) points at a shell script sourced before
every harness command (start/review/fix-comments/sync) and every
repos.commands execution (currently the pre-release gate): `bash -lc
'source <file> && <cmd>'` for shell strings (terminal/resume), and
parsed `KEY=VAL` export for direct-exec launches (subprocess env=).

The default file content is seeded on first launch (ensure_env_file).
"""
from __future__ import annotations
import os
import shlex
import socket
from pathlib import Path

#: Local egress proxy address (xray on :10808 on this workstation). Mirrors
#: the guard block in ~/.config/workagent/env.sh and the egress-proxy.sh
#: reference: direct port-53 DNS is blocked, so external fetches (gh/glab,
#: uv, curl) only work through the proxy. WORKAGENT_PROXY_ADDR/PORT env
#: overrides exist for tests and non-standard machines.
PROXY_ADDR = "http://127.0.0.1:10808"
PROXY_PORT = 10808


def _proxy_listener_up(port: int) -> bool:
    """True when something listens on 127.0.0.1:<port> (egress proxy up)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def ensure_proxy_env(target: dict[str, str] | None = None) -> dict[str, str]:
    """Export http(s)_proxy into *target* when the egress proxy is up.

    Python port of the env.sh guard: sets http_proxy/https_proxy (+ upper
    case, ALL_PROXY/all_proxy) to PROXY_ADDR only when the listener is up
    and no proxy is already set — explicit user config always wins.
    Defaults to os.environ; pass a dict for dry-run/test overlays.
    """
    d = target if target is not None else os.environ
    if d.get("http_proxy") or d.get("HTTP_PROXY"):
        return d
    addr = os.environ.get("WORKAGENT_PROXY_ADDR", PROXY_ADDR)
    try:
        port = int(os.environ.get("WORKAGENT_PROXY_PORT", str(PROXY_PORT)))
    except ValueError:
        port = PROXY_PORT
    if not _proxy_listener_up(port):
        return d
    d["http_proxy"] = addr
    d["https_proxy"] = addr
    d["HTTP_PROXY"] = addr
    d["HTTPS_PROXY"] = addr
    d.setdefault("ALL_PROXY", addr)
    d.setdefault("all_proxy", addr)
    return d

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


_SOURCED_CACHE: dict = {}


def sourced_env(env_file: str | Path | None) -> dict[str, str]:
    """Bash-source *env_file* in a clean shell; return the exported diff.

    Runs `set -a; source <file>; env` so shell logic (conditionals,
    `source ~/.secrets/.env`, proxy guards) evaluates exactly as a login
    shell would — unlike parse_env_file, which only handles static
    `KEY=VAL` lines. Result cached by (path, mtime_ns); failures return {}.
    """
    if not env_file:
        return {}
    import subprocess
    try:
        p = Path(str(env_file)).expanduser()
        if not p.is_file():
            return {}
        mtime = p.stat().st_mtime_ns
    except OSError:
        return {}
    key = str(p)
    hit = _SOURCED_CACHE.get(key)
    if hit and hit[0] == mtime:
        return dict(hit[1])
    import shlex as _shlex
    try:
        proc = subprocess.run(
            ["bash", "-c", f"set -a; source {_shlex.quote(str(p))} >/dev/null 2>&1; exec env -0"],
            capture_output=True, timeout=10, env=dict(os.environ),
        )
    except Exception:
        return dict(hit[1]) if hit else {}
    if proc.returncode != 0:
        return dict(hit[1]) if hit else {}
    raw = getattr(proc, "stdout", b"")
    if isinstance(raw, str):
        # Test fakes (monkeypatched subprocess.run) return str stdout —
        # not real sourced output. Fall back to the static parser so
        # mocked runs still see KEY=VAL lines without executing bash.
        return dict(parse_env_file(p))
    if not isinstance(raw, (bytes, bytearray)):
        return dict(hit[1]) if hit else {}
    before = dict(os.environ)
    out: dict[str, str] = {}
    for chunk in bytes(raw).split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        try:
            text = chunk.decode("utf-8", "replace")
        except Exception:
            continue
        k, _, v = text.partition("=")
        if not k or before.get(k) == v:
            continue
        out[k] = v
    _SOURCED_CACHE[key] = (mtime, dict(out))
    return dict(out)


def apply_env_file(env_file: str | Path | None = "__default__") -> dict[str, str]:
    """Source the env file into this process (os.environ); return applied.

    Single choke point for "env before literally every command": sourcing
    once at startup means every later subprocess (git/jira/gh/glab/uv)
    inherits the vars without per-call wiring. `"__default__"` resolves
    via resolve_env_file(); explicit path/None pass through. Never
    clobbers an explicitly-exported var — sourced values fill gaps only,
    matching the proxy-guard's explicit-wins rule. Resolves through
    WORKAGENT_CONFIG_DIR like everything else; safe to call repeatedly
    (mtime cache, best-effort — never raises).
    """
    try:
        target = resolve_env_file(None if env_file == "__default__" else env_file)
        if target is None:
            ensure_proxy_env()
            return {}
        applied: dict[str, str] = {}
        for k, v in sourced_env(target).items():
            if k not in os.environ:
                os.environ[k] = v
                applied[k] = v
        ensure_proxy_env()
        return applied
    except Exception:
        return {}


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
        # One line may hold several assignments
        # (`export A="x" B="y"` in the proxy guard). Split on KEY=
        # boundaries so quoted values with spaces/semicolons
        # (`"${VAR:-code --wait}"`, `a;b,c`) stay intact — a plain
        # whitespace split would shred them.
        bounds = [m.start() for m in
                  re.finditer(r"[A-Za-z_][A-Za-z0-9_]*=", line)]
        if not bounds:
            continue
        spans = (bounds[1:] + [len(line)])
        for start, end in zip(bounds, spans):
            part = line[start:end].strip().rstrip(";").strip()
            if "=" not in part:
                continue
            key, _, val = part.partition("=")
            key = key.strip()
            v = val.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            # ${VAR:-default} → env value or default (bash semantics for set-or-not)
            def _sub_default(m: re.Match) -> str:
                name, default = m.group(1), m.group(2)
                current = os.environ.get(name, "")
                local = out.get(name, "")
                return current or local or default
            v = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}", _sub_default, v)
            v = os.path.expandvars(v)
            out[key] = v
    return out


def launch_env(env_file: str | Path | None) -> dict[str, str]:
    """Environ overlay for headless child launches (bash-sourced env file)."""
    if not env_file:
        return {}
    try:
        if not Path(str(env_file)).expanduser().is_file():
            return {}
    except OSError:
        return {}
    out = dict(parse_env_file(env_file))
    out.update(sourced_env(env_file) or {})
    return out


def merged_env(env_file: str | Path | None) -> dict[str, str]:
    """os.environ overlaid with the sourced env file (empty when none).

    Single choke point for every direct-exec child (headless harness,
    pre-release gate, and any future repos.commands runner): parse the
    file once here instead of reimplementing the merge per caller.
    The egress proxy guard applies too, so direct-exec children inherit
    working external fetches even when the serve/CLI process env lacks it.
    Explicitly-exported vars always win (sourced values fill gaps only).
    """
    merged = ensure_proxy_env(dict(os.environ))
    for k, v in launch_env(env_file).items():
        merged.setdefault(k, v)
    return merged
