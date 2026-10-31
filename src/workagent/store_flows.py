"""Flow templates + flow runs store (flow_templates / flows tables)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .store_custom_actions import normalize_label, validate_label

STEP_KINDS = ("start", "review", "fix_comments", "sync", "action",
              "cleanup", "release")

FLOW_STATUSES = ("pending", "running", "paused", "failed", "done")

#: Banned substrings in any step config (then-fix-comments shortcut).
_BANNED_STEP_FLAGS = ("then-fix-comments", "then_fix_comments")

#: Banned substrings in cleanup step config (fixed squash + soft-delete).
_BANNED_CLEANUP_FLAGS = ("hard_delete", "hard-delete",
                         "no_squash", "no-squash")


def _connect(db: Path):
    from .store_sqlite import connect
    from . import store_sqlite as _sq
    _sq.init_db(db)
    return connect(db)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config_has_banned(config: object, banned: tuple[str, ...]) -> str | None:
    """Scan config keys + string values for banned substrings."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                for b in banned:
                    if b in str(k):
                        found.append(b)
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            for b in banned:
                if b in node:
                    found.append(b)

    walk(config)
    return found[0] if found else None


def validate_template(steps: list[dict] | str,
                      get_action=None) -> list[dict]:
    """Validate a flow step chain; return normalized steps.

    Raises HarnessError (exit 2) on any ordering/flag/label violation.
    """
    from .errors import HarnessError
    if isinstance(steps, str):
        try:
            steps = json.loads(steps) if steps.strip() else []
        except json.JSONDecodeError as e:
            raise HarnessError(f"steps is not valid JSON: {e}",
                               exit_code=2) from e
    steps = list(steps or [])
    if not steps:
        raise HarnessError("flow template must have at least one step",
                           exit_code=2)
    if not all(isinstance(s, dict) for s in steps):
        raise HarnessError("each flow step must be an object",
                           exit_code=2)
    kinds = [str(s.get("kind", "")) for s in steps]
    for k in kinds:
        if k not in STEP_KINDS:
            raise HarnessError(
                f"unknown step kind {k!r} (expected one of "
                f"{', '.join(STEP_KINDS)})", exit_code=2)
    starts = [i for i, k in enumerate(kinds) if k == "start"]
    if len(starts) > 1:
        raise HarnessError("flow template must have at most one "
                           "'start' step", exit_code=2)
    if starts and starts[0] != 0:
        raise HarnessError("'start' must be the first step",
                           exit_code=2)
    releases = [i for i, k in enumerate(kinds) if k == "release"]
    if len(releases) > 1:
        raise HarnessError("flow template must have at most one "
                           "'release' step", exit_code=2)
    if releases and releases[0] != len(kinds) - 1:
        raise HarnessError("'release' must be the last step",
                           exit_code=2)
    cleanups = [i for i, k in enumerate(kinds) if k == "cleanup"]
    if releases and not cleanups:
        raise HarnessError("'release' requires a prior 'cleanup' step",
                           exit_code=2)
    if releases and cleanups and max(cleanups) > releases[0]:
        raise HarnessError("'release' requires a prior 'cleanup' step",
                           exit_code=2)
    for i, k in enumerate(kinds):
        if k != "review":
            continue
        rest = kinds[i + 1:]
        if "cleanup" not in rest:
            continue
        span = kinds[i + 1:i + 1 + rest.index("cleanup")]
        if "fix_comments" not in span:
            raise HarnessError(
                "review…cleanup requires 'fix_comments' between them",
                exit_code=2)
    for i, (step, k) in enumerate(zip(steps, kinds)):
        if k != "action":
            continue
        if cleanups and i >= min(cleanups):
            raise HarnessError("'action' steps must come before 'cleanup'",
                               exit_code=2)
        label = str(step.get("action_label", "") or "")
        if not label:
            raise HarnessError("'action' step is missing 'action_label'",
                               exit_code=2)
        try:
            validate_label(label)
        except ValueError:
            raise HarnessError(
                f"invalid action_label {label!r}", exit_code=2) from None
        if get_action is not None and get_action(label) is None:
            raise HarnessError(f"unknown action_label {label!r}",
                               exit_code=2)
    for step, k in zip(steps, kinds):
        config = step.get("config", {})
        banned = _config_has_banned(config, _BANNED_STEP_FLAGS)
        if banned:
            raise HarnessError(
                f"{banned!r} is rejected inside flows — use an explicit "
                f"'fix_comments' step", exit_code=2)
        if k == "cleanup":
            banned = _config_has_banned(config, _BANNED_CLEANUP_FLAGS)
            if banned:
                raise HarnessError(
                    f"{banned!r} is rejected in cleanup steps — cleanup "
                    f"is fixed to squash + soft-delete", exit_code=2)
    out = []
    for step, k in zip(steps, kinds):
        norm = {"kind": k, "config": step.get("config", {}) or {}}
        if k == "action":
            norm["action_label"] = step["action_label"]
        out.append(norm)
    return out

def collapse_first_start(steps: list[dict]) -> tuple[list[dict], bool]:
    """Drop every 'start' except the chain's first step; return (chain, dropped)."""
    out: list[dict] = []
    dropped = False
    for i, s in enumerate(steps or []):
        if str(s.get("kind", "")) == "start" and i != 0:
            dropped = True
            continue
        out.append(s)
    return out, dropped


def compose_chains(sources: list[tuple[str, list[dict]]]) -> tuple[list[dict], list[str], bool]:
    """Concatenate (label, steps) pairs; collapse extra starts; index-mapped errors."""
    from .errors import HarnessError
    merged: list[dict] = []
    origin: list[str] = []
    for label, steps in sources:
        for j, s in enumerate(steps or []):
            origin.append(f"{label}:{j + 1}")
            merged.append(dict(s))
    kept: list[dict] = []
    kept_origin: list[str] = []
    dropped = False
    for i, (s, o) in enumerate(zip(merged, origin)):
        if str(s.get("kind", "")) == "start" and i != 0:
            dropped = True
            continue
        kept.append(s)
        kept_origin.append(o)
    try:
        norm = validate_template(kept)
    except HarnessError as e:
        raise HarnessError(
            f"compose: {e} (sources: {', '.join(kept_origin)})",
            exit_code=2) from None
    return norm, [label for label, _ in sources], dropped


def _row(r: sqlite3.Row | tuple) -> dict:
    if isinstance(r, sqlite3.Row):
        d = dict(r)
    else:
        keys = ("id", "label", "name", "steps", "pre_execution",
                "post_execution", "created_at", "modified_at")
        d = dict(zip(keys, r))
    try:
        d["steps"] = json.loads(d.get("steps") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["steps"] = []
    try:
        parents = json.loads(d.get("parent_labels") or "[]")
        d["parent_labels"] = parents if isinstance(parents, list) else []
    except (json.JSONDecodeError, TypeError):
        d["parent_labels"] = []
    return d


def _flow_row(r: sqlite3.Row | tuple) -> dict:
    if isinstance(r, sqlite3.Row):
        d = dict(r)
    else:
        keys = ("id", "template_label", "worktree_ref", "repo_snapshot",
                "status", "current_step", "step_results", "created_at",
                "modified_at")
        d = dict(zip(keys, r))
    for key, default in (("repo_snapshot", "{}"), ("step_results", "[]")):
        try:
            d[key] = json.loads(d.get(key) or default)
        except (json.JSONDecodeError, TypeError):
            d[key] = json.loads(default)
    raw = d.get("effective_steps") or ""
    try:
        eff = json.loads(raw) if str(raw).strip() else None
        d["effective_steps"] = eff if isinstance(eff, list) else None
    except (json.JSONDecodeError, TypeError):
        d["effective_steps"] = None
    return d


def list_templates(db: Path) -> list[dict]:
    """All flow templates ordered by id."""
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [_row(r) for r in conn.execute(
            "SELECT * FROM flow_templates ORDER BY id")]


def get_template(db: Path, label: str) -> dict | None:
    """One flow template by label; None when unknown."""
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM flow_templates WHERE label = ?",
                         (label,)).fetchone()
        return _row(r) if r is not None else None


def create_template(db: Path, *, name: str, label: str = "",
                    steps: str | list = "[]",
                    pre_execution: str = "",
                    post_execution: str = "",
                    parent_labels: list[str] | None = None) -> dict:
    """Insert a flow template; label defaults to the normalized name."""
    from .errors import HarnessError
    if not (name or "").strip():
        raise HarnessError("template name is required", exit_code=2)
    key = validate_label(label) if (label or "").strip() \
        else validate_label(normalize_label(name))
    if not key:
        raise HarnessError(
            f"name {name!r} normalizes to an empty label — pass label",
            exit_code=2)
    norm = validate_template(steps)
    parents = list(parent_labels or [])
    if not all(isinstance(p, str) and p for p in parents):
        raise HarnessError("parent_labels must be a list of label strings",
                           exit_code=2)
    now = _now_iso()
    with _connect(db) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO flow_templates (label, name, steps,"
                " pre_execution, post_execution, parent_labels,"
                " created_at, modified_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, name.strip(), json.dumps(norm), pre_execution or "",
                 post_execution or "", json.dumps(parents), now, now))
        except sqlite3.IntegrityError:
            raise HarnessError(
                f"template label {key!r} already exists",
                exit_code=2) from None
        row_id = cur.lastrowid
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM flow_templates WHERE id = ?",
                         (row_id,)).fetchone()
        return _row(r)


def update_template(db: Path, label: str, **fields) -> dict:
    """Update mutable fields (name/steps/pre/post); bumps modified_at.

    Pass ``new_label`` to rename the template (``^[a-z0-9_]+$``; collisions
    rejected with HarnessError exit 2). ``flows.template_label`` history
    rows are retargeted to the new label in the same connection.
    """
    from .errors import HarnessError
    allowed = {"name", "steps", "pre_execution", "post_execution",
               "parent_labels", "new_label"}
    unknown = set(fields) - allowed
    if unknown:
        raise HarnessError(f"unknown fields: {sorted(unknown)}",
                           exit_code=2)
    if "name" in fields and not (fields["name"] or "").strip():
        raise HarnessError("template name must not be blank", exit_code=2)
    if "steps" in fields:
        norm = validate_template(fields["steps"])
        fields["steps"] = json.dumps(norm)
    if "parent_labels" in fields:
        parents = fields["parent_labels"]
        if isinstance(parents, str):
            try:
                parents = json.loads(parents)
            except json.JSONDecodeError:
                parents = None
        if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
            raise HarnessError("parent_labels must be a list of label strings",
                               exit_code=2)
        fields["parent_labels"] = json.dumps(list(parents))
    new_label = fields.pop("new_label", None)
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT * FROM flow_templates WHERE label = ?",
            (label,)).fetchone()
        if existing is None:
            raise HarnessError(f"unknown template label: {label!r}",
                               exit_code=2)
        target = label
        if new_label is not None and new_label != label:
            try:
                validate_label(new_label)
            except ValueError as e:
                raise HarnessError(str(e), exit_code=2) from None
            clash = conn.execute(
                "SELECT 1 FROM flow_templates WHERE label = ?",
                (new_label,)).fetchone()
            if clash is not None:
                raise HarnessError(
                    f"template label {new_label!r} already exists",
                    exit_code=2)
            fields["label"] = new_label
            target = new_label
        if "name" in fields:
            fields["name"] = fields["name"].strip()
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields) + ", modified_at = ?"
            conn.execute(f"UPDATE flow_templates SET {sets} WHERE label = ?",
                         (*fields.values(), _now_iso(), label))
        else:
            conn.execute("UPDATE flow_templates SET modified_at = ? WHERE label = ?",
                         (_now_iso(), label))
        if target != label:
            conn.execute("UPDATE flows SET template_label = ? WHERE template_label = ?",
                         (target, label))
        r = conn.execute("SELECT * FROM flow_templates WHERE label = ?",
                         (target,)).fetchone()
        return _row(r)


def delete_template(db: Path, label: str) -> None:
    """Delete one template; unknown label → HarnessError exit 2."""
    from .errors import HarnessError
    with _connect(db) as conn:
        cur = conn.execute("DELETE FROM flow_templates WHERE label = ?",
                           (label,))
        if not cur.rowcount:
            raise HarnessError(f"unknown template label: {label!r}",
                               exit_code=2)


def create_flow(db: Path, *, template_label: str, worktree_ref: str = "",
                repo_snapshot: dict | str | None = None) -> dict:
    """Insert a flow run (status=pending, current_step=0)."""
    from .errors import HarnessError
    if not (template_label or "").strip():
        raise HarnessError("template_label is required", exit_code=2)
    snap = repo_snapshot if isinstance(repo_snapshot, str) \
        else json.dumps(repo_snapshot or {})
    now = _now_iso()
    with _connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO flows (template_label, worktree_ref,"
            " repo_snapshot, status, current_step, step_results,"
            " created_at, modified_at)"
            " VALUES (?, ?, ?, 'pending', 0, '[]', ?, ?)",
            (template_label, worktree_ref or "", snap, now, now))
        row_id = cur.lastrowid
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM flows WHERE id = ?",
                         (row_id,)).fetchone()
        return _flow_row(r)


def get_flow(db: Path, flow_id: int,
             include_deleted_worktrees: bool = False) -> dict | None:
    """One flow by id; None when unknown (or its worktree is soft-deleted)."""
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT f.* FROM flows f"
             " LEFT JOIN worktrees w ON w.ref_key = f.worktree_ref"
             " WHERE f.id = ?")
        params: list = [flow_id]
        if not include_deleted_worktrees:
            q += (" AND (f.worktree_ref = '' OR w.ref_key IS NULL"
                  " OR (w.deleted_at IS NULL OR w.deleted_at = ''))")
        r = conn.execute(q, params).fetchone()
        return _flow_row(r) if r is not None else None


def list_flows(db: Path, worktree: str = "",
               include_deleted_worktrees: bool = False) -> list[dict]:
    """All flows (optionally scoped to one worktree).

    Soft-deleted worktrees are hidden unless *include_deleted_worktrees*;
    flows with an empty worktree_ref (pre-Start) are always listed.
    """
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        q = ("SELECT f.* FROM flows f"
             " LEFT JOIN worktrees w ON w.ref_key = f.worktree_ref")
        where = []
        params: list = []
        if worktree:
            where.append("f.worktree_ref = ?")
            params.append(worktree)
        if not include_deleted_worktrees:
            where.append("(f.worktree_ref = '' OR w.ref_key IS NULL"
                         " OR (w.deleted_at IS NULL OR w.deleted_at = ''))")
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY f.id"
        return [_flow_row(r) for r in conn.execute(q, params)]


def update_flow(db: Path, flow_id: int, **fields) -> dict:
    """Update mutable flow fields; bumps modified_at."""
    from .errors import HarnessError
    allowed = {"worktree_ref", "repo_snapshot", "status", "current_step",
               "step_results"}
    unknown = set(fields) - allowed
    if unknown:
        raise HarnessError(f"unknown fields: {sorted(unknown)}",
                           exit_code=2)
    if "status" in fields and fields["status"] not in FLOW_STATUSES:
        raise HarnessError(
            f"status must be one of {', '.join(FLOW_STATUSES)}"
            f" (got {fields['status']!r})", exit_code=2)
    for key in ("repo_snapshot", "step_results"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    with _connect(db) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute("SELECT * FROM flows WHERE id = ?",
                                (flow_id,)).fetchone()
        if existing is None:
            raise HarnessError(f"unknown flow id: {flow_id!r}",
                               exit_code=2)
        sets = ", ".join(f"{k} = ?" for k in fields) + ", modified_at = ?"
        conn.execute(f"UPDATE flows SET {sets} WHERE id = ?",
                     (*fields.values(), _now_iso(), flow_id))
        r = conn.execute("SELECT * FROM flows WHERE id = ?",
                         (flow_id,)).fetchone()
        return _flow_row(r)
