"""Arg specs + validation/argv builders (from webapp.py).

Leaf module: stdlib + pydantic only. Imported by webapp (endpoints) and
web_runs (registry summaries).
"""
from __future__ import annotations

import sys
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── command inventory (mirrors web/FEATURE_INVENTORY.md) ──────────────
# subcommand → {confirm: destructive?, force flag?, target key builder}
def _message_key(args: list[str]) -> str:
    """Target key for `message` runs: per-(subcommand, ref) so concurrent

    message runs never collide on "config" (registry 409s on duplicate
    target). Plain row CRUD (add/list) keeps the shared "config" key;
    agent-spawning runs + show/update/remove key on the message ref
    (`message:<ref>`, `message:action:<ref>`, `message:flow:<ref>` — the
    API_CONTRACT §messages shape, one live run per message).
    """
    if not args:
        return "config"
    sub = args[0]
    if sub in ("action", "flow"):
        return f"message:{sub}:{_nth_positional(args[1:], 2, VAL_FLAGS['message ' + sub + ' run'])}"
    vals = {"adhoc": VAL_FLAGS["message adhoc"],
            "validate": VAL_FLAGS["message validate"],
            "show": (), "update": VAL_FLAGS["message update"],
            "remove": ()}.get(sub)
    if vals is None:
        return "config"
    return f"message:{_first_positional(args[1:], vals)}"

SPECS: dict[str, dict[str, Any]] = {
    "start": {"confirm": True, "force": False, "key": "start"},
    "review": {"confirm": True, "force": False,
               "key": lambda args: "review:all" if "--all" in args
               else f"review:{_first_positional(args, VAL_FLAGS['review'])}"},
    "cleanup": {"confirm": True, "force": True,
                "key": lambda args: "cleanup:all" if "--merged" in args
                else f"cleanup:{_first_positional(args)}"},
    "release": {"confirm": True, "force": True,
                "key": lambda args: f"release:{_first_positional(args, VAL_FLAGS['release'])}"},
    "sync": {"confirm": True, "force": False,
             "key": lambda args: "sync:all" if "--all" in args
             else f"sync:{_first_positional(args, VAL_FLAGS['sync'])}"},
    "open": {"confirm": False, "force": False,
             "key": lambda args: f"open:{_first_positional(args)}"},
    "register": {"confirm": False, "force": True,
                 "key": lambda args: f"register:{_first_positional(args, VAL_FLAGS['register'])}"},
    "repo": {"confirm": False, "force": True, "key": "config"},
    "link": {"confirm": False, "force": False, "confirm_subs": ("destroy-deleted",),
             "key": lambda args: "config" if args[:1] not in (["destroy-deleted"],) else "link:destroy-deleted"},
    "tracker": {"confirm": False, "force": False, "key": "config"},
    "action": {"confirm": False, "force": False, "confirm_subs": ("run", "delete"),
               "key": lambda args: f"action:{_first_positional(args, VAL_FLAGS['action run'])}:{_first_positional(args[1:], VAL_FLAGS['action run'])}" if args[:1] == ["run"] else "config"},
    "flow": {"confirm": False, "force": False, "confirm_subs": ("run", "delete"),
             "key": lambda args: f"flow:{_nth_positional(args, 1, VAL_FLAGS['flow run'])}:{_nth_positional(args, 2, VAL_FLAGS['flow run'])}" if args[:1] == ["run"] else "config"},
    "message": {"confirm": False, "force": False, "confirm_subs": ("remove", "adhoc", "validate", "run"),
                "key": _message_key},
    "backup": {"confirm": False, "force": False, "confirm_subs": ("import",),
               "key": "config"},
    "prodtest": {"confirm": True, "force": False,
                 "key": lambda args: "prodtest:run-all" if args[:1] == ["run"] and "--all" in args
                 else (f"prodtest:{_first_positional(args[1:], VAL_FLAGS['prodtest plan'])}" if args[:1] == ["plan"] else f"prodtest:{_first_positional(args[1:], VAL_FLAGS['prodtest run'])}") if args[:1] in (["plan"], ["run"]) else "config"},
    "adhoc": {"confirm": True, "force": False,
              "key": lambda args: f"adhoc:{_first_positional(args, VAL_FLAGS['adhoc'])}"},
}

BOOL_FLAGS: dict[str, tuple[str, ...]] = {
    "start": ("-L", "--no-tty", "--launch", "--terminal", "--dry-run",
              "--yes", "--json", "--no-status", "--no-review"),
    "review": ("-L", "--no-tty", "--launch", "--terminal", "--dry-run",
               "--yes", "--json", "--all", "--sequential", "--fix", "--fix-comments", "--then-fix-comments", "--use-review-session", "--force-all", "--post-comments", "--no-status"),
    "cleanup": ("--force", "--yes", "--dry-run", "--json", "--merged", "--released", "--no-squash", "--hard-delete", "--merge-only"),
    "release": ("--yes", "--dry-run", "--json", "--no-status"),
    "open": (),
    "sync": ("-m", "--merge", "--harness", "--all", "--yes", "--force",
             "-y", "--dry-run", "--json"),
    "register": ("--yes", "-y", "--force", "--json"),
    "repo add": ("--json", "--fetch", "--no-fetch"),
    "repo list": ("--json", "--csv"),
    "repo remove": ("--json", "--force"),
    "repo update": ("--json",),
    "link set": ("--json",),
    "link remove": ("--json", "--hard-delete"),
    "link deactivate": ("--json",),
    "link reactivate": ("--json",),
    "link undelete": ("--json",),
    "link gc": ("--dry-run", "--json"),
    "link destroy-deleted": ("--all", "--dry-run", "--json", "--yes"),
    "link list": ("--worktree", "--refresh-pr", "--json", "--csv", "--include-deleted"),
    "tracker add": ("--json", "--fetch", "--no-fetch"),
    "tracker update": ("--json", "--fetch", "--no-fetch", "--enable", "--disable"),
    "tracker remove": ("--json",),
    "tracker list": ("--json", "--csv"),
    "action add": ("--json", "--yes", "--protected", "--no-protected",
                   "--dedicated-top-bar", "--no-dedicated-top-bar",
                   "--dedicated-overlay", "--no-dedicated-overlay",
                   "--run-immediately", "--no-run-immediately"),
    "action list": ("--json", "--csv"),
    "action show": ("--json",),
    "action run": ("--launch", "--terminal", "--dry-run", "--yes", "--json"),
    "action update": ("--json", "--yes", "--protected", "--no-protected",
                      "--dedicated-top-bar", "--no-dedicated-top-bar",
                      "--dedicated-overlay", "--no-dedicated-overlay",
                      "--run-immediately", "--no-run-immediately"),
    "action delete": ("--yes", "--json"),
    "flow": (),
    "flow template": (),
    "flow template add": ("--json", "--yes", "--protected", "--no-protected",
                          "--dedicated-top-bar", "--no-dedicated-top-bar",
                          "--dedicated-overlay", "--no-dedicated-overlay",
                          "--run-immediately", "--no-run-immediately"),
    "flow template list": ("--json", "--csv"),
    "flow template show": ("--json",),
    "flow template update": ("--json", "--yes", "--protected", "--no-protected",
                             "--dedicated-top-bar", "--no-dedicated-top-bar",
                             "--dedicated-overlay", "--no-dedicated-overlay",
                             "--run-immediately", "--no-run-immediately"),
    "flow template clone": ("--json", "--yes"),
    "flow template compose": ("--json", "--yes"),
    "flow template delete": ("--yes", "--json"),
    "flow list": ("--json", "--csv"),
    "flow show": ("--json",),
    "flow run": ("--dry-run", "--yes", "--json", "--fresh"),
    "backup export": ("--json",),
    "backup import": ("--yes", "--json"),
    "backup run-scheduled": ("--json",),
    "prodtest": (),
    "prodtest plan": ("-L", "--no-tty", "--launch", "--terminal", "--dry-run",
                      "--yes", "--json", "--update"),
    "prodtest run": ("-L", "--no-tty", "--launch", "--terminal", "--dry-run",
                     "--yes", "--json", "--skip-version-gate", "--all"),
    "adhoc": ("--launch", "--terminal", "--dry-run", "--yes", "--json"),
    "message add": ("--json",),
    "message list": ("--json", "--csv"),
    "message show": ("--json",),
    "message update": ("--json",),
    "message remove": ("--yes", "--json"),
    "message adhoc": ("--launch", "--terminal", "--dry-run", "--yes", "--json"),
    "message validate": ("--launch", "--terminal", "--dry-run", "--yes", "--json"),
    "message action run": ("--launch", "--terminal", "--dry-run", "--yes", "--json"),
    "message flow run": ("--dry-run", "--yes", "--json"),
    "message": (),
    "message action": (),
    "message flow": (),
}
VAL_FLAGS: dict[str, tuple[str, ...]] = {
    "adhoc": ("--prompt", "--harness", "--extra-prompt", "--session-file", "--env-file"),
    "prodtest": (),
    "prodtest plan": ("--harness", "--session-file", "--env-file", "--extra-prompt"),
    "prodtest run": ("--fix-mode", "--run-dir", "--harness", "--session-file", "--env-file", "--extra-prompt"),
    "start": ("--repo", "--depth", "--base", "--harness", "--session-file", "--env-file", "--extra-prompt", "--related-repo"),
    "review": ("--repo", "--depth", "--harness", "--session-file", "--env-file", "--extra-prompt"),
    "sync": ("--session-file", "--env-file", "--extra-prompt"),
    "register": ("--key", "--issue", "--repo"),
    "release": ("--checks", "--diff", "--pre-release-timeout", "--ci-timeout", "--env-file", "--action", "--run-mode", "--extra-prompt"),
    "repo add": ("--name", "--path", "--tracker", "--release-branch", "--pre-release"),
    "repo update": ("--default-branch", "--release-branch", "--pre-release", "--overlay-buttons"),
    "link remove": ("--repo",),
    "tracker add": ("--vendor", "--remote-url"),
    "tracker update": ("--vendor", "--remote-url"),
    "action add": ("--name", "--label", "--icon", "--prompt", "--pre-execution", "--post-execution", "--default-options", "--sort-order", "--category"),
    "action update": ("--name", "--icon", "--prompt", "--pre-execution", "--post-execution", "--default-options", "--new-label", "--sort-order", "--category"),
    "action run": ("--harness", "--extra-prompt", "--env-file", "--session-file"),
    "flow template add": ("--name", "--label", "--steps", "--pre-execution", "--post-execution", "--sort-order", "--category"),
    "flow template update": ("--name", "--steps", "--pre-execution", "--post-execution", "--new-label", "--sort-order", "--category"),
    "flow template clone": ("--name", "--label"),
    "flow template compose": ("--from", "--name"),
    "flow list": ("--worktree",),
    "flow run": ("--step-config", "--skip-steps", "--steps"),
    "backup export": ("--path",),
    "backup import": (),
    "backup run-scheduled": ("--keep",),
    "message add": ("--message-id", "--thread-id", "--app", "--sender", "--content", "--ts", "--repo", "--related-repo", "--worktree"),
    "message list": ("--thread",),
    "message adhoc": ("--prompt", "--ref", "--repo", "--harness", "--extra-prompt", "--env-file", "--session-file"),
    "message validate": ("--prompt", "--ref", "--repo", "--harness"),
    "message update": ("--thread-id", "--sender", "--content", "--ts", "--repo", "--related-repo", "--worktree"),
    "message action run": ("--ref", "--repo", "--harness", "--extra-prompt", "--env-file", "--session-file"),
    "message flow run": ("--ref", "--repo",),
    "message": (),
}
SUBCOMMANDS: dict[str, set[str]] = {"repo": {"add", "list", "remove", "update"},
                                    "link": {"list", "set", "remove", "deactivate", "reactivate", "undelete", "destroy-deleted"},
                                    "tracker": {"add", "list", "remove", "update"},
                                    "action": {"add", "list", "show", "run", "update", "delete"},
                                    "flow": {"template", "list", "show", "run"},
                                    "backup": {"export", "import", "run-scheduled"},
                                    "message": {"add", "list", "show", "update", "remove", "adhoc", "validate", "action", "flow"}}
TEMPLATE_SUBCOMMANDS: set[str] = {"add", "list", "show", "update", "clone", "compose", "delete"}
def _needs_confirm(command: str, args: list[str]) -> bool:
    """True when the (sub)command needs confirm:true (destructive only).

    Plain SPECS confirm covers whole commands (cleanup/sync/start/review);
    action/flow narrow it via confirm_subs to run/delete (add/update/list/
    show are plain row writes — the inventory marks them
    non-destructive). Nested flow template CRUD nests two deep
    (["template", "add", ...]) so the second token decides. Message
    action/flow runs nest two deep (["action", "run", ...]); the bare
    "run" token in confirm_subs matches them. Message adhoc/validate run an
    agent, so they need confirm (message add/list/show/update/remove stay
    non-destructive reads/plain row writes).
    """
    spec = SPECS[command]
    if "--dry-run" in args:
        return False
    subs = spec.get("confirm_subs")
    if not subs:
        return bool(spec.get("confirm"))
    rest = [a for a in args if a not in ("-v", "--verbose")]
    if command == "flow" and rest[:1] == ["template"]:
        sub = rest[1] if len(rest) > 1 else ""
        return sub in ("delete", "run")
    if command == "message" and rest[:1] in (["action"], ["flow"]):
        sub = rest[1] if len(rest) > 1 else ""
        return sub in subs
    sub = rest[0] if rest else ""
    return sub in subs



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


def _nth_positional(args: list[str], n: int, vals: tuple[str, ...] = ()) -> str:
    """nth positional of *args* skipping val-flag values (0-based)."""
    skip_next = False
    found = 0
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
            if found == n:
                return a
            found += 1
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
        if command == "flow" and args[:1] == ["template"] and len(args) > 1:
            return f"flow template {args[1]}"
        if command == "message" and args[:1] in (["action"], ["flow"]) and len(args) > 1:
            return f"message {args[0]} {args[1]}"
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
    if command == "flow" and rest[:1] == ["template"] and (
            len(rest) < 2 or rest[1] not in TEMPLATE_SUBCOMMANDS):
        raise ApiError("bad_subcommand",
                       "flow template needs one of: "
                       f"{', '.join(sorted(TEMPLATE_SUBCOMMANDS))}", 400)
    sub = _sub_of(command, rest) if command in SUBCOMMANDS else command
    bools = set(BOOL_FLAGS.get(sub, ()))
    vals = set(VAL_FLAGS.get(sub, ()))
    i = 0
    while i < len(rest):
        a = rest[i]
        name = a.split("=", 1)[0] if a.startswith("--") and "=" in a else a
        if a in bools:
            i += 1
        elif name in vals:
            if "=" not in a:
                if i + 1 >= len(rest):
                    raise ApiError("bad_arg", f"{a} needs a value", 400)
                value = rest[i + 1]
                # Free-text prompt may start with '-'; the = form is required
                # so the value can't be mistaken for the next flag. Same for
                # --overlay-buttons: hide-only values (`-adhoc`) and the empty
                # reset (`""`) are legal CLI values.
                if value.startswith("-") and a not in ("--extra-prompt", "--overlay-buttons"):
                    raise ApiError(
                        "bad_arg",
                        f"value for {a} must not start with '-': {value!r}", 400)
                i += 2
            else:
                i += 1
        elif a.startswith("-"):
            hint = " (renamed to '--launch'; old '--no-harness'/-N removed)" if a in ("--no-harness", "-N") else ""
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
        from . import refs as _refs
        from . import trackers as _trackers
        from . import worktrees as _worktrees
        ref = _first_positional(rest, VAL_FLAGS.get(command, ()))
        repo = ""
        for i, a in enumerate(rest):
            if a == "--repo" and i + 1 < len(rest):
                repo = rest[i + 1]
            elif a.startswith("--repo="):
                repo = a.split("=", 1)[1]
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
                    pinned = bool(key and any(
                        str((links.get(k) or {}).get("repo", ""))
                        for k in _refs.matching_issue_keys(key, links)))
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
    out: list[str] = []
    i = 0
    rest = [a for a in args if a not in ("-v", "--verbose")]
    while i < len(rest):
        # Click parses `--extra-prompt value` fine, but a value starting
        # with '-' would be read as the next flag — always use the = form.
        # Same for --overlay-buttons: hide-only values (`-adhoc`) and the
        # empty reset (`""`) must survive as one token.
        if rest[i] in ("--extra-prompt", "--overlay-buttons") and i + 1 < len(rest):
            out.append(f"{rest[i]}={rest[i + 1]}")
            i += 2
        else:
            out.append(rest[i])
            i += 1
    argv.extend(out)
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


def _session_file_for(sid: str, command: str = "", args: list[str] | None = None) -> str:
    from . import store_sqlite as _sq
    action: str | None = None
    try:
        fix = bool(args) and "--fix-comments" in list(args)
        action = _sq.derive_session_type(command, fix)
    except Exception:
        action = None
    try:
        return str(_sq.session_file_path(sid, "omp", session_type=action))
    except Exception:
        from . import store as _store
        base = _store.config_dir() / "sessions" / "omp"
        if action:
            base = base / action
        base.mkdir(parents=True, exist_ok=True)
        return str(base / f"{sid}.jsonl")
