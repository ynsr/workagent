"""Parse PRODTEST_*_RESULT payloads: result.json wins, last-line fallback."""
from __future__ import annotations
import json, re
from pathlib import Path

def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s)

def parse_result_line(stdout: str, run_dir: str, prefix: str) -> dict:
    try:
        raw = (Path(run_dir) / "result.json").read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, ValueError):
        pass
    tag = f"{prefix}_RESULT:"
    for line in reversed(_strip_ansi(stdout or "").splitlines()):
        if tag in line:
            return json.loads(line.split(tag, 1)[1].strip())
    raise ValueError(f"no {tag} line and no result.json in {run_dir}")
