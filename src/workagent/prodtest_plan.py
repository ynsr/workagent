"""Prodtest YAML plan reader for the web UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store

DEFAULT_MAX_PLAN_BYTES = 512 * 1024


class PlanPathError(ValueError):
    """Raised when a requested plan path is not a readable YAML file."""


def read_prodtest_plan(
    issue_key: str,
    max_bytes: int = DEFAULT_MAX_PLAN_BYTES,
) -> dict[str, Any]:
    """Read and parse the prodtest YAML plan for *issue_key*.

    Raises ``FileNotFoundError`` when no prodtest item or plan file exists, or
    ``PlanPathError`` if the stored path is not a readable YAML file.
    """
    from .cli_prodtest import _prodtest_key_for_status

    try:
        key = _prodtest_key_for_status(issue_key)
    except Exception:
        key = issue_key
    item = store.get_prodtest(key)
    if item is None:
        raise FileNotFoundError(f"unknown prodtest item: {issue_key!r}")

    plan_path_str = str(item.get("plan_path") or "").strip()
    if not plan_path_str:
        raise FileNotFoundError(f"no plan_path recorded for prodtest {key!r}")

    plan_path = Path(plan_path_str).expanduser()
    if plan_path.suffix.lower() not in (".yaml", ".yml"):
        raise PlanPathError(f"plan_path is not a YAML file: {plan_path_str!r}")
    if not plan_path.is_file():
        raise FileNotFoundError(f"plan file not found on disk: {plan_path_str}")

    with plan_path.open("rb") as fh:
        raw_bytes = fh.read(max_bytes + 1)
    truncated = len(raw_bytes) > max_bytes
    yaml_text = raw_bytes[:max_bytes].decode("utf-8", errors="replace")

    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    if not truncated:
        try:
            import yaml  # type: ignore[import-untyped]
            loaded = yaml.safe_load(yaml_text)
            if isinstance(loaded, dict):
                parsed = loaded
            elif loaded is not None:
                parse_error = "plan YAML root must be a mapping"
        except Exception as exc:
            parse_error = str(exc)

    cases_raw = (parsed or {}).get("cases") if isinstance((parsed or {}).get("cases"), list) else []
    pre_raw = (parsed or {}).get("preconditions") if isinstance((parsed or {}).get("preconditions"), list) else []

    return {
        "issue_key": key,
        "status": str(item.get("status") or ""),
        "plan_path": str(plan_path),
        "plan_revision": item.get("plan_revision"),
        "plan_risk": str(item.get("plan_risk") or (parsed or {}).get("risk") or ""),
        "plan_diff_hash": str(item.get("plan_diff_hash") or (parsed or {}).get("diff_hash") or ""),
        "release_sha": str(item.get("release_sha") or ""),
        "last_run_id": str(item.get("last_run_id") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "truncated": truncated,
        "yaml": yaml_text,
        "parse_error": parse_error,
        "summary": str((parsed or {}).get("summary") or (parsed or {}).get("title") or ""),
        "preconditions": pre_raw,
        "cases": cases_raw,
        "metadata": {
            k: v
            for k, v in (parsed or {}).items()
            if k not in ("cases", "preconditions") and isinstance(v, (str, int, float, bool, list))
        },
    }
