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
BACKUP_UNIT_NAME = "workagent-backup.service"
BACKUP_TIMER_NAME = "workagent-backup.timer"
BACKUP_INTERVAL = "6h"
#: Timestamped backup files kept (pruned oldest-first by the timer script).
BACKUP_KEEP = 7

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
        "KillMode=process\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "StartLimitBurst=3\n"
        "Environment=NO_COLOR=1\n"
        f"Environment=PATH={path_env}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )

def backup_unit_path(home: Path | None = None) -> Path:
    h = home or Path(os.environ.get("HOME", str(Path.home())))
    return h / ".config" / "systemd" / "user" / BACKUP_UNIT_NAME


def backup_timer_path(home: Path | None = None) -> Path:
    h = home or Path(os.environ.get("HOME", str(Path.home())))
    return h / ".config" / "systemd" / "user" / BACKUP_TIMER_NAME


def render_backup_unit() -> str:
    path_env = _unit_path_env()
    return (
        "[Unit]\n"
        "Description=workagent scheduled backup (actions + flow templates)\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={_serve_bin()} backup run-scheduled --keep {BACKUP_KEEP}\n"
        "Environment=NO_COLOR=1\n"
        f"Environment=PATH={path_env}\n"
    )


def render_backup_timer() -> str:
    return (
        "[Unit]\n"
        "Description=workagent backup every 6 hours\n"
        "\n"
        "[Timer]\n"
        f"OnUnitActiveSec={BACKUP_INTERVAL}\n"
        f"OnBootSec={BACKUP_INTERVAL}\n"
        "Unit=" + BACKUP_UNIT_NAME + "\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _run_systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, check=False,
    )


def _run_journal(*args: str) -> int:
    return subprocess.run(["journalctl", "--user", "-u", "workagent", *args], check=False).returncode


def _write_unit(path: Path, content: str, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(path)
    result["actions"].append(f"wrote {path}")


def install_backup(home: Path | None = None, dry_run: bool = False) -> dict:
    """Write + enable the 6h backup timer (unit + timer, enable --now)."""
    upath, tpath = backup_unit_path(home), backup_timer_path(home)
    result: dict = {"unit": str(upath), "timer": str(tpath),
                    "dry_run": dry_run, "actions": []}
    if dry_run:
        result["actions"] = [f"write {upath}", f"write {tpath}",
                             "systemctl --user daemon-reload",
                             f"systemctl --user enable --now {BACKUP_TIMER_NAME}"]
        return result
    _write_unit(upath, render_backup_unit(), result)
    _write_unit(tpath, render_backup_timer(), result)
    for args in (["daemon-reload"], ["enable", "--now", BACKUP_TIMER_NAME]):
        cp = _run_systemctl(*args)
        result["actions"].append(f"systemctl --user {' '.join(args)} (rc={cp.returncode})")
        if cp.returncode != 0:
            result["error"] = (cp.stderr or cp.stdout).strip()
            return result
    return result


def uninstall_backup(home: Path | None = None) -> dict:
    """Disable + remove the backup timer and unit."""
    actions = []
    cp = _run_systemctl("disable", "--now", BACKUP_TIMER_NAME)
    actions.append(f"systemctl --user disable --now {BACKUP_TIMER_NAME} (rc={cp.returncode})")
    for p in (backup_timer_path(home), backup_unit_path(home)):
        if p.exists():
            p.unlink()
            actions.append(f"removed {p}")
        else:
            actions.append(f"{p} already absent")
    cp = _run_systemctl("daemon-reload")
    actions.append(f"systemctl --user daemon-reload (rc={cp.returncode})")
    return {"actions": actions}


def install(host: str, port: int, yes: bool, dry_run: bool = False,
            home: Path | None = None, backup: bool = True) -> dict:
    """Write unit, daemon-reload, enable --now, ensure linger (+ backup timer)."""
    path = unit_path(home)
    content = render_unit(host, port)
    result: dict = {"unit": str(path), "host": host, "port": port,
                    "dry_run": dry_run, "actions": []}
    if dry_run:
        result["actions"] = [f"write {path}", "systemctl --user daemon-reload",
                             "systemctl --user enable --now workagent",
                             f"loginctl enable-linger {os.environ.get('USER', '$USER')}"]
        if backup:
            result["actions"].extend(install_backup(home, dry_run=True)["actions"])
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
    if backup:
        bout = install_backup(home)
        result["actions"].extend(bout["actions"])
        if bout.get("error"):
            result["error"] = bout["error"]
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
    bout = uninstall_backup(home)
    actions.extend(bout["actions"])
    if path.exists():
        path.unlink()
        actions.append(f"removed {path}")
    else:
        actions.append(f"{path} already absent")
    cp = _run_systemctl("daemon-reload")
    actions.append(f"systemctl --user daemon-reload (rc={cp.returncode})")
    return {"unit": str(path), "actions": actions}
