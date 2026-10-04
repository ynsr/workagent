"""User-scoped systemd unit for `workagent serve` (auto-start on login).

Unit: ~/.config/systemd/user/workagent.service, WantedBy=default.target,
Restart=on-failure with StartLimitBurst=3 (up to 3 retries in the interval).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "workagent.service"
DEFAULT_PORT = 3344
DEFAULT_HOST = "127.0.0.1"


def unit_path(home: Path | None = None) -> Path:
    h = home or Path(os.environ.get("HOME", str(Path.home())))
    return h / ".config" / "systemd" / "user" / UNIT_NAME


def _serve_bin() -> str:
    exe = shutil.which("workagent")
    if exe:
        return exe
    return sys.executable + " -m workagent"


def _unit_path_env() -> str:
    """PATH for the serve daemon: install-time PATH so harness/tool

    binaries (omp, gh, glab, ...) resolve the same as in the shell.

    systemd units get a minimal default PATH; without this, `shutil.which`
    checks in backend/gitwt fail for tools installed in user bin dirs
    (~/.bun/bin, linuxbrew, ...).
    """
    return os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")


def render_unit(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    path_env = _unit_path_env()
    return (
        "[Unit]\n"
        "Description=workagent web UI (serve)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={_serve_bin()} serve --host {host} --port {port}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "StartLimitBurst=3\n"
        "Environment=NO_COLOR=1\n"
        f"Environment=PATH={path_env}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _run_systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, check=False,
    )


def _run_journal(*args: str) -> int:
    return subprocess.run(["journalctl", "--user", "-u", "workagent", *args], check=False).returncode


def install(host: str, port: int, yes: bool, dry_run: bool = False,
            home: Path | None = None) -> dict:
    """Write unit, daemon-reload, enable --now, ensure linger."""
    path = unit_path(home)
    content = render_unit(host, port)
    result: dict = {"unit": str(path), "host": host, "port": port,
                    "dry_run": dry_run, "actions": []}
    if dry_run:
        result["actions"] = [f"write {path}", "systemctl --user daemon-reload",
                             "systemctl --user enable --now workagent",
                             f"loginctl enable-linger {os.environ.get('USER', '$USER')}"]
        result["unit_content"] = content
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".service.tmp")
    tmp.write_text(content)
    tmp.replace(path)
    result["actions"].append(f"wrote {path}")
    for args in (["daemon-reload"], ["enable", "--now", "workagent"]):
        cp = _run_systemctl(*args)
        result["actions"].append(f"systemctl --user {' '.join(args)} (rc={cp.returncode})")
        if cp.returncode != 0:
            result["error"] = (cp.stderr or cp.stdout).strip()
            return result
    cp = subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")],
                        capture_output=True, text=True, check=False)
    result["actions"].append(f"loginctl enable-linger (rc={cp.returncode})")
    if cp.returncode != 0:
        result["linger_warning"] = (cp.stderr or cp.stdout).strip() or "linger failed"
    return result


def uninstall(home: Path | None = None) -> dict:
    path = unit_path(home)
    actions = []
    cp = _run_systemctl("disable", "--now", "workagent")
    actions.append(f"systemctl --user disable --now workagent (rc={cp.returncode})")
    if path.exists():
        path.unlink()
        actions.append(f"removed {path}")
    else:
        actions.append(f"{path} already absent")
    cp = _run_systemctl("daemon-reload")
    actions.append(f"systemctl --user daemon-reload (rc={cp.returncode})")
    return {"unit": str(path), "actions": actions}
