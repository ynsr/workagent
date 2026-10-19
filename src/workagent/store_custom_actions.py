"""Custom actions store: user-defined agent operations (custom_actions table)."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

#: Default lucide icon when none is given.
DEFAULT_ICON = "Zap"

#: Valid run modes for default_options.run_mode.
RUN_MODES = ("preview", "headless", "terminal")


def _connect(db: Path):
    from .store_sqlite import connect
    from . import store_sqlite as _sq
    _sq.init_db(db)
    return connect(db)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_label(name: str) -> str:
    """Human name → machine label: lower, non-alnum runs → _, strip."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return re.sub(r"_{2,}", "_", slug)


def validate_label(label: str) -> str:
    """Non-blank `^[a-z0-9_]+$` label; raises ValueError otherwise."""
    if not re.fullmatch(r"[a-z0-9_]+", label or ""):
        raise ValueError(
            f"label must match ^[a-z0-9_]+$ (got {label!r})")
    return label or ""


def parse_default_options(raw: str) -> dict:
    """JSON object string → dict; blank → {}; invalid → ValueError."""
    if not (raw or "").strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"default_options is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError("default_options must be a JSON object")
    return parsed


def _row(r: sqlite3.Row | tuple) -> dict:
    if isinstance(r, sqlite3.Row):
        d = dict(r)
    else:
        keys = ("id", "label", "name", "icon", "prompt", "pre_execution",
                "post_execution", "default_options", "created_at",
                "modified_at")
        d = dict(zip(keys, r))
    try:
        d["default_options"] = json.loads(d.get("default_options") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["default_options"] = {}
    return d


def list_actions(db: Path) -> list[dict]:
    """All custom actions ordered by id."""
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [_row(r) for r in conn.execute(
            "SELECT * FROM custom_actions ORDER BY id")]


def get_action(db: Path, label: str) -> dict | None:
    """One action by label; None when unknown."""
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM custom_actions WHERE label = ?",
                         (label,)).fetchone()
        return _row(r) if r is not None else None


def create_action(db: Path, *, name: str, label: str = "",
                  icon: str = "", prompt: str = "",
                  pre_execution: str = "", post_execution: str = "",
                  default_options: str = "") -> dict:
    """Insert a custom action; label defaults to the normalized name."""
    from .errors import HarnessError
    if not (name or "").strip():
        raise HarnessError("action name is required", exit_code=2)
    key = validate_label(label) if (label or "").strip() \
        else validate_label(normalize_label(name))
    if not key:
        raise HarnessError(
            f"name {name!r} normalizes to an empty label — pass --label",
            exit_code=2)
    icon = (icon or "").strip() or DEFAULT_ICON
    if not re.fullmatch(r"[A-Za-z0-9_-]+", icon):
        raise HarnessError(f"icon must be an identifier (got {icon!r})",
                           exit_code=2)
    if not (prompt or "").strip() and not (pre_execution or "").strip():
        raise HarnessError(
            "at least one of --prompt / --pre-execution is required",
            exit_code=2)
    opts = parse_default_options(default_options)
    if not isinstance(opts.get("run_mode", "preview"), str) or \
            opts.get("run_mode", "preview") not in RUN_MODES:
        raise HarnessError(
            f"run_mode must be one of {', '.join(RUN_MODES)}", exit_code=2)
    now = _now_iso()
    with _connect(db) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO custom_actions (label, name, icon, prompt,"
                " pre_execution, post_execution, default_options,"
                " created_at, modified_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, name.strip(), icon, prompt or "", pre_execution or "",
                 post_execution or "", json.dumps(opts), now, now))
        except sqlite3.IntegrityError:
            raise HarnessError(
                f"action label {key!r} already exists", exit_code=2) from None
        row_id = cur.lastrowid
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM custom_actions WHERE id = ?",
                         (row_id,)).fetchone()
        return _row(r)


def update_action(db: Path, label: str, **fields) -> dict:
    """Update mutable fields (name/icon/prompt/pre/post/options); bumps modified_at."""
    from .errors import HarnessError
    allowed = {"name", "icon", "prompt", "pre_execution", "post_execution",
               "default_options"}
    unknown = set(fields) - allowed
    if unknown:
        raise HarnessError(f"unknown fields: {sorted(unknown)}", exit_code=2)
    if "name" in fields and not (fields["name"] or "").strip():
        raise HarnessError("action name must not be blank", exit_code=2)
    if "icon" in fields:
        icon = (fields["icon"] or "").strip() or DEFAULT_ICON
        if not re.fullmatch(r"[A-Za-z0-9_-]+", icon):
            raise HarnessError(f"icon must be an identifier (got {icon!r})",
                               exit_code=2)
        fields["icon"] = icon
    if "default_options" in fields and isinstance(fields["default_options"], str):
        fields["default_options"] = json.dumps(
            parse_default_options(fields["default_options"]))
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM custom_actions WHERE label = ?",
                           (label,))
        existing = cur.fetchone()
        if existing is None:
            raise HarnessError(f"unknown action label: {label!r}",
                               exit_code=2)
        merged = {**dict(existing), **fields}
        if not (merged.get("prompt") or "").strip() \
                and not (merged.get("pre_execution") or "").strip():
            raise HarnessError(
                "at least one of prompt / pre_execution must stay set",
                exit_code=2)
        sets = ", ".join(f"{k} = ?" for k in fields) + ", modified_at = ?"
        vals = [fields[k] if k != "default_options" or isinstance(fields[k], str)
                else json.dumps(fields[k]) for k in fields]
        conn.execute(f"UPDATE custom_actions SET {sets} WHERE label = ?",
                     (*vals, _now_iso(), label))
        r = conn.execute("SELECT * FROM custom_actions WHERE label = ?",
                         (label,)).fetchone()
        return _row(r)


def delete_action(db: Path, label: str) -> None:
    """Delete one action; unknown label → HarnessError exit 2."""
    from .errors import HarnessError
    with _connect(db) as conn:
        cur = conn.execute("DELETE FROM custom_actions WHERE label = ?",
                           (label,))
        if not cur.rowcount:
            raise HarnessError(f"unknown action label: {label!r}",
                               exit_code=2)
