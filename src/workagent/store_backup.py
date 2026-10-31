"""Backup export/import for custom actions + flow templates (JSON snapshot).

DB stays authoritative; this is the backup story (export → file,
import → replace tables wholesale). Import reuses the existing
create/update validators so a hand-edited file cannot smuggle in
invalid rows.
"""
from __future__ import annotations

import json
from pathlib import Path

BACKUP_VERSION = 1


def export_backup(db) -> dict:
    """Dump actions + templates to a versioned plain-dict snapshot."""
    from . import store_custom_actions as _ca
    from . import store_flows as _fl
    return {
        "version": BACKUP_VERSION,
        "actions": _ca.list_actions(db),
        "flow_templates": _fl.list_templates(db),
    }


def _import_actions(db, rows: list) -> dict:
    from . import store_custom_actions as _ca
    from .errors import HarnessError
    if not isinstance(rows, list):
        raise HarnessError("backup 'actions' must be a list", exit_code=2)
    with _ca._connect(db) as conn:
        conn.execute("DELETE FROM custom_actions")
    created = updated = 0
    for raw in rows:
        if not isinstance(raw, dict):
            raise HarnessError("each action entry must be an object",
                               exit_code=2)
        name = raw.get("name", "")
        label = raw.get("label", "") or ""
        opts = raw.get("default_options", {})
        opts_raw = opts if isinstance(opts, str) else json.dumps(opts or {})
        try:
            _ca.create_action(
                db, name=name, label=label, icon=raw.get("icon", ""),
                prompt=raw.get("prompt", ""),
                pre_execution=raw.get("pre_execution", ""),
                post_execution=raw.get("post_execution", ""),
                default_options=opts_raw)
            created += 1
        except HarnessError as e:
            if "already exists" not in str(e):
                raise
            _ca.update_action(
                db, label or _ca.normalize_label(name),
                name=name, icon=raw.get("icon", ""),
                prompt=raw.get("prompt", ""),
                pre_execution=raw.get("pre_execution", ""),
                post_execution=raw.get("post_execution", ""),
                default_options=opts_raw)
            updated += 1
    return {"created": created, "updated": updated}


def _import_templates(db, rows: list) -> dict:
    from . import store_flows as _fl
    from .errors import HarnessError
    if not isinstance(rows, list):
        raise HarnessError("backup 'flow_templates' must be a list",
                           exit_code=2)
    with _fl._connect(db) as conn:
        conn.execute("DELETE FROM flow_templates")
    created = updated = 0
    for raw in rows:
        if not isinstance(raw, dict):
            raise HarnessError("each template entry must be an object",
                               exit_code=2)
        name = raw.get("name", "")
        label = raw.get("label", "") or ""
        try:
            _fl.create_template(
                db, name=name, label=label, steps=raw.get("steps", []),
                pre_execution=raw.get("pre_execution", ""),
                post_execution=raw.get("post_execution", ""),
                parent_labels=raw.get("parent_labels", []))
            created += 1
        except HarnessError as e:
            if "already exists" not in str(e):
                raise
            _fl.update_template(
                db, label or _fl.normalize_label(name), name=name,
                steps=raw.get("steps", []),
                pre_execution=raw.get("pre_execution", ""),
                post_execution=raw.get("post_execution", ""),
                parent_labels=raw.get("parent_labels", []))
            updated += 1
    return {"created": created, "updated": updated}


def import_backup(db, snapshot: dict) -> dict:
    """Replace actions + templates from a snapshot (validates every row)."""
    from .errors import HarnessError
    if not isinstance(snapshot, dict):
        raise HarnessError("backup file must hold a JSON object",
                           exit_code=2)
    if snapshot.get("version") != BACKUP_VERSION:
        raise HarnessError(
            f"unsupported backup version: {snapshot.get('version')!r} "
            f"(want {BACKUP_VERSION})", exit_code=2)
    actions = _import_actions(db, snapshot.get("actions", []))
    templates = _import_templates(db, snapshot.get("flow_templates", []))
    return {"actions": actions, "flow_templates": templates}


def default_backup_path() -> Path:
    """Config-dir home for backup files (next to state.db)."""
    from . import store_sqlite as _sq
    return _sq.db_path().parent / "backup-actions-flows.json"
