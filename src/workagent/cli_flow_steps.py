"""Flow step runners (split from cli_flow_util.py: 500-line cap)."""
from __future__ import annotations

from .cli_flow_util import (
    ACTION_FLAGS,
    PRODTEST_PLAN_FLAGS,
    RELEASE_FLAGS,
    REVIEW_FLAGS,
    START_FLAGS,
    SYNC_FLAGS,
    flag_argv,
    has_banned,
)


def pick(config: dict, *names: str) -> dict:
    """Config subset for runner kwargs."""
    return {n: config[n] for n in names if n in config}


def _call(fn, *args, **kwargs):
    """Call a runner's ``__wrapped__`` with real bool/int/str values.

    Click/typer stores ``OptionInfo(default=...)`` sentinels as defaults, and
    those objects are truthy — forwarding them raw turns every omitted
    ``terminal``/``dry_run`` into True inside the runner's guards. Unwrap
    them to their declared defaults so headless flow steps really run
    headless.
    """
    import inspect as _inspect
    import typer.models
    fixed = {k: (v.default if isinstance(v, typer.models.OptionInfo) else v)
             for k, v in kwargs.items()}
    try:
        sig = _inspect.signature(fn)
        for name, param in sig.parameters.items():
            if name in fixed:
                continue
            d = param.default
            if isinstance(d, typer.models.OptionInfo):
                fixed[name] = d.default
    except (TypeError, ValueError):
        pass
    return fn(*args, **fixed)
# ── step runners (live here to keep cli_flow.py at its 500-line cap) ──
# Lazy imports throughout: this module must never import runner modules
# at top level (circular). Callers pass spawn/manual step callbacks.
def _start_target(ref: str, key: str) -> str:
    """Issue ref to hand ``start`` for a flow step.

    Flows launched from a worktree selection pass the link key as *ref*
    (``branch:feat/x``, ``gitlab:host/g/r#3``, ``jira:KEY@repo``); ``start``
    parses its argument first, so a key reaches "cannot parse issue/PR
    ref". Fall back to the link's recorded issue (issue #109: late/repeated
    starts would otherwise be unreachable from the FlowsMenu).
    """
    from . import refs as _refs
    from . import store as _store
    from .errors import HarnessError as _HE
    try:
        _refs.parse_ref(ref)
        return ref
    except _HE:
        pass
    entry = (_store.load_links(include_deleted=True).get(key, {}) or {})
    fallback = str(entry.get("issue_url") or entry.get("issue") or "")
    if not fallback:
        fallback = _refs.issue_url(key) or ""
    try:
        _refs.parse_ref(fallback)
    except _HE:
        raise _HE(
            f"start step: {ref!r} is not an issue/PR ref and the link "
            f"records no issue — cannot start a harness from it",
            exit_code=2) from None
    return fallback


def run_start_step(ref: str, key: str, config: dict,
                   json_output: bool, spawn, manual) -> dict:
    """Start step: adopt the worktree when it exists, else start the ref."""
    from . import cli_start as _mod
    target = _start_target(ref, key)
    extra = flag_argv(config, *START_FLAGS)
    if bool(config.get("terminal", False)):
        from . import store as _store
        harness = str(config.get("harness", "") or _store.default_harness())
        return spawn("start", [target, *extra], branch="", harness=harness)
    if not (bool(config.get("launch", True))
            and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
        return manual("start", [target, *extra])
    # Pass the original issue ref: start resolves links itself and adopts
    # the existing worktree (pre-resolving to a key breaks parse_ref).
    out = _call(_mod.start.__wrapped__, target, yes=True, launch=True,
                no_tty=True, json_output=json_output,
                **pick(config, "repo", "related_repo", "depth",
                       "base", "harness", "no_tty", "session_file",
                       "env_file", "extra_prompt", "no_status",
                       "no_review", "plan"))
    if isinstance(out, dict) and out.get("preview"):
        return out
    # start returns None (prints its own result): recover the recorded
    # link key so downstream steps get a key, not the raw issue URL
    # (flow #71: {"key": <url>} re-resolved to [jira:IPG-1069@..,
    # github:o/r#69] and the review step died "ambiguous ref" under
    # --yes). Canonical key first, else whatever resolve_worktree finds;
    # only live rows (not soft-deleted, with a worktree) count.
    from . import refs as _refs
    from . import store as _store
    from . import worktrees as _wt
    from .errors import HarnessError as _HarnessError
    new_key = key or ""
    try:
        links = _store.load_links()
        parsed = _refs.parse_ref(target)
        canon = _refs.issue_key(parsed)
        hit = links.get(canon, {}) or {}
        if hit.get("worktree") and not hit.get("deleted_at"):
            new_key = canon
        else:
            resolved = _wt.resolve_worktree(target, links)
            if (isinstance(resolved, str)
                    and (links.get(resolved) or {}).get("worktree")
                    and not (links.get(resolved) or {}).get("deleted_at")):
                new_key = resolved
    except _HarnessError:
        pass
    return {"key": new_key or ref, "ref": ref, "status": "started"}



def _flow_repo(key: str, config: dict) -> str:
    """Repo the flow already resolved for *key* ("" when unknown).

    The start step records it on the link row; the review step inherits
    it so a template that sets ``--repo`` once does not re-resolve from
    a fuzzy worktree lookup per step (issue #91: the review step pinned
    an unrelated repo whose number collided as a substring, tripping the
    host-scoped tracker guard). Sync resolves from its own link row via
    ``_sync_one`` and inherits nothing. An explicit per-step ``repo``
    always wins.
    """
    if str(config.get("repo", "") or ""):
        return ""
    try:
        from . import store as _store
        got = (_store.load_links(include_deleted=True).get(key, {})
               or {}).get("repo", "")
    except Exception:
        return ""
    return str(got or "")


def run_review_step(key: str, config: dict, fix_comments: bool,
                    json_output: bool, spawn, manual) -> dict:
    """Review (or Fix-comments) step with merged flags."""
    from . import cli_review as _mod
    from . import store as _store
    if not config.get("repo"):
        fb = _flow_repo(key, config)
        if fb:
            config = {**config, "repo": fb}
    fix = bool(fix_comments or config.get("fix_comments", False))
    fix_argv = ["--fix-comments"] if fix else []
    extra = flag_argv(config, *REVIEW_FLAGS)
    if bool(config.get("terminal", False)):
        harness = str(config.get("harness", "") or _store.default_harness())
        links = _store.load_links(include_deleted=True)
        branch = str((links.get(key, {}) or {}).get("branch", "") or "")
        return spawn("review", [key, *fix_argv, *extra],
                     branch=branch, harness=harness)
    if not (bool(config.get("launch", True))
            and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
        return manual("review", [key, *fix_argv, *extra])
    _call(_mod.review.__wrapped__, key, yes=True, launch=True, no_tty=True,
          json_output=json_output, fix_comments=fix,
          **pick(config, "repo", "depth", "harness", "no_tty",
                 "then_fix_comments", "use_review_session", "session_file",
                 "env_file", "extra_prompt", "no_status"))
    return {"key": key, "status": "reviewed", "fix_comments": fix_comments}


def run_sync_step(key: str, config: dict, json_output: bool,
                  spawn, manual) -> dict:
    """Sync step with merged flags."""
    from . import cli_sync as _mod
    from . import store as _store
    extra = flag_argv(config, *SYNC_FLAGS)
    if bool(config.get("terminal", False)):
        links = _store.load_links()
        wt = str((links.get(key, {}) or {}).get("worktree", "") or "")
        harness = str(config.get("harness", "") or _store.default_harness())
        dlinks = _store.load_links(include_deleted=True)
        branch = str((dlinks.get(key, {}) or {}).get("branch", "") or "")
        return spawn("sync", [key, *extra], wt, branch=branch, harness=harness)
    if not (bool(config.get("launch", True))
            and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
        return manual("sync", [key, *extra])
    _call(_mod.sync_cmd.__wrapped__, key, yes=True, json_output=json_output,
          **pick(config, "merge", "harness", "session_file", "env_file",
                 "extra_prompt"))
    return {"key": key, "status": "synced"}


def run_action_step(key: str, action_label: str, config: dict,
                    json_output: bool, spawn, manual) -> dict:
    """Custom-action step (live label re-check for races)."""
    from . import cli_action as _mod
    from . import release_gates as _gates
    from . import store as _store
    from .errors import HarnessError as _HE
    if _store.get_action(action_label) is None:
        raise _HE(f"unknown action_label {action_label!r}", exit_code=2)
    extra = flag_argv(config, *ACTION_FLAGS)
    if bool(config.get("terminal", False)):
        links = _store.load_links()
        wt = str((links.get(key, {}) or {}).get("worktree", "") or "")
        harness = str(config.get("harness", "") or _store.default_harness())
        dlinks = _store.load_links(include_deleted=True)
        branch = str((dlinks.get(key, {}) or {}).get("branch", "") or "")
        return spawn("action", ["run", action_label, key, *extra], wt,
                     branch=branch, harness=harness)
    if not (bool(config.get("launch", True))
            and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
        return manual("action", ["run", action_label, key, *extra])
    try:
        out = _call(_mod.action_run.__wrapped__, action_label, key,
                    launch=True, yes=True, json_output=json_output,
                    **pick(config, "harness", "extra_prompt", "env_file",
                           "session_file"))
    except _gates.SkippedGate as e:
        return {"key": key, "label": action_label, "status": "skipped",
                "detail": e.reason}
    if isinstance(out, dict) and out.get("skipped"):
        return {"key": key, "label": action_label, "status": "skipped",
                "detail": str(out["skipped"])}
    return {"key": key, "label": action_label, "status": "acted"}


def run_prodtest_plan_step(key: str, config: dict, json_output: bool,
                           spawn, manual) -> dict:
    """Prodtest-plan step: generate/update the plan for the flow worktree."""
    from . import cli_prodtest as _mod
    from . import store as _store
    extra = flag_argv(config, *PRODTEST_PLAN_FLAGS)
    if bool(config.get("terminal", False)):
        links = _store.load_links()
        wt = str((links.get(key, {}) or {}).get("worktree", "") or "")
        harness = str(config.get("harness", "") or _store.default_harness())
        dlinks = _store.load_links(include_deleted=True)
        branch = str((dlinks.get(key, {}) or {}).get("branch", "") or "")
        return spawn("prodtest", ["plan", key, *extra], wt,
                     branch=branch, harness=harness)
    if not (bool(config.get("launch", True))
            and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
        return manual("prodtest", ["plan", key, *extra])
    _call(_mod.prodtest_plan.__wrapped__, key, yes=True, launch=True,
          no_tty=True, json_output=json_output,
          **pick(config, "update", "harness", "env_file",
                 "session_file", "extra_prompt"))
    return {"key": key, "status": "prodtest-planned"}


def run_cleanup_step(key: str, config: dict, json_output: bool,
                     spawn, manual) -> dict:
    """Cleanup step (fixed squash + soft-delete; overrides rejected)."""
    from . import cli_cleanup as _mod
    from . import store as _store
    from . import store_flows as _sfx
    from .errors import HarnessError as _HE
    cfg = dict(config or {})
    merge_only = bool(cfg.pop("merge_only", False))
    if has_banned(cfg, _sfx._BANNED_CLEANUP_FLAGS):
        raise _HE("cleanup is fixed to squash + soft-delete — "
                  "hard-delete/no-squash overrides rejected", exit_code=2)
    if merge_only:
        # Merge-PR-only mode: squash-merge the open PR, keep worktree /
        # branch / link. Mirrors `cleanup --merge-only`.
        if bool(config.get("terminal", False)):
            wt = str((_store.load_links().get(key, {}) or {}).get("worktree", "") or "")
            return spawn("cleanup", [key, "--merge-only"], wt)
        if not (bool(config.get("launch", True))
                and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
            return manual("cleanup", [key, "--merge-only"])
        entry = _store.load_links().get(key) or {}
        if not key or not entry:
            raise _HE(f"no linked state for {key}", exit_code=2)
        _mod._merge_only_one(key, dict(entry), bool(config.get("force", False)),
                             json_output, squash=True)
        return {"key": key, "status": "merged", "merge_only": True}
    if bool(config.get("terminal", False)):
        wt = str((_store.load_links().get(key, {}) or {}).get("worktree", "") or "")
        return spawn("cleanup", [key], wt)
    if not (bool(config.get("launch", True))
            and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
        return manual("cleanup", [key])
    entry = _store.load_links().get(key) or {}
    if not key or not entry:
        raise _HE(f"no linked state for {key}", exit_code=2)
    _mod._cleanup_one(key, dict(entry), bool(config.get("force", False)),
                      True, False, json_output,
                      squash=True, merge=None, hard_delete=False)
    return {"key": key, "status": "cleaned"}


def run_release_step(repo_snapshot: dict, config: dict, json_output: bool,
                     spawn, manual) -> dict:
    """Release step from the flow's repo snapshot (never links)."""
    from . import release_lock as _lock
    from . import release_util as _util
    from .errors import HarnessError as _HE
    repo = str((repo_snapshot or {}).get("repo", "") or "")
    if not repo:
        raise _HE("flow has no repo snapshot — cannot release", exit_code=2)
    extra = flag_argv(config, *RELEASE_FLAGS)
    if bool(config.get("terminal", False)):
        return spawn("release", [repo, *extra])
    if not (bool(config.get("launch", True))
            and not any(config.get(k) for k in ("preview", "dry_run", "terminal"))):
        return manual("release", [repo, *extra])
    lock = _lock.lock_path(str(_util.worktree_base()), repo)
    if _lock.locked(lock):
        return {"skipped": "release-locked", "repo": repo}
    from . import cli_release as _mod
    out = _mod.run_release(repo, yes=True, dry_run=False,
                           json_output=json_output,
                           **pick(config, "checks", "diff",
                                  "pre_release_timeout", "ci_timeout",
                                  "env_file", "no_status"))
    return {"repo": repo,
            "status": str((out or {}).get("status") or "released"),
            **(out or {})}


def run_repo_command(repo_name: str, phase: str, command: str, cwd: str) -> None:
    """Pre/post-execution repo command (600s cap; shared with cli_flow)."""
    from . import release_gates as _gates
    _gates.run_repo_command(repo_name, phase, command, cwd, 600, None)


def _tagged_flow(row: dict) -> int | None:
    """Flow a run row belongs to: column first, then the metadata history."""
    fid = row.get("flow_id")
    if fid is not None:
        return int(fid)
    meta = row.get("metadata")
    mfid = meta.get("flow_id") if isinstance(meta, dict) else None
    try:
        return int(mfid) if mfid is not None else None
    except (TypeError, ValueError):
        return None


def tag_flow_run(key: str, flow_id: int) -> None:
    """Best-effort: stamp flow_id onto the step's execution run.

    The run row carries the flow column plus the metadata history field,
    so the Runs page can group a flow's steps and history rows keep the
    mapping after the flow is gone. Headless validation-attempt rows are
    newer and attributed to the worktree too, but only the execution row
    owns the transcript: prefer the newest row with a ``file_path`` so
    later runs of that transcript inherit the flow (``insert_run``'s
    file_path lookup). Rows already tagged with a different flow are
    never this step's (steps that launch no harness leave only
    transcriptless rows): skipping them keeps a cleanup/release/sync tag
    from overwriting an earlier flow's attribution. A terminal/manual
    pause row carries its flow only in ``metadata.flow_id`` (the column
    stays NULL), so the metadata counts as ownership too.
    """
    try:
        from . import store_sqlite as _sq
        db = _sq.db_path()
        rows = _sq.runs_for_worktree(db, key, include_deleted_worktrees=True)
        owned = [r for r in rows if _tagged_flow(r) in (None, flow_id)]
        row = next((r for r in owned if str(r.get("file_path") or "")),
                   owned[0] if owned else None)
        if row is not None:
            _sq.set_run_flow_id(db, int(row["id"]), flow_id)
    except Exception as e:
        import sys as _sys
        print(f"flow run tagging skipped: {e}", file=_sys.stderr)
