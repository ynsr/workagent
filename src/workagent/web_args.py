"""Arg specs + validation/argv builders (from webapp.py).

Leaf module: stdlib + pydantic only. Imported by webapp (endpoints) and
web_runs (registry summaries).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── command inventory (mirrors web/FEATURE_INVENTORY.md) ──────────────
# subcommand → {confirm: destructive?, force flag?, target key builder}
SPECS: dict[str, dict[str, Any]] = {
    "start": {"confirm": True, "force": False, "key": "start"},
    "review": {"confirm": True, "force": False,
               "key": lambda args: "review:all" if "--all" in args
               else f"review:{_first_positional(args, VAL_FLAGS['review'])}"},
    "cleanup": {"confirm": True, "force": True,
                "key": lambda args: "cleanup:all" if "--merged" in args
                else f"cleanup:{_first_positional(args)}"},
    "sync": {"confirm": True, "force": False,
             "key": lambda args: "sync:all" if "--all" in args
             else f"sync:{_first_positional(args, VAL_FLAGS['sync'])}"},
    "open": {"confirm": False, "force": False,
             "key": lambda args: f"open:{_first_positional(args)}"},
    "register": {"confirm": False, "force": True,
                 "key": lambda args: f"register:{_first_positional(args, VAL_FLAGS['register'])}"},
    "repo": {"confirm": False, "force": True, "key": "config"},
    "link": {"confirm": False, "force": False, "key": "config"},
    "tracker": {"confirm": False, "force": False, "key": "config"},
}

BOOL_FLAGS: dict[str, tuple[str, ...]] = {
    "start": ("-N", "--no-tty", "--no-runtime", "--dry-run", "--yes",
              "--json"),
    "review": ("-N", "--no-tty", "--no-runtime", "--dry-run", "--yes",
               "--json", "--all", "--sequential", "--fix", "--fix-comments", "--force-all", "--post-comments"),
    "cleanup": ("--force", "--yes", "--dry-run", "--json", "--merged", "--no-squash"),
    "open": (),
    "sync": ("-m", "--merge", "--harness", "--all", "--yes", "--force",
             "-y", "--dry-run", "--json"),
    "register": ("--yes", "-y", "--force", "--json"),
    "repo add": ("--json",),
    "repo list": ("--json", "--csv"),
    "link set": ("--json",),
    "link remove": ("--json",),
    "link list": ("--worktree", "--refresh-pr", "--json", "--csv"),
    "tracker add": ("--json",),
    "repo remove": ("--json", "--force"),
    "tracker remove": ("--json",),
    "tracker list": ("--json", "--csv"),
}
VAL_FLAGS: dict[str, tuple[str, ...]] = {
    "start": ("--repo", "--depth", "--base", "--harness", "--session-file"),
    "review": ("--repo", "--depth", "--harness", "--session-file"),
    "sync": ("--session-file",),
    "register": ("--key", "--issue", "--repo"),
    "repo add": ("--name", "--path", "--tracker"),
    "link remove": ("--repo",),
    "tracker add": ("--vendor", "--remote-url"),
}
SUBCOMMANDS: dict[str, set[str]] = {"repo": {"add", "list", "remove"},
                                    "link": {"list", "set", "remove"},
                                    "tracker": {"add", "list", "remove"}}


def _first_positional(args: list[str], vals: tuple[str, ...] = ()) -> str:
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in vals:
            skip_next = True
            continue
        if a.startswith("--") and "=" in a:
            name = a.split("=", 1)[0]
            if name in vals:
                continue
        if not a.startswith("-"):
            return a
    return ""


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    args: list[str] = Field(default_factory=list)
    confirm: bool = False
    force: bool = False


def _sub_of(command: str, args: list[str]) -> str:
    if command in SUBCOMMANDS and args:
        return f"{command} {args[0]}"
    return command


def _validate_args(command: str, args: list[str], body_force: bool = False) -> None:
    from . import store  # lazy: avoids import cycle at module load
    if command not in SPECS:
        raise ApiError("bad_command", f"command not allowed: {command!r}", 400)
    rest = [a for a in args if a not in ("-v", "--verbose")]
    if command in SUBCOMMANDS and (
            not rest or rest[0] not in SUBCOMMANDS[command]):
        raise ApiError("bad_subcommand",
                       f"{command} needs one of: "
                       f"{', '.join(sorted(SUBCOMMANDS[command]))}", 400)
    sub = _sub_of(command, rest) if command in SUBCOMMANDS else command
    bools = set(BOOL_FLAGS.get(sub, ()))
    vals = set(VAL_FLAGS.get(sub, ()))
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in bools:
            i += 1
        elif a in vals:
            if i + 1 >= len(rest):
                raise ApiError("bad_arg", f"{a} needs a value", 400)
            value = rest[i + 1]
            if value.startswith("-"):
                raise ApiError(
                    "bad_arg",
                    f"value for {a} must not start with '-': {value!r}", 400)
            i += 2
        elif a.startswith("-"):
            hint = " (renamed to '--no-runtime' in #14)" if a == "--no-harness" else ""
            raise ApiError("bad_arg", f"unknown option for {sub}: {a!r}{hint}", 400)
        else:
            i += 1
    if "--all" in rest and "--session-file" in rest:
        raise ApiError("bad_arg",
                       "--session-file cannot be used with --all "
                       "(one transcript per worktree — omit it and each "
                       "launch gets its own file)", 400)
    if sub == "tracker add":
        try:
            uidx = rest.index("add")
        except ValueError:
            uidx = -1
        tail = rest[uidx + 1:] if uidx >= 0 else rest
        if "--remote-url" not in tail:
            raise ApiError("bad_arg", "tracker add requires --remote-url", 400)
        try:
            u = tail[tail.index("--remote-url") + 1]
        except IndexError:
            raise ApiError("bad_arg", "--remote-url needs a value", 400)
        if not u.strip():
            raise ApiError("bad_arg", "--remote-url must be a real tracker web URL", 400)
    if command in ("start", "review"):
        from . import trackers as _trackers
        from . import worktrees as _worktrees
        from . import refs as _refs
        ref = _first_positional(rest, VAL_FLAGS.get(command, ()))
        repo = ""
        for i, a in enumerate(rest):
            if a == "--repo" and i + 1 < len(rest):
                repo = rest[i + 1]
        if ref and not repo:
            try:
                parsed = _refs.parse_ref(ref)
                tid = _trackers.tracker_id(parsed)
            except Exception:
                parsed, tid = None, ""
            pinned = False
            if parsed is not None:
                links = store.load_links()
                if command == "start":
                    try:
                        key = _refs.issue_key(parsed)
                    except Exception:
                        key = ""
                    pinned = bool(key and str((links.get(key) or {}).get("repo", "")))
                else:
                    pinned = isinstance(_worktrees.resolve_worktree(ref, links), str)
            if tid and not pinned:
                known = _trackers.linked_repos(tid)
                if len(known) > 1:
                    raise ApiError(
                        "repo_ambiguous",
                        f"tracker {tid} is linked to multiple repos: "
                        f"{', '.join(known)}. Re-run with --repo <name|path> to pick one.",
                        400)
    if sub == "repo remove" and not (body_force or "--force" in rest):
        name = next((a for a in rest[1:] if not a.startswith("-")), "")
        if name and store._sqlite_path() is not None:
            from . import store_sqlite as _sq
            n = _sq.worktree_count_for_repo(_sq.db_path(), name)
            if n:
                raise ApiError(
                    "worktrees_exist",
                    f"repo {name} still has {n} linked worktree(s) — "
                    "pass force: true to remove them with the repo", 400)

def _build_argv(command: str, args: list[str]) -> list[str]:
    """Same-code child invocation: this interpreter, `python -m workagent`.

    Global flags (only -v is exposed) precede the subcommand.
    """
    argv = [sys.executable, "-m", "workagent"]
    verbose = any(a in ("-v", "--verbose") for a in args)
    if verbose:
        argv.append("-v")
    argv.append(command)
    argv.extend(a for a in args if a not in ("-v", "--verbose"))
    return argv


def _target_for(command: str, args: list[str]) -> str:
    key = SPECS[command]["key"]
    rest = [a for a in args if a not in ("-v", "--verbose")]
    return key(rest) if callable(key) else key


def _has_session_file(command: str, args: list[str]) -> bool:
    return _session_file_arg(command, args) != ""


def _session_file_arg(command: str, args: list[str]) -> str:
    for i, a in enumerate(args):
        if a == "--session-file" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--session-file="):
            return a.split("=", 1)[1]
    return ""


def _session_file_for(sid: str) -> str:
    from . import store_sqlite as _sq
    try:
        return str(_sq.session_file_path(sid, "omp"))
    except Exception:
        from . import store as _store
        base = _store.config_dir() / "sessions" / "omp"
        base.mkdir(parents=True, exist_ok=True)
        return str(base / f"{sid}.jsonl")
