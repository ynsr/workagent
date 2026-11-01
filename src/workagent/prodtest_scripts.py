"""Vendored prodtest scripts (pinned copies of the skill scripts)."""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

_FILES = Path(__file__).parent / "prodtest_files"
_SRC = str(Path(__file__).parent.parent)  # src/ dir, for `import workagent` in children


def script(name: str) -> Path:
    return _FILES / name


def _env() -> dict:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _SRC + (os.pathsep + pp if pp else "")
    return env


def run_validate(plan: str, manifest: str | None) -> dict:
    argv = [sys.executable, str(script("validate_plan.py")), plan, "--json"]
    if manifest:
        argv += ["--manifest", manifest]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=_env())
    try:
        return json.loads(proc.stdout or "{}")
    except ValueError:
        return {"valid": proc.returncode == 0, "errors": [proc.stderr.strip() or "validator failed"], "warnings": []}


def render(results_yaml: str) -> str:
    proc = subprocess.run([sys.executable, str(script("render_report.py")), results_yaml], capture_output=True, text=True, timeout=60, env=_env())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "render_report failed")
    return proc.stdout
