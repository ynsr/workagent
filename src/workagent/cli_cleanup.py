"""`cleanup` command + PR/issue-close helpers (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from . import backend, gitwt, refs, repos, store, trackers, worktrees
from . import sync as sync_mod
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _init_completions,
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .cli_harness import _guard_harness
from .cli_repo_util import _repo_tool
from .errors import HarnessError
from .cli_cleanup_merge import (  # noqa: F401 (re-export: moved helpers keep import sites working)
    _BLOCKED_MERGE_TOKENS,
    _LONG_LIVED_SOURCES,
    _close_issue,
    _close_pr,
    _fresh_pr_state,
    _merge_blocked_reason,
    _merge_pr,
    _pr_conflicted,
    _pr_missing,
    _resolve_branch_pr,
    _source_needs_squash,
)
from .cli_prodtest_gate import (  # noqa: F401 (re-export)
    _prodtest_blocked,
    _prodtest_issue_key,
    current_prodtest_diff,
    manifest_path_for,
    prodtest_gate,
    row_prodtest_gate,
    run_validate,
)
from .cli_cleanup_gc import destroy_deleted, gc_soft_deleted  # noqa: F401 (re-export)
_COMP = _init_completions()
_complete_refs = _COMP["refs"]

# ── cleanup ───────────────────────────────────────────────────────────


@app.command("cleanup")
@_catch_harness_errors
def cleanup(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="Issue ID/URL, PR/MR URL, or worktree ref (key/branch/worktree); omit with --merged."),
    force: bool = typer.Option(False, "--force", help="Skip state validation."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    merged: bool = typer.Option(False, "--merged", help="Clean every linked worktree whose PR/MR is merged/closed (requires --yes; skips live-harness and invalid worktrees)."),
    released: bool = typer.Option(False, "--released", help="Close every linked issue contained in its repo release branch (requires --yes; skips invalid worktrees)."),
    no_squash: bool = typer.Option(False, "--no-squash", help="Merge open PRs with a merge commit instead of squash."),
    merge_only: bool = typer.Option(False, "--merge-only", help="Merge the open PR/MR (squash) without deleting anything: worktree, branch, and link all survive."),
    hard_delete: bool = typer.Option(False, "--hard-delete", help="Delete the row, worktree, and branch immediately (skip the 7-day soft-delete recovery window)."),
) -> None:
    """Close issue + remove worktree/branch/PR.

    Example:
      workagent cleanup OWNER/REPO#22 --force --yes
      workagent cleanup OWNER/REPO#22 --dry-run
      workagent cleanup --merged --yes
    """
    if merged and ref:
        _fail("--merged takes no ref; it loops every linked worktree.", EXIT_USAGE)
    # --released accepts an optional ref (Dashboard per-key close); --merged does not.
    if merged and released:
        _fail("--merged and --released cannot be combined.", EXIT_USAGE)
    if merge_only and (merged or released):
        _fail("--merge-only cannot be combined with --merged/--released.", EXIT_USAGE)
    if merge_only and not ref:
        _fail("--merge-only takes a ref (per-worktree merge, no teardown).", EXIT_USAGE)
    if not ref and not merged and not released and not merge_only:
        _fail("ref required.\n"
              "  Pass a ref, or use --merged to clean every merged/closed link.",
              EXIT_USAGE)
    if merged and not yes and not dry_run:
        _fail("--merged requires --yes (non-interactive).\n"
              "  Re-run with --yes, or add --dry-run to preview.", EXIT_USAGE)
    if released and not ref and not yes and not dry_run:
        _fail("--released requires --yes (non-interactive).\n"
              "  Re-run with --yes, or add --dry-run to preview.", EXIT_USAGE)
    links = store.load_links()
    if merged or released:
        links = store.load_links(include_inactive=True,
                                 include_deleted=released)
    if released:
        from . import issue_status as _st

        def _close_if_jira_release_mark(key: str, entry: dict) -> dict | None:
            """Close guard for skipped rows: Jira Product Test -> done, else keep skip.

            `Released` no longer exists (issue #48); the release mark is now
            `Product Test`, with `released` still accepted for trackers that
            report it. Returns the result row when the Jira status matches,
            else None (caller keeps the original skip). Non-issue refs,
            unknown states, and non-matching states all keep the skip.
            Worktree/branch/PR untouched — issue state only.
            """
            stored = entry.get("issue", "") or key
            try:
                parsed = _st._parse(stored)
            except Exception:
                return None
            if parsed is None:
                return None
            cur = _st._issue_state(parsed.get("number", ""), parsed.get("tool", ""),
                                   parsed.get("url", ""))
            if cur not in ("product test", "released"):
                return None
            if dry_run:
                return {"key": key, "branch": entry.get("branch", ""),
                        "status": "dry-run"}
            ok = _st.set_issue_status(stored, "done", dry_run=False)
            return {"key": key, "branch": entry.get("branch", ""),
                    "status": "closed" if ok else "failed"}

        targets = dict(links)
        if ref:
            try:
                rkey, rentry, _ = worktrees.resolve_any(ref, links)
            except worktrees.NoLinkedState:
                if released:
                    # Per-ref close must also reach soft-deleted rows.
                    all_links = store.load_links(include_inactive=True,
                                                 include_deleted=True)
                    try:
                        rkey, rentry, _ = worktrees.resolve_any(ref, all_links)
                    except worktrees.NoLinkedState:
                        _fail(f"no linked state for {ref}.", EXIT_USAGE)
                else:
                    _fail(f"no linked state for {ref}.", EXIT_USAGE)
            targets = {rkey: rentry} if rkey and rentry else {}
        rows = []
        for key, entry in targets.items():
            if entry.get("deleted_at"):
                decided = _close_if_jira_release_mark(key, entry)
                rows.append(decided or {"key": key, "branch": entry.get("branch", ""),
                                        "status": "skipped:deleted"})
                continue
            if entry.get("active", 1) == 0:
                decided = _close_if_jira_release_mark(key, entry)
                rows.append(decided or {"key": key, "branch": entry.get("branch", ""),
                                        "status": "skipped:deactivated"})
                continue
            repo = str(entry.get("repo", "") or "")
            branch = str(entry.get("branch", "") or "")
            info = (store.load_repos().get(repo, {}) or {})
            rel_branch = str(info.get("release_branch", "") or "")
            if not repo or not branch or not rel_branch:
                decided = _close_if_jira_release_mark(key, entry)
                rows.append(decided or {"key": key, "branch": branch,
                                        "status": "skipped:no-release-branch"})
                continue
            wt = str(entry.get("worktree", "") or "")
            tip = ""
            try:
                from .errors import run_cmd as _run
                tip = (_run("git", "-C", wt if wt else repo, "rev-parse",
                            branch, echo=False) or "").strip() if (wt or repo) else ""
            except Exception:
                tip = ""
            contained = False
            if tip:
                try:
                    from .errors import run_cmd as _run2
                    _run2("git", "-C", wt if wt else repo, "merge-base",
                          "--is-ancestor", tip, f"origin/{rel_branch}", echo=False)
                    contained = True
                except Exception:
                    contained = False
            if not contained:
                rows.append({"key": key, "branch": branch, "status": "skipped:unreleased"})
                continue
            stored = entry.get("issue", "") or key
            if dry_run:
                try:
                    from . import refs as _refs
                    _refs.parse_ref(stored)
                    rows.append({"key": key, "branch": branch, "status": "dry-run"})
                except Exception:
                    rows.append({"key": key, "branch": branch, "status": "skipped:no-issue"})
                continue
            ok = _st.set_issue_status(stored, "done", dry_run=False)
            rows.append({"key": key, "branch": branch,
                         "status": "closed" if ok else "failed"})
        if json_output:
            print(json.dumps({"results": rows}, indent=2, ensure_ascii=False))
        else:
            _print_rows(rows, json_output=False, csv_output=False,
                        columns=["key", "branch", "status"],
                        title="Close released",
                        empty="no linked worktrees")
        return

    if merged:
        # _status_cells(refresh_pr=True) is the merged/closed source of truth;
        # live harnesses and invalid worktrees are skipped, never torn down.
        rows = []
        for key, entry in links.items():
            branch = entry.get("branch", "")
            from . import cli as _cli
            _sc = _cli._status_cells
            state = ((_sc(key, dict(entry), refresh_pr=True).get("pr_data")
                     or {}).get("state", "") or "").upper()
            if state not in ("MERGED", "CLOSED"):
                rows.append({"key": key, "branch": branch,
                             "status": f"skipped:{state.lower() or 'no-pr'}"})
                continue
            if store.active_harness(key):
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:live-harness"})
                continue
            if entry.get("active", 1) == 0:
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:deactivated"})
                continue
            wt = entry.get("worktree", "")
            if not wt or not worktrees.is_valid_worktree(wt):
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:invalid"})
                continue
            gate = None if force else row_prodtest_gate(key, dict(entry))
            if gate and gate.startswith("blocked:"):
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:prodtest-stale"})
                continue
            rows.append(_cleanup_one(key, dict(entry), force, yes, dry_run,
                               json_output, squash=not no_squash,
                               merge=False, hard_delete=hard_delete))
        if json_output:
            print(json.dumps({"results": rows}, indent=2, ensure_ascii=False))
        else:
            _print_rows(rows, json_output=False, csv_output=False,
                        columns=["key", "branch", "status"],
                        title="Cleanup (merged/closed)",
                        empty="no linked worktrees")
        return

    try:
        key, entry, _ = worktrees.resolve_any(ref, links)
    except worktrees.NoLinkedState:
        _fail(
            f"no linked state for {ref}.\n"
            "  Run `workagent link list` to see linked worktrees.",
            EXIT_USAGE,
        )
    if not key or not entry:
        _fail(
            f"no linked state for {ref}.\n"
            "  Run `workagent link list` to see linked worktrees.",
            EXIT_USAGE,
        )
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    if not branch:
        _fail(f"linked entry for {ref} has no branch", EXIT_USAGE)
    if key != ref and not yes and not dry_run and sys.stdin.isatty():
        try:
            answer = input(f"ref {ref!r} matches worktree {key!r} — use it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    if dry_run:
        _print_result({"dry_run": True, "key": key, "repo": str(repo), "branch": branch,
                       "pr_url": entry.get("pr_url", ""), "force": force,
                       "merge": entry.get("pr_url", "")}, json_output)
        return

    if merge_only:
        result = _merge_only_one(key, dict(entry), force, json_output,
                                 squash=not no_squash)
        if json_output:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not yes and not force and sys.stdin.isatty():
        try:
            answer = input(f"Remove worktree + delete branch '{branch}'? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    result = _cleanup_one(key, dict(entry), force, True, False, json_output,
                          squash=not no_squash, hard_delete=hard_delete)
    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def _merge_only_one(key: str, entry: dict, force: bool, json_output: bool,
                    squash: bool = True) -> dict:
    """Squash-merge the open PR/MR for *key*; tear nothing down.

    Shared resolution/startup with _cleanup_one up to PR dispatch, then
    stops: no issue close, no git-wt cleanup, no branch delete, no row
    change. Conflicted/blocked PRs fail closed (exit 1) unless --force.
    """
    from . import cli as _cli  # shim: tests patch cli.*
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    pr_url = entry.get("pr_url", "")
    worktree = entry.get("worktree", "")
    stored_ref = entry.get("issue", "") or entry.get("pr_url", "") or key
    effective = worktrees.effective_repo_for_entry(dict(entry), default=repo) or repo
    repo = effective
    host_cwd = worktree if worktree and Path(worktree).is_dir() else str(repo)
    try:
        parsed = refs.parse_ref(key if not key.startswith("pr:") else stored_ref)
    except HarnessError:
        try:
            parsed = refs.parse_ref(stored_ref)
        except HarnessError:
            parsed = {"kind": "", "tool": "", "repo": "", "number": "",
                      "url": pr_url or stored_ref}
    _sc2 = _cli._status_cells
    state = (_sc2(key, dict(entry), refresh_pr=False).get("pr_data")
             or {}).get("state", "")
    tool = _repo_tool(str(repo))
    recorded_url = pr_url
    pr_url, fresh, picked = _cli._resolve_branch_pr(branch, recorded_url, host_cwd,
                                                     tool=tool)
    if picked:
        eprint(f"note: acting on {pr_url} (latest for branch {branch}).")
    fresh_state = ((fresh or {}).get("state") or "").lower()
    if not force and pr_url and fresh_state in ("open", "opened"):
        gate = row_prodtest_gate(key, dict(entry))
        if gate == "blocked:stale":
            _fail(f"prodtest plan is stale for {_prodtest_issue_key(key, entry)} — "
                  "run `workagent prodtest plan` first (or re-run with --force).",
                  EXIT_GENERAL)
        if gate == "blocked:invalid-plan":
            _fail(f"prodtest plan is invalid for {_prodtest_issue_key(key, entry)} — "
                  "fix the plan (or re-run with --force).", EXIT_GENERAL)
    open_states = ("open", "opened")
    if pr_url and fresh_state in open_states:
        if not force and _pr_conflicted(fresh):
            _fail(f"{pr_url} conflicts with its destination branch — remote branch kept.\n"
                  "  Resolve the conflict on the host, then re-run.",
                  EXIT_GENERAL)
        blocked_reason = _merge_blocked_reason(fresh)
        if not force and blocked_reason:
            _fail(f"{pr_url} cannot merge yet ({blocked_reason}) — remote branch kept.\n"
                  "  Resolve it on the host (threads/pipeline/approvals), then re-run.",
                  EXIT_GENERAL)
        try:
            _cli._merge_pr(pr_url, squash=squash, cwd=host_cwd,
                           source_branch=branch, no_squash=not squash)
        except HarnessError as e:
            if force and not _pr_missing(str(e)):
                eprint(f"note: {pr_url} could not be merged ({e}); left open.")
                return {"key": key, "branch": branch, "status": "merge-failed",
                        "pr_url": pr_url}
            raise
        if not json_output:
            eprint(f"{key}: merged {pr_url} (worktree/branch/link kept)")
        return {"key": key, "branch": branch, "status": "merged",
                "pr_url": pr_url}
    if pr_url and fresh_state in ("merged", "closed"):
        if not json_output:
            eprint(f"note: {pr_url} already {fresh_state}; nothing to merge.")
        return {"key": key, "branch": branch, "status": "already-merged",
                "pr_url": pr_url}
    parsed_kind = parsed.get("kind", "")
    _fail(f"no open PR/MR to merge for {key} ({parsed_kind or 'no PR tracked'}).\n"
          "  Push the branch and open a PR first, then re-run --merge-only.",
          EXIT_GENERAL)


def _cleanup_one(key: str, entry: dict, force: bool, yes: bool,
                 dry_run: bool, json_output: bool, squash: bool = True,
                 merge: bool | None = None, hard_delete: bool = False) -> dict:
    """Clean one linked worktree: merge open PR/MR, git-wt cleanup, close + drop link.

    Shared by `cleanup <ref>` (after confirmation) and the `cleanup --merged`
    loop (after the merged/live-harness/validity gates). The caller owns ref
    resolution and prompts; this only acts. Returns a summary row with
    "status": "cleaned" or "dry-run".

    Soft-delete (default): the MR merge-or-close still runs, but all local
    and remote teardown is skipped and the row is stamped deleted_at
    instead of dropped — recoverable for 7 days via `link undelete`.
    `hard_delete` restores the immediate full teardown.
    """
    from . import cli as _cli  # shim: tests patch cli.*
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    pr_url = entry.get("pr_url", "")
    worktree = entry.get("worktree", "")
    stored_ref = entry.get("issue", "") or entry.get("pr_url", "") or key
    # Host CLIs (gh/glab) bind host+remote from the process cwd. The
    # recorded repo can be stale (e.g. a link created while cwd was an
    # unrelated checkout), so prefer the live worktree — it resolves to
    # the real repo via --git-common-dir — and repair the stored repo.
    effective = worktrees.effective_repo_for_entry(dict(entry), default=repo) or repo
    if effective != repo:
        entry["repo"] = str(effective)
        if not dry_run:
            store.record_link(key, {"repo": str(effective)})
    repo = effective
    # cwd for host-CLI calls: the worktree when it still exists (its
    # origin remote is authoritative), else the effective repo root.
    host_cwd = worktree if worktree and Path(worktree).is_dir() else str(repo)
    try:
        parsed = refs.parse_ref(key if not key.startswith("pr:") else stored_ref)
    except HarnessError:
        try:
            parsed = refs.parse_ref(stored_ref)
        except HarnessError:
            parsed = {"kind": "", "tool": "", "repo": "", "number": "",
                      "url": pr_url or stored_ref}
    if dry_run:
        return {"key": key, "branch": branch, "repo": str(repo),
                "pr_url": pr_url, "force": force, "status": "dry-run",
                "merge": pr_url or ""}
    _sc2 = _cli._status_cells
    state = (_sc2(key, dict(entry), refresh_pr=False).get("pr_data")
             or {}).get("state", "")
    tool = _repo_tool(str(repo))
    recorded_url = pr_url
    pr_url, fresh, picked = _cli._resolve_branch_pr(branch, recorded_url, host_cwd,
                                               tool=tool)
    if picked:
        eprint(f"note: acting on {pr_url} (latest for branch {branch}).")
    fresh_state = ((fresh or {}).get("state") or "").lower()
    # A 404 on the fresh lookup means the cached row is stale but the host
    # PR is gone; fall back to cached state unless it is explicitly open.
    if fresh is None and state.lower() not in ("open", "opened"):
        fresh = {"state": state}
        fresh_state = (state or "").lower()
    conflicted = _pr_conflicted(fresh)
    merged_now = False
    remote_deleted = False
    if merge is None:
        # Merge only on explicitly open states; unknown ("") falls back
        # to close-and-remove so offline/cache-miss cleanup still works.
        merge = bool(pr_url) and state in ("open", "opened", "OPEN", "OPENED")
    state_norm = (state or "").lower()
    # Remote close BEFORE local teardown: host-CLI calls run with cwd
    # inside the live worktree (its origin remote is authoritative). After
    # git-wt removes the worktree the cwd no longer exists and glab falls
    # back to the server cwd — an unrelated checkout — failing with
    # "no git remote points to a known host" (IPG-953). Merge failures
    # raise BEFORE teardown — nothing is torn down on a failed merge.
    open_states = ("open", "opened")
    if (not force and pr_url and fresh_state in open_states and conflicted):
        # Rule 2: conflicted PR stays open; nothing is torn down.
        _fail(f"{pr_url} conflicts with its destination branch — remote branch kept.\n"
              "  Resolve the conflict on the host, then re-run cleanup.",
              EXIT_GENERAL)
    blocked_reason = _merge_blocked_reason(fresh)
    if (not force and pr_url and fresh_state in open_states and blocked_reason):
        # Same Rule 2 for non-conflict blockers (e.g. unresolved
        # discussions, pipeline gate): the host would refuse the merge
        # (405), so fail closed BEFORE attempting it — nothing torn down.
        _fail(f"{pr_url} cannot merge yet ({blocked_reason}) — remote branch kept.\n"
              "  Resolve it on the host (threads/pipeline/approvals), then re-run cleanup.",
              EXIT_GENERAL)
    if not force and pr_url and fresh_state in open_states:
        gate = row_prodtest_gate(key, dict(entry))
        if gate == "blocked:stale":
            _fail(f"prodtest plan is stale for {_prodtest_issue_key(key, entry)} — "
                  "run `workagent prodtest plan` first (or re-run with --force).",
                  EXIT_GENERAL)
        if gate == "blocked:invalid-plan":
            _fail(f"prodtest plan is invalid for {_prodtest_issue_key(key, entry)} — "
                  "fix the plan (or re-run with --force).", EXIT_GENERAL)
    if merge:
        try:
            _cli._merge_pr(pr_url, squash=squash, cwd=host_cwd,
                           source_branch=branch, no_squash=not squash)
        except HarnessError as e:
            if force and not _pr_missing(str(e)):
                # Rule 3: --force merge failed (e.g. conflicts) → leave the
                # PR open, keep the remote branch, continue local cleanup.
                _cli._close_issue(parsed, force)
                eprint(f"note: {pr_url} could not be merged ({e}); left open.")
                merged_now = False
            else:
                # 404 without force, or conflict without force: PR stays
                # open, user notified via the raised error; nothing torn down.
                raise
        else:
            merged_now = True
    else:
        _cli._close_issue(parsed, force)
        if state_norm in ("merged", "closed"):
            # Already terminal on the host: closing is a no-op (glab fails
            # "already been merged"), so skip straight to local teardown.
            eprint(f"note: {pr_url} already {state_norm}; skipping remote close.")
        else:
            try:
                _cli._close_pr(parsed, pr_url, force, cwd=host_cwd)
            except HarnessError as e:
                if _pr_missing(str(e)):
                    # 404: host PR is gone; keep the remote branch and the
                    # link so nothing is lost silently.
                    _fail(f"{pr_url} not found on the host (404) — remote branch kept.\n"
                          "  Re-run with the branch deleted manually once confirmed.",
                          EXIT_GENERAL)
                raise
    # git-wt only removes the local branch (delete_branch=False below):
    # remote deletion is workagent's call. Rule 1: delete the remote branch
    # iff the PR/MR merged with this branch as its source (merged here, or
    # already-merged for this branch, verified via head_ref). Unmerged PRs —
    # closed, open, conflicted, or left open after a failed --force merge —
    # keep their remote branch.
    head_ref = ""
    if pr_url and (merged_now or fresh_state == "merged"):
        try:
            head_ref = (refs.fetch_pr_info(refs.parse_ref(pr_url),
                                           cwd=host_cwd).get("head_ref") or "")
        except HarnessError as e:
            eprint(f"warning: cannot verify PR head branch for {pr_url}: {e}")
            head_ref = branch if merged_now else ""
    delete_remote = bool(branch) and (merged_now or fresh_state == "merged") \
        and (not head_ref or head_ref == branch)
    if not hard_delete:
        # Soft-delete: no local/remote teardown — worktree files, branch
        # (local + remote) all survive. Only the row is stamped.
        deleted_at = store.soft_delete_worktree(key)
        tail = " (soft-deleted; recover within 7 days via `link undelete`)"
        if not json_output:
            eprint(f"{key}: cleaned{tail}")
        try:
            from . import cli_harness as _h
            _h.record_headless_attempt(
                key, "cleanup", [key, "--yes", "--force"] if force else [key, "--yes"],
                f"harness command: workagent cleanup {key}"
                f" -> cleaned{tail} pr={pr_url or 'none'}",
                exit_code=0, metadata={"mode": "cleanup",
                                       "soft_deleted": True})
        except Exception:
            pass
        return {"key": key, "branch": branch, "status": "cleaned",
                "deleted_at": deleted_at}
    cleanup = gitwt.cleanup_worktree(repo, branch, delete_branch=False,
                                     force=force, yes=yes)
    try:
        _cli.run_cmd("git", "-C", str(repo), "branch", "-D", branch)
        cleanup.setdefault("actions", []).append(f"deleted local branch {branch}")
    except HarnessError:
        pass
    if delete_remote:
        # Remote branch goes LAST: local cleanup already succeeded.
        try:
            _cli.run_cmd("git", "-C", str(repo), "push", "origin",
                    "--delete", branch)
            remote_deleted = True
        except HarnessError as e:
            eprint(f"warning: remote branch delete failed: {e}")
    store.delete_worktree_row(key)
    if not json_output:
        tail = " (remote branch deleted)" if remote_deleted \
            else " (remote branch kept)"
        eprint(f"{key}: cleaned{tail}")
    return {"key": key, "branch": branch, "status": "cleaned", "cleanup": cleanup,
            "remote_deleted": remote_deleted}
