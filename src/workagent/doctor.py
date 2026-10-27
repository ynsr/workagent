"""Install-sync self-check. Mirrors the hashing in install.sh."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _pkg_dir() -> Path:
    return Path(__file__).resolve().parent


def source_hash() -> str:
    pkg = _pkg_dir()
    d = hashlib.sha256()
    for f in sorted(pkg.rglob("*.py")):
        if ".venv" in f.parts:
            continue
        d.update(f.relative_to(pkg).as_posix().encode())
        d.update(f.read_bytes())
    return d.hexdigest()[:12]


def receipt_path() -> Path:
    home = Path(os.environ.get("HOME", str(Path.home())))
    return home / ".local" / "share" / "workagent" / "install-receipt.json"


def source_dir() -> Path:
    # <project>/src/workagent/doctor.py → <project>
    return _pkg_dir().parent.parent


def is_installed_copy() -> bool:
    """True when running from site-packages / pipx venv (not the source tree)."""
    parts = _pkg_dir().parts
    return "site-packages" in parts or ".local/share" in str(_pkg_dir())


def check(json_output: bool = False) -> dict:
    """doctor handler: status ok|stale|missing; caller decides the exit code."""
    import shutil
    import sys
    tools = {t: shutil.which(t) is not None for t in ("git-wt", "omp", "gh", "glab", "jira-cli")}
    live = source_hash()
    receipt = receipt_path()
    try:
        recorded = json.loads(receipt.read_text()).get("source_hash", "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        recorded = ""
    status = "ok" if recorded == live else ("missing" if not recorded else "stale")
    result = {"status": status, "live_hash": live, "recorded_hash": recorded,
              "tools": tools, "receipt": str(receipt)}
    from .service import unit_path as _unit_path
    try:
        installed = _unit_path().read_text()
        result["serve_killmode"] = ("ok" if "KillMode=process" in installed
                                    else "legacy")
    except OSError:
        result["serve_killmode"] = "no-unit"
    for tool, ok in tools.items():
        print(f"{tool}: {'found' if ok else 'MISSING'}", file=sys.stderr)
    if status != "ok":
        print(f"doctor: {status} — reinstall with ./install.sh", file=sys.stderr)
    else:
        print("doctor: ok", file=sys.stderr)
    if result.get("serve_killmode") == "legacy":
        print("doctor: serve unit lacks KillMode=process — runs die on "
              "restart; run `workagent service install --yes`", file=sys.stderr)
    return result


def dev_warning() -> str | None:
    if is_installed_copy():
        return None
    receipt = receipt_path()
    try:
        recorded = json.loads(receipt.read_text()).get("source_hash", "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        recorded = ""
    if recorded != source_hash():
        return (
            "warning: running workagent from source tree with a stale/missing install "
            "(run ./install.sh to sync)."
        )
    return None
