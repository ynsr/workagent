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
               ("no_review", "--no-review", "bool"),
               ("plan", "--plan", "bool"))

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
              ("harness", "--harness", "str"),
              ("session_file", "--session-file", "str"),
              ("env_file", "--env-file", "str"),
              ("extra_prompt", "--extra-prompt", "str"))

#: custom-action config keys for the terminal/manual handoff.
ACTION_FLAGS = (("harness", "--harness", "str"),
                ("extra_prompt", "--extra-prompt", "str"),
                ("env_file", "--env-file", "str"),
                ("session_file", "--session-file", "str"))

#: prodtest-plan step config keys for the terminal/manual handoff.
PRODTEST_PLAN_FLAGS = (("update", "--update", "bool"),
                       ("harness", "--harness", "str"),
                       ("session_file", "--session-file", "str"),
                       ("env_file", "--env-file", "str"),
                       ("extra_prompt", "--extra-prompt", "str"))

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
TERMINAL_CAPABLE = ("start", "review", "action", "prodtest")


def spawn_terminal_step(kind: str, args: list[str], cwd: str = "",
                        branch: str = "", harness: str = "") -> dict:
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
    (the harness rejects empty transcripts). Best effort: ("", "") on failure.
    """
    from . import store as _store
    harness = harness or _store.default_harness()
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
                           scope_dir: str = "",
                           harness_name: str | None = None) -> int | None:
    """Persist a terminal/manual pause run tagged with the flow.

    The scope (fresh transcript id minted pre-spawn) pins the run's
    file_path; the resume path re-resolves the detached terminal's minted
    .jsonl via `adopt_terminal_transcript`. Best effort — never raises.
    """
    from . import cli_harness as _h
    from . import store_sqlite as _sq
    meta = {"flow_id": flow_id, "mode": "flow-terminal",
            "session_dir": scope_dir}
    if terminal_pid is not None:
        meta["terminal_pid"] = terminal_pid
    try:
        rid = _h.record_headless_attempt(
            key_or_ref, manual_kind, manual_args, output, exit_code=0,
            harness_name=harness_name or "",
            metadata=meta, session_file=scope or None)
        if rid and scope:
            try:
                _sq.set_run_metadata(_sq.db_path(), rid, meta)
            except Exception:
                pass
        return rid
    except Exception:
        return None



def backfill_terminal_transcripts(flow_id: int, key: str, ref: str) -> None:
    """Re-resolve every flow-tagged pause run's minted transcript.

    The detached terminal mints its .jsonl after the pause row was
    written; on resume each flow-tagged pause row re-resolves its scope
    dir's newest transcript. Reads the worktree's runs uncapped — a pause
    row older than the newest N runs must still be seen, or the resume
    proceeds from the stale pre-mint path. Never raises.
    """
    from . import store_sqlite as _sq
    try:
        db = _sq.db_path()
        if not db.exists():
            return
        rows = _sq.runs_for_worktree(db, key, include_deleted_worktrees=True)
        if ref and ref != key:
            # Pause rows may be attributed to the ref the terminal was
            # spawned with before the worktree key resolved.
            rows += _sq.runs_for_worktree(db, ref,
                                          include_deleted_worktrees=True)
    except Exception:
        return
    for r in rows:
        try:
            if (r.get("worktree_ref") or "") not in (key, ref):
                continue
            meta = r.get("metadata") or {}
            if meta.get("flow_id") != flow_id:
                continue
            if meta.get("mode") not in ("flow-terminal", "manual-step"):
                continue
            scope = str(r.get("file_path") or "")
            rid = int(r.get("id") or 0)
            if scope and rid:
                adopt_terminal_transcript(scope, rid)
        except Exception:
            continue

#: flow-step kind → harness session type segment for the scope dir.
#: sync/cleanup/release have no harness phase — their terminal/manual
#: pause carries no session scope.
_SESSION_TYPE = {"start": "start", "review": "review",
                 "fix_comments": "fix_comments", "action": "action",
                 "prodtest": "prodtest"}


def mint_terminal_scope(kind: str, branch: str = "",
                        harness: str = "") -> tuple[str, str]:
    """Mint the pre-spawn transcript scope for a terminal flow step.

    Returns (scope_session_file, scope_dir): the session id is fresh
    (never reused), the parent dir is created, and the file itself is
    NOT pre-touched — the harness owns the minted filename and
    rejects empty transcripts. Best effort: ("", "") on failure.
    """
    try:
        from . import store as _store
        from . import store_sqlite as _sq
        harness = harness or _store.default_harness()
        stype = _SESSION_TYPE.get(kind, "")
        path = _sq.session_file_path(_sq.gen_session_id(), harness,
                                     branch=branch or None,
                                     session_type=stype or None)
        return str(path), str(path.parent)
    except Exception:
        return "", ""


def adopt_terminal_transcript(scope_file: str, run_id: int) -> str:
    """Re-resolve a detached terminal step's minted .jsonl on resume.

    The spawned terminal mints its transcript seconds after we return;
    by resume time it exists under the scope dir. Mirrors the web resume
    fallback: pinned path when ready, agy workspace adoption when
    applicable, else the newest non-empty .jsonl under the scope dir
    (persisted on the run). A stored pre-rename ``sessions/…`` path
    resolves through the runs tree. Returns "" when nothing resumable is
    found. Never raises.
    """
    try:
        from datetime import datetime
        from pathlib import Path as _Path
        from . import store as _store, store_sqlite as _sq
        p = _sq.resolve_transcript_path(scope_file) if scope_file else None
        if p is not None:
            try:
                if p.is_file() and p.stat().st_size > 0:
                    return str(p)
            except OSError:
                pass
        db = _sq.db_path()
        row = None
        if run_id and db.exists():
            try:
                row = _sq.get_run(db, run_id, include_deleted_worktrees=True)
            except Exception:
                row = None
        if row is not None:
            db_p = _sq.resolve_transcript_path(str(row.get("file_path") or ""))
            if db_p is not None:
                try:
                    if db_p.is_file() and db_p.stat().st_size > 0:
                        return str(db_p)
                except OSError:
                    pass
            hname = str(row.get("harness_name") or "") or _store.default_harness()
            if hname == "agy":
                try:
                    from . import agy_harness as _agy
                    meta = row.get("metadata")
                    meta = dict(meta) if isinstance(meta, dict) else {}
                    dest = (str(p) if p is not None else str(row.get("file_path") or ""))
                    if not dest:
                        dest = str(_sq.session_file_path(
                            _sq.gen_session_id(), "agy",
                            session_type=row.get("session_type")))
                    cid = str(meta.get("conversation_id") or "")
                    if cid and _agy.mirror_brain_transcript(cid, dest):
                        dp = _Path(dest)
                        if dp.is_file() and dp.stat().st_size > 0:
                            _sq.set_run_file_path(db, run_id, dest)
                            _sq.set_run_metadata(db, run_id, meta)
                            return dest
                    worktree = ""
                    ref = str(row.get("worktree_ref") or "")
                    if ref:
                        links = _store.load_links()
                        if ref in links and isinstance(links[ref], dict):
                            worktree = str(links[ref].get("worktree") or "")
                    if not worktree:
                        args = row.get("args") if isinstance(row.get("args"), list) else []
                        if len(args) >= 2 and isinstance(args[1], str):
                            links = _store.load_links()
                            if args[1] in links and isinstance(links[args[1]], dict):
                                worktree = str(links[args[1]].get("worktree") or "")
                    if worktree:
                        created_ts = 0.0
                        created = str(row.get("created_at") or "")
                        if created:
                            try:
                                created_ts = datetime.fromisoformat(created).timestamp()
                            except Exception:
                                created_ts = 0.0
                        adopted_cid, adopted_path = _agy.adopt_from_workspace(
                            worktree, created_ts, dest)
                        if adopted_cid:
                            meta["conversation_id"] = adopted_cid
                            if adopted_path:
                                ap = _Path(adopted_path)
                                if ap.is_file() and ap.stat().st_size > 0:
                                    _sq.set_run_file_path(db, run_id, adopted_path)
                                    _sq.set_run_metadata(db, run_id, meta)
                                    return adopted_path
                            _sq.set_run_metadata(db, run_id, meta)
                except Exception:
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
            if run_id and db.exists():
                _sq.set_run_file_path(db, run_id, str(minted))
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
