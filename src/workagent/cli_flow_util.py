"""Shared flow-config helpers (keeps cli_flow.py at its 500-line cap).

F1/F5 fixes live here: wizard `run_mode`/`extra-flags` translation plus the
single banned-substring scanner all guards converge on.
"""
from __future__ import annotations

import shlex


#: run_mode values the wizard emits → backend manual-mode flags.
_RUN_MODES = ("preview", "dry_run", "terminal")


def _coerce(value: str) -> object:
    """Coerce pure-digit flag values to int (e.g. `--depth 7`)."""
    return int(value) if value.isdigit() else value


#: config keys that must reach the runner as a list (UI edits them as
#: comma-separated text; idempotent — lists pass through untouched).
_LIST_KEYS = ("related_repo",)


#: start-step config keys that must survive the terminal/manual handoff.
START_FLAGS = (("repo", "--repo", "str"),
               ("related_repo", "--related-repo", "list"),
               ("depth", "--depth", "str"),
               ("base", "--base", "str"),
               ("harness", "--harness", "str"),
               ("no_tty", "--no-tty", "bool"),
               ("session_file", "--session-file", "str"),
               ("env_file", "--env-file", "str"),
               ("extra_prompt", "--extra-prompt", "str"),
               ("no_status", "--no-status", "bool"),
               ("no_review", "--no-review", "bool"))

#: review/fix-comments config keys for the terminal/manual handoff
#: (then_fix_comments stays banned inside flows — never forwarded).
REVIEW_FLAGS = (("repo", "--repo", "str"),
                ("depth", "--depth", "str"),
                ("harness", "--harness", "str"),
                ("no_tty", "--no-tty", "bool"),
                ("use_review_session", "--use-review-session", "bool"),
                ("session_file", "--session-file", "str"),
                ("env_file", "--env-file", "str"),
                ("extra_prompt", "--extra-prompt", "str"),
                ("no_status", "--no-status", "bool"))

#: sync-step config keys for the terminal/manual handoff.
SYNC_FLAGS = (("merge", "--merge", "bool"),
              ("harness", "--harness", "bool"),
              ("session_file", "--session-file", "str"),
              ("env_file", "--env-file", "str"),
              ("extra_prompt", "--extra-prompt", "str"))

#: custom-action config keys for the terminal/manual handoff.
ACTION_FLAGS = (("harness", "--harness", "str"),
                ("extra_prompt", "--extra-prompt", "str"),
                ("env_file", "--env-file", "str"),
                ("session_file", "--session-file", "str"))

#: release-step config keys for the terminal/manual handoff.
RELEASE_FLAGS = (("checks", "--checks", "str"),
                 ("diff", "--diff", "str"),
                 ("pre_release_timeout", "--pre-release-timeout", "str"),
                 ("ci_timeout", "--ci-timeout", "str"),
                 ("env_file", "--env-file", "str"),
                 ("no_status", "--no-status", "bool"))


def flag_argv(config: dict, *specs: tuple[str, str, str]) -> list[str]:
    """Render config keys as CLI argv fragments for terminal/manual steps.

    Each spec is (config_key, flag, kind) with kind "str" | "bool" | "list".
    Headless runners get these via ``_pick``; without this the spawned
    terminal/manual command silently drops them (e.g. --repo → ambiguous
    tracker error in the terminal while headless would have worked).
    """
    argv: list[str] = []
    for key, flag, kind in specs:
        val = config.get(key)
        if kind == "bool":
            if val:
                argv.append(flag)
        elif kind == "list":
            items = val if isinstance(val, list) else ([val] if val else [])
            for item in items:
                if item is not None and str(item) != "":
                    argv += [flag, str(item)]
        elif val is not None and str(val) != "":
            argv += [flag, str(val)]
    return argv

def normalize_config(config: object) -> dict:
    """Translate UI-only keys to real runner keys (idempotent).

    - ``run_mode: preview|terminal`` → ``{"preview"|"terminal": True}``
      (``headless`` strips any manual-mode flags).
    - ``extra-flags``/``extra_flags``: ``"--depth 7 --no-tty"`` →
      ``{"depth": 7, "no_tty": True}`` (shlex-parsed; ``--k=v`` /
      ``--k v`` / bare boolean ``--k``; dashes normalized to
      underscores so keys match the ``_pick`` allowlists; pure-digit
      values coerced to int).
    Unknown keys pass through untouched; non-dict input → ``{}``.
    """
    if not isinstance(config, dict):
        return {}
    out = dict(config)
    mode = out.pop("run_mode", None)
    if isinstance(mode, str) and mode:
        for m in _RUN_MODES:
            out.pop(m, None)
        if mode in ("preview", "dry_run"):
            out["preview" if mode == "preview" else "dry_run"] = True
        elif mode == "terminal":
            out["terminal"] = True
        # "headless" (or unknown): manual flags already stripped above.
    raw_flags = out.pop("extra-flags", None)
    if raw_flags is None:
        raw_flags = out.pop("extra_flags", None)
    else:
        out.pop("extra_flags", None)
    if not isinstance(raw_flags, str) or not raw_flags.strip():
        return _coerce_lists(out)
    try:
        tokens = shlex.split(raw_flags)
    except ValueError:
        tokens = raw_flags.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok.startswith("--"):
            continue
        body = tok[2:]
        if not body:
            continue
        if "=" in body:
            k, v = body.split("=", 1)
            out[k.replace("-", "_")] = _coerce(v)
            continue
        nxt = tokens[i] if i < len(tokens) else None
        if nxt is not None and not nxt.startswith("-"):
            out[body.replace("-", "_")] = _coerce(nxt)
            i += 1
        else:
            out[body.replace("-", "_")] = True
    return _coerce_lists(out)


def _coerce_lists(out: dict) -> dict:
    """Split comma-string list keys (idempotent; blank → dropped)."""
    for k in _LIST_KEYS:
        v = out.get(k)
        if isinstance(v, str):
            items = [p.strip() for p in v.split(",") if p.strip()]
            if items:
                out[k] = items
            else:
                del out[k]
    return out


def has_banned(config: object, banned: tuple[str, ...]) -> str | None:
    """Scan config keys + string values for banned substrings.

    Single scanner for all flow banned-key guards (template validation,
    run fail-fast, cleanup runtime): dict keys and every string value
    anywhere in the structure are checked; the raw JSON blob is never
    scanned, so innocent prose (e.g. ``extra_prompt`` text) cannot trip it.
    Returns the first banned hit, else None.
    """
    from . import store_flows as _sf
    return _sf._config_has_banned(config, banned)

#: flow steps whose runners accept `--terminal` (the child spawns the
#: harness in the OS terminal instead of printing its command). sync,
#: cleanup and release have no such flag — their terminal steps degrade
#: to a manual pause with the runnable command printed.
TERMINAL_CAPABLE = ("start", "review", "action")


def spawn_terminal_step(kind: str, args: list[str], cwd: str = "",
                        branch: str = "", harness: str = "omp") -> dict:
    """Terminal-mode step: mint the transcript scope, spawn, then pause.

    *kind* selects terminal support: terminal-capable kinds get
    ``--terminal`` appended so the child really runs the harness in the
    new terminal instead of printing its command (preview). Other kinds
    keep the command as-is (manual pause). Spawn failure degrades to a
    plain manual pause — the command is always printed either way.

    Option A scope: the detached terminal mints its .jsonl seconds after
    we return, so the scope (fresh session id + created parent dir) is
    minted HERE and returned as ``session_scope``/``session_dir``. The
    resume path re-resolves the minted file via
    ``adopt_terminal_transcript`` — the file itself is never pre-touched
    (omp rejects empty transcripts). Best effort: ("", "") on failure.
    """
    scope_file, scope_dir = mint_terminal_scope(kind, branch, harness)
    from . import cli_harness as _h
    argv = [*args, "--terminal"] if kind in TERMINAL_CAPABLE else args
    cmd = " ".join(shlex.quote(a) for a in [kind, *argv] if a)
    full = f"workagent {cmd}"
    base = {"preview": True, "command": full,
            "manual_kind": kind, "manual_args": [kind, *argv]}
    if scope_file:
        base["session_scope"] = scope_file
        base["session_dir"] = scope_dir
    try:
        tpid = _h.spawn_in_terminal(full, cwd)
    except Exception as e:
        return {**base, "terminal_error": str(e)}
    out = {**base, "terminal_pid": tpid}
    if cwd:
        out["terminal_cwd"] = cwd
    return out


def persist_terminal_pause(key_or_ref: str, manual_kind: str,
                           manual_args: list, output: str, flow_id: int,
                           terminal_pid=None, scope: str = "",
                           scope_dir: str = "") -> str | None:
    """Persist a terminal/manual pause session tagged with the flow.

    The scope (fresh session id minted pre-spawn) pins the row id; the
    resume path re-resolves the detached terminal's minted .jsonl via
    `adopt_terminal_transcript`. Best effort — never raises.
    """
    from . import cli_harness as _h
    from . import store_sqlite as _sq
    meta = {"flow_id": flow_id, "mode": "flow-terminal",
            "session_dir": scope_dir}
    if terminal_pid is not None:
        meta["terminal_pid"] = terminal_pid
    try:
        sid = _h.record_headless_attempt(
            key_or_ref, manual_kind, manual_args, output, exit_code=0,
            metadata=meta, session_file=scope or None)
        if sid and scope:
            try:
                _sq.set_session_metadata(_sq.db_path(), sid, meta)
            except Exception:
                pass
        return sid
    except Exception:
        return None



def backfill_terminal_transcripts(flow_id: int, key: str, ref: str) -> None:
    """Re-resolve every flow-tagged pause session's minted transcript.

    The detached terminal mints its .jsonl after the pause row was
    written; on resume each flow-tagged pause row re-resolves its scope
    dir's newest transcript. Never raises.
    """
    from . import store_sqlite as _sq
    try:
        db = _sq.db_path()
        if not db.exists():
            return
        rows = _sq.list_sessions(db, include_deleted_worktrees=True)
    except Exception:
        return
    for s in rows:
        try:
            if (s.get("worktree_ref") or "") not in (key, ref):
                continue
            if (s.get("metadata") or {}).get("flow_id") != flow_id:
                continue
            if (s.get("metadata") or {}).get("mode") not in (
                    "flow-terminal", "manual-step"):
                continue
            scope = str(s.get("file_path") or "")
            sid = str(s.get("id") or "")
            if scope and sid:
                adopt_terminal_transcript(scope, sid)
        except Exception:
            continue

#: flow-step kind → harness session type segment for the scope dir.
#: sync/cleanup/release have no harness phase — their terminal/manual
#: pause carries no session scope.
_SESSION_TYPE = {"start": "start", "review": "review",
                 "fix_comments": "fix_comments", "action": "action"}


def mint_terminal_scope(kind: str, branch: str = "",
                        harness: str = "omp") -> tuple[str, str]:
    """Mint the pre-spawn transcript scope for a terminal flow step.

    Returns (scope_session_file, scope_dir): the session id is fresh
    (never reused), the parent dir is created, and the file itself is
    NOT pre-touched — omp owns the minted filename under --session-dir
    and rejects empty transcripts. Best effort: ("", "") on failure.
    """
    try:
        from . import store_sqlite as _sq
        stype = _SESSION_TYPE.get(kind, "")
        path = _sq.session_file_path(_sq.gen_session_id(), harness,
                                     branch=branch or None,
                                     session_type=stype or None)
        return str(path), str(path.parent)
    except Exception:
        return "", ""


def adopt_terminal_transcript(scope_file: str, sid: str) -> str:
    """Re-resolve a detached terminal step's minted .jsonl on resume.

    The spawned terminal mints its transcript seconds after we return;
    by resume time it exists under the scope dir. Mirrors the web
    `_resolve_session_file` fallback: pinned path when ready, else the
    newest non-empty .jsonl under the scope dir (persisted on the
    session). Returns "" when nothing resumable is found. Never raises.
    """
    from pathlib import Path as _p
    try:
        from . import store_sqlite as _sq
        p = _p(scope_file) if scope_file else None
        if p is not None:
            try:
                if p.is_file() and p.stat().st_size > 0:
                    return scope_file
            except OSError:
                pass
        scope = p.parent if p is not None else None
        if scope is None:
            return ""
        try:
            if not scope.is_dir():
                return ""
        except OSError:
            return ""
        minted = _sq.newest_transcript(scope)
        if minted is None:
            return ""
        try:
            db = _sq.db_path()
            if sid and db.exists():
                _sq.set_session_file_path(db, sid, str(minted))
        except Exception:
            pass
        return str(minted)
    except Exception:
        return ""


def adopt_skipped_start_key(resolver, ref: str, key: str):
    """Re-resolve *ref* after a skipped start step.

    A skipped start means the human finished in the terminal (or the
    worktree already exists): whatever the ref resolves to NOW is the
    key downstream steps must use. Returns (key, entry) — (unchanged
    key, {}) when nothing resolves. Resolver failures are swallowed
    (the step still runs with the old key and fails loudly there).
    """
    from . import store as _store
    try:
        new_key, new_entry, _ = resolver(ref)
    except Exception:
        return key, {}
    if new_key and new_key != key:
        links = _store.load_links()
        return new_key, links.get(new_key, new_entry or {})
    return key, new_entry or {}
