"""`review` command + helpers (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import os
import re
import shlex
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
from .cli_harness import _guard_harness, _run_harness, append_extra_prompt, is_exec_mode
from .errors import HarnessError

_COMP = _init_completions()
_complete_repos = _COMP["repos"]
_complete_refs = _COMP["refs"]

# ── review ────────────────────────────────────────────────────────────


def _is_reviewed(key: str, entry: dict) -> bool:
    """Reviewed AND the worktree tip still matches the recorded tip sha;
    new commits on the worktree invalidate the reviewed state."""
    if not entry.get("reviewed"):
        return False
    wt = entry.get("worktree", "")
    tip = repos.branch_tip(wt) if wt and Path(wt).is_dir() else ""
    return bool(tip) and tip == entry.get("reviewed_at", "")


def _reviewable_keys(links: dict, force: bool = False) -> list[tuple[str, str]]:
    """(key, pr_url) pairs to review under --all: live worktree, resolvable
    PR/MR, no live harness, and not already reviewed at the current tip.

    A non-pr link carrying a pr_url whose pr:<url> entry also exists is an
    alias: reviewed/tip live on the pr: entry, so the alias is skipped and
    one worktree/PR yields exactly one child (links for different PRs stay
    distinct).

    Issue #28: worktrees with an in-progress review/PR-comment (a live
    harness or any unresolved PR thread) are skipped unless force=True;
    worktrees without a PR/MR can never be reviewed. --force-all re-includes
    already-reviewed worktrees (still PR/MR only).
    """
    out = []
    for k, v in links.items():
        if v.get("active", 1) == 0:
            eprint(f"{k}: deactivated — skipping")
            continue
        own_pr = v.get("pr_url", "")
        if own_pr and not k.startswith("pr:") and f"pr:{own_pr}" in links:
            continue
        if _is_reviewed(k, v) and not force:
            continue
        pr = worktrees.worktree_pr_url(k, v)
        if pr and not worktrees.is_valid_worktree(v.get("worktree", "")):
            continue
        if not pr:
            wt = v.get("worktree", "")
            if wt and Path(wt).is_dir():
                eprint(f"{k}: no PR/MR — skipping")
            continue
        if store.active_harness(k):
            eprint(f"{k}: harness already live — skipping")
            continue
        if not force and _has_unresolved_comments(v, pr):
            eprint(f"{k}: unresolved PR comments — skipping (use --force-all)")
            continue
        out.append((k, pr))
    return out


def _has_unresolved_comments(entry: dict, pr_url: str) -> bool:
    """True when the cached PR stats record unresolved threads (issue #28).

    Cache-only by design: _reviewable_keys must not spawn host-CLI lookups
    (the --all tests run fully offline). Live stats ride the status cells
    via _reviews_cell and persist through cache_review_stats.
    """
    branch = str(entry.get("branch", ""))
    repo = str(entry.get("repo", "") or "")
    cached = store.lookup_pr_cache(branch, repo)
    n = (cached or {}).get("unresolved")
    return isinstance(n, int) and n > 0


def _mr_touches_prodtest_plan(pr_url: str, cwd: str) -> bool:
    """True when the MR diff contains a prodtest plan file (best effort)."""
    from .errors import run_cmd
    try:
        parsed = refs.parse_ref(pr_url)
        tool, number = parsed.get("tool", ""), str(parsed.get("number", "") or "")
        if tool == "glab":
            out = run_cmd("glab", "mr", "diff", str(number), "--name-only",
                          cwd=cwd, echo=False) or ""
            return ".workagent/prodtest/plans/" in out
        if tool == "gh":
            repo = parsed.get("repo", "") or ""
            out = run_cmd("gh", "pr", "diff", str(number), "--repo", repo,
                          "--name-only", cwd=cwd or None, echo=False) or ""
            return ".workagent/prodtest/plans/" in out
    except Exception:
        return False
    return False


def _prodtest_checklist_block(plan_rel: str = ".workagent/prodtest/plans/") -> str:
    """Review checklist block for prodtest plans (appended to review prompts)."""
    return ("\n\nProdtest plan checklist:\n"
            f"- Confirm the plan under {plan_rel} covers the MR diff "
            "(subjects, risks, version gate).\n"
            "- Flag missing subjects, stale diff hashes, or [HUMAN] placeholders.")


def _prodtest_fix_warning(pr_url: str, repo_dir: str) -> str:
    """Loud stale/invalid-plan warning for fix-comments entry (best effort).

    Spec §4: fixing review comments must not silently proceed against a
    stale or invalid prodtest plan. Never raises; '' when nothing to warn.
    """
    from pathlib import Path as _P
    if not _mr_touches_prodtest_plan(pr_url, repo_dir):
        return ""
    warn = ("\n\nWARNING: this MR touches a prodtest plan "
            "(.workagent/prodtest/plans/). Do NOT fix review comments "
            "against a stale or invalid plan:")
    try:
        from . import cli_cleanup as _cc
        from . import store as _store
        key = _review_issue_ref(pr_url) or pr_url
        item = None
        try:
            item = _store.get_prodtest(key)
        except Exception:
            item = None
        repo = str(repo_dir or "")
        try:
            from . import repos as _repos
            default = _repos.default_branch(_P(repo)) if repo and _P(repo).is_dir() else "main"
        except Exception:
            default = "main"
        current = _cc.current_prodtest_diff(repo, default or "main")
        gate = _cc.prodtest_gate(key, item, current_diff=current, config={"mode": "block"},
                                manifest_path=_cc.manifest_path_for(key, repo_dir=repo))
        if gate == "blocked:stale":
            return (warn + " the stored plan diff hash does not match the current "
                    "diff — re-run `prodtest plan` first, then re-review.")
        if gate == "blocked:invalid-plan":
            return (warn + " the plan fails validation — fix the plan first, "
                    "then re-review.")
        if gate and gate.startswith("warn:"):
            return (warn + f" plan gate reports {gate} — verify the plan is current.")
    except Exception:
        return ""
    return ""


def _same_repo(entry: dict, repo_dir: Path) -> bool:
    """True when the recorded entry belongs to ``repo_dir`` (or either side unrecorded)."""
    recorded = (entry.get("repo", "") or "").strip()
    if not recorded or not str(repo_dir):
        return True
    try:
        return Path(recorded).expanduser().resolve() == Path(str(repo_dir)).expanduser().resolve()
    except OSError:
        return recorded == str(repo_dir)

def _pinned_repo_for_pr(ref: str, parsed: dict | None) -> str:
    """Worktree-pinned repo for a PR/MR URL ("" when unknowable).

    Only rows that own this PR may pin the repo: the canonical link key,
    a stored ``pr_url`` match, or the live reuse row for the PR head
    branch. A bare substring hit (e.g. PR number ``89`` inside branch
    ``IPG-1089`` — issue #91) must never pin an unrelated repo: the
    host-scoped tracker guard would then reject the real repo.
    """
    links = store.load_links(include_deleted=True)
    if parsed is not None and parsed.get("kind") in ("pr", "mr"):
        canon = refs.pr_key(str(parsed.get("url") or ""))
        if canon and isinstance(links.get(canon), dict):
            return str(links[canon].get("repo", "") or "")
        hit = [k for k, v in links.items()
               if isinstance(v, dict) and v.get("pr_url") == ref
               and v.get("repo")]
        if len(hit) == 1:
            return str(links[hit[0]].get("repo", "") or "")
        try:
            head = (refs.fetch_pr_info(parsed) or {}).get("head_ref", "")
        except Exception:
            head = ""
        if head:
            exact = [k for k, v in links.items()
                     if isinstance(v, dict) and v.get("branch") == head
                     and v.get("repo") and not v.get("deleted_at")]
            if len(exact) == 1:
                return str(links[exact[0]].get("repo", "") or "")
        return ""
    _pre = worktrees.resolve_worktree(ref, links)
    if isinstance(_pre, str):
        return str((links.get(_pre) or {}).get("repo", "") or "")
    return ""

def _review_key_for(pr_url: str, worktree: str, branch: str, repo: str = "") -> str:
    """Link key review state lives under: the recorded row for this
    worktree/branch when one exists, else the source branch name.

    Reviewing an already-tracked worktree must reuse its row (stamping
    pr_url on it) — never insert a second row for the same path/(repo,
    branch) (which collides on the UNIQUE keys). The match is
    repo-scoped: the same branch name in another repo is a different
    worktree (e.g. IPG-1002 in projectx vs ipg-commons).
    """
    return (worktrees.recorded_key(worktree, branch, store.load_links(include_deleted=True), repo=repo)
            or branch)


def _adopt_fix_reuse(result: dict, review_key: str) -> bool:
    """Point *result* at the review run owning the pinned transcript.

    The web server passes the saved run `file_path` as explicit
    --session-file. The owning run is found by exact file_path match (the
    pinned path itself — minted filenames never match their stem, so
    stem lookup silently misses). When the newest row on the path is a
    continuation (`fix_comments`) row rather than the review identity,
    the key's latest review run is used instead, as long as it names the
    same transcript (exact path, then legacy stem). When the resolved
    owner is the latest non-running review run for the key, mark the
    reuse so the harness layer treats the path as a resume target
    verbatim instead of rescoping it under fix_comments/ (same stem,
    wrong dir starts fresh). Returns True when the reuse was
    adopted.
    """
    try:
        from pathlib import Path as _Path
        from . import store_sqlite as _sq
        pinned = str(result.get("session_file", "") or "")
        if not pinned:
            return False
        db = _sq.db_path()
        if not db.exists():
            return False
        existing = (_sq.run_by_file_path(db, pinned, include_deleted_worktrees=True)
                    or _sq.run_by_file_stem(db, _Path(pinned).stem,
                                            include_deleted_worktrees=True))
        latest = _sq.latest_review_run(db, review_key,
                                       include_deleted_worktrees=True)
        if existing is None or str(existing.get("session_type", "") or "") != "review":
            # Every launch row names the transcript it runs, so once a
            # fix-continue ran on this path the newest row is `fix_comments`,
            # not the owning review row. Resolve the identity from the key's
            # latest review run instead, and only when it names this exact
            # transcript (path, then legacy stem).
            if latest is None:
                return False
            stored = str(latest.get("file_path", "") or "")
            if stored != pinned and _Path(stored).stem != _Path(pinned).stem:
                return False
            existing = latest
        if str(existing.get("worktree_ref", "")) != review_key:
            return False
        if latest is not None and int(latest.get("id", 0)) != int(existing.get("id", 0)):
            return False
        if str(existing.get("state", "")) == "running":
            return False
        result["reuse_run_id"] = int(existing["id"])
        result["session_file"] = str(_sq.resolve_transcript_path(
            str(existing.get("file_path", "") or pinned)) or pinned)
        return True
    except Exception:
        return False


def _apply_fix_session(result: dict, review_key: str, fix_comments: bool,
                       use_review_session: bool, explicit_file: str | None) -> None:
    """Resolve the fix-comments transcript strategy into *result*.

    Fresh fix_comments transcript by default (linked back via
    metadata.fixed_from_run_id). Explicit --session-file always wins.
    --use-review-session continues the latest non-running review run
    instead (reuse id + transcript); when the explicit file names that
    run the reuse is adopted so the transcript is resumed verbatim.
    Non-fix runs leave result untouched.
    """
    if explicit_file:
        result["session_file"] = explicit_file
        if fix_comments and use_review_session:
            _adopt_fix_reuse(result, review_key)
        return
    if not fix_comments:
        return
    from . import store_sqlite as _sq
    db = _sq.db_path()
    latest = _sq.latest_review_run(db, review_key, include_deleted_worktrees=True) if db.exists() else None
    if latest and use_review_session:
        result["reuse_run_id"] = int(latest["id"])
        stored = str(latest.get("file_path") or "")
        resolved = _sq.resolve_transcript_path(stored)
        result["session_file"] = str(resolved) if resolved else stored
    elif latest:
        result["session_metadata"] = {"fixed_from_run_id": int(latest["id"])}


def _child_argv(pr: str, fix: bool = False, harness: "str | None" = None,
                post_comments: bool = False, acp: bool = False,
                run_mode: "str | None" = None, model: "str | None" = None,
                effort: "str | None" = None) -> list[str]:
    """Child review argv for --all fan-out and then-fix chaining."""
    argv = [sys.executable, "-m", "workagent", "review", pr, "--no-tty"]
    if harness:
        argv += ["--harness", harness]
    if acp:
        argv.append("--acp")
    if run_mode:
        argv += ["--run-mode", run_mode]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if post_comments:
        return argv + ["--post-comments"] + (["--fix"] if fix else [])
    return argv


def _chain_fix_command(review_key: str, pr_url: str, harness_name: str,
                       use_review_session: bool, env_file: str | None,
                       extra_prompt: str | None, acp: bool = False,
                       run_mode: "str | None" = None, model: "str | None" = None,
                       effort: "str | None" = None) -> list[str]:
    """Headless follow-up argv for --then-fix-comments: a second review
    invocation in pure fix mode against the same ref. A fresh fix
    transcript is the default; --use-review-session continues the
    just-finished review (resolved inside the child via latest_review_run).
    Exit-code gating lives with the caller: only chain on review exit 0.
    """
    _acp_mode = bool(acp) or (run_mode or "").strip().lower() == "acp"
    argv = [sys.executable, "-m", "workagent", "review", pr_url,
            "--fix-comments", "--no-tty"] + ([] if _acp_mode else ["--launch"])
    if use_review_session:
        argv.append("--use-review-session")
    if harness_name:
        argv += ["--harness", harness_name]
    if acp:
        argv.append("--acp")
    if run_mode:
        argv += ["--run-mode", run_mode]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if env_file:
        argv += ["--env-file", env_file]
    if extra_prompt:
        argv += ["--extra-prompt", extra_prompt]
    return argv

def _print_then_fix_hint(pr_url: str, harness_name: str,
                         use_review_session: bool) -> None:
    """Preview/terminal follow-up: these modes return instantly (no exit
    code to gate on), so print the exact manual command instead of
    chaining. The user runs it after the review yields."""
    cmd = " ".join(shlex.quote(a) for a in
                   _chain_fix_command(pr_url, pr_url, harness_name,
                                      use_review_session, None, None)[3:])
    eprint(f"then-fix: after the review yields, run: workagent {cmd}")


def _review_status(ref: str, fix_comments: bool,
                   dry_run: bool, no_status: bool) -> None:
    """Pre status transition for review/fix-comments (best effort)."""
    if no_status or not ref:
        return
    from . import issue_status as _st
    _st.set_issue_status(ref, "ready to release" if fix_comments else "code review",
                         dry_run=dry_run)


def _review_post_fix(ref: str, fix_comments: bool, launch: bool,
                     terminal: bool, no_status: bool, acp: bool = False,
                     run_mode: "str | None" = None) -> None:
    """Post status transition after a headless fix-comments run (best effort)."""
    if no_status or not ref or not fix_comments or not is_exec_mode(launch, acp, run_mode) or terminal:
        return
    from . import issue_status as _st
    _st.set_issue_status(ref, "ready to release", dry_run=False)


def _review_verdict_settle(ref: str, pr_url: str, cwd: str, no_status: bool,
                           fix_comments: bool, launch: bool, terminal: bool,
                           acp: bool = False, run_mode: "str | None" = None) -> None:
    """Post-review verdict transition (best effort).

    Only for plain review runs (not --fix-comments) after headless success:
    approved/merged → `ready to release`, changes-requested → `in progress`,
    and only when the issue is currently in `code review`. Opt-out via
    --no-status; dry-run/preview/terminal never settle.
    """
    if no_status or not ref or fix_comments or not is_exec_mode(launch, acp, run_mode) or terminal:
        return
    from . import issue_status as _st
    _st.settle_issue_post_review(ref, _st.pr_review_state(pr_url, cwd=cwd))

def _review_issue_ref(ref: str) -> str:
    """Best-effort linked issue ref for a PR/MR URL or worktree key."""
    try:
        links = store.load_links(include_deleted=True)
    except Exception:
        return ""
    entry = links.get(ref, {}) or {}
    for cand in (str(entry.get("issue_url", "") or ""),
                 str(entry.get("issue", "") or "")):
        if cand and cand != ref:
            try:
                parsed = refs.parse_ref(cand)
            except HarnessError:
                continue
            if parsed.get("kind") == "issue":
                return cand
    pr_url = str(entry.get("pr_url", "") or (ref if ref.startswith("http") else ""))
    branch = str(entry.get("branch", "") or "")
    if not pr_url and not branch:
        for key, ent in (links or {}).items():
            if str((ent or {}).get("pr_url", "") or "") == ref and key != ref:
                pr_url = str((ent or {}).get("pr_url", "") or "")
                branch = str((ent or {}).get("branch", "") or "")
                break
    if branch and branch != ref:
        try:
            parsed = refs.parse_ref(branch)
        except HarnessError:
            parsed = None
        if parsed is not None and parsed.get("kind") == "issue":
            return branch
    if pr_url:
        try:
            info = refs.fetch_pr_info(refs.parse_ref(pr_url))
        except HarnessError:
            info = {}
        for cand in (str((info or {}).get("issue_key", "") or ""),
                     str((info or {}).get("body", "") or "")):
            if not cand:
                continue
            try:
                parsed = refs.parse_ref(cand)
            except HarnessError:
                parsed = None
            if parsed is not None and parsed.get("kind") == "issue":
                return cand
    return ""


def _run_then_fix(review_key: str, pr_url: str, harness_name: str,
                  use_review_session: bool, env_file: str | None,
                  extra_prompt: str | None, then_fix: bool,
                  launch: bool, terminal: bool, acp: bool = False,
                  run_mode: "str | None" = None, model: "str | None" = None,
                  effort: "str | None" = None) -> None:
    """--then-fix-comments dispatcher. No-op unless requested. Headless
    (--launch) chains the fix child in-process after review exit 0 (the
    review returning at all means success — failures raise before we get
    here); its exit code propagates. Preview/terminal print the manual
    follow-up instead — they detach instantly with nothing to gate on."""
    if not then_fix:
        return
    if not is_exec_mode(launch, acp, run_mode) or terminal:
        _print_then_fix_hint(pr_url, harness_name, use_review_session)
        return
    argv = _chain_fix_command(review_key, pr_url, harness_name,
                              use_review_session, env_file, extra_prompt,
                              acp=acp, run_mode=run_mode, model=model, effort=effort)
    eprint(f"then-fix: review done, chaining: {' '.join(shlex.quote(a) for a in argv)}")
    child_env = None
    parent_rid = (os.environ.get("WORKAGENT_RUN_ID")
                  or os.environ.get("WORKAGENT_PARENT_RUN_ID") or "").strip()
    if parent_rid:
        child_env = {**os.environ, "WORKAGENT_PARENT_RUN_ID": parent_rid}
    if child_env is not None:
        proc = subprocess.run(argv, env=child_env)
    else:
        proc = subprocess.run(argv)
    if proc.returncode != 0:
        _fail(f"then-fix child exited {proc.returncode}", proc.returncode)

def _ensure_branch_worktree(repo_dir: Path, head_ref: str, base_branch: str,
                            pr_url: str, *, issue_url: str = "") -> tuple[str, str, str]:
    """Reuse or create the worktree for a PR/MR source branch.

    Returns (key, worktree, branch): an already-recorded row for this
    (repo, branch) wins (its key is reused with pr_url stamped on it);
    otherwise a fresh worktree is created via git-wt and recorded under
    the bare branch name. Branch names are unique per repo
    (UNIQUE (repo_key, branch)), so the same branch in another repo is
    simply a different row — e.g. IPG-1002 in projectx vs ipg-commons.
    """
    links = store.load_links(include_deleted=True)
    reuse = worktrees.recorded_key("", head_ref, links, repo=str(repo_dir))
    if isinstance(reuse, str):
        entry = links.get(reuse, {})
        worktree = entry.get("worktree", "")
        branch = entry.get("branch", head_ref)
        rec = {"pr_url": pr_url, "worktree": worktree,
               "branch": branch, "repo": str(repo_dir)}
        if issue_url and not entry.get("issue_url"):
            rec["issue_url"] = issue_url
        store.record_link(reuse, rec)
        return reuse, worktree, branch
    wt = gitwt.start_worktree(repo_dir, branch=head_ref, base=base_branch)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", head_ref)
    links_now = store.load_links(include_deleted=True)
    prior = worktrees.recorded_key(
        worktree, branch, links_now, repo=str(repo_dir))
    use_key = prior if isinstance(prior, str) else head_ref
    rec = {"pr_url": pr_url, "worktree": worktree,
           "branch": branch, "repo": str(repo_dir)}
    if issue_url and not (links_now.get(use_key) or {}).get("issue_url"):
        rec["issue_url"] = issue_url
    store.record_link(use_key, rec)
    try:
        repos.register_repo(repos.repo_name(repo_dir), repo_dir)
    except HarnessError:
        pass
    return use_key, worktree, branch


def auto_register_pr_worktree(
    ref: str,
    parsed: dict,
    *,
    repo: str | None = None,
    depth: int = 7,
    yes: bool = False,
    dry_run: bool = False,
) -> tuple[str, dict]:
    """Create/adopt and link the worktree for an unlinked PR/MR ref.

    Shared by ``flow run``, ``action run``, ``adhoc``, and ``sync`` so any
    PR/MR URL works without a manual ``workagent review`` pre-step.
    Returns ``(key, entry)``; when ``dry_run`` is True returns ``("", info)``.
    """
    if not yes and not dry_run and sys.stdin.isatty():
        eprint(f"auto-register worktree for {ref}? [y/N] ", end="")
        if (sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    _pre_repo = _pinned_repo_for_pr(ref, parsed) if not repo else ""
    repo_dir, _base, tid, outcome = trackers.resolve_repo_for_ref(
        parsed, repo, Path.cwd(), depth=depth, yes=yes,
        persist=not dry_run, pinned=_pre_repo)
    if outcome == "recorded" and tid and not dry_run:
        eprint(f"note: linked tracker {tid} to repo {repo_dir}")
    base_branch = repos.default_branch(repo_dir)
    info, head_ref = refs.require_head_ref(parsed, repo_dir, ref)
    if dry_run:
        return "", {"dry_run": True, "repo": str(repo_dir),
                    "pr_url": parsed["url"], "key": head_ref,
                    "branch": head_ref}
    issue_url = refs.derive_issue_url(head_ref, info, parsed)
    key, worktree, branch = _ensure_branch_worktree(
        repo_dir, head_ref, base_branch, parsed["url"], issue_url=issue_url)
    eprint(f"note: auto-registered worktree {key} ({worktree}) for {ref}")
    links = store.load_links(include_deleted=True)
    entry = links.get(key) or {
        "worktree": worktree,
        "branch": branch,
        "repo": str(repo_dir),
        "pr_url": parsed["url"],
        **({"issue_url": issue_url} if issue_url else {}),
    }
    return key, entry


def _mark_reviewed(key: str, worktree: str) -> None:
    """Persist reviewed=True + the current worktree tip on the review row."""
    entry = store.load_links().get(key, {})
    entry["reviewed"] = True
    entry["reviewed_at"] = repos.branch_tip(worktree)
    store.record_link(key, entry)


def _clear_reviewed(key: str) -> None:
    """Revert reviewed/reviewed_at after a failed (non-exec) harness launch.

    record_link merges, which cannot drop keys — rewrite the entry without
    them instead (other fields preserved), same as `link remove`.
    """
    links = store.load_links(include_inactive=True)
    entry = links.get(key)
    if not entry:
        return
    entry.pop("reviewed", None)
    entry.pop("reviewed_at", None)
    links[key] = entry
    store.save_links(links)


@app.command("review")
@_catch_harness_errors
def review(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="PR/MR URL, OWNER/REPO#NUM, or worktree ref (key/branch/worktree); optional with --all."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Registered name, local path, or clone URL."),
    depth: int = typer.Option(7, "--depth", help="Clone depth for repo URLs."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured)."),
    acp: bool = typer.Option(False, "--acp", help="Run this step via ACP stdio (JSON-RPC) instead of CLI argv."),
    run_mode: Optional[str] = typer.Option(None, "--run-mode", help="Run mode: preview|headless|acp|terminal (default: derived from --launch/--terminal/--acp)."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id (ACP-discovered; non-ACP harnesses pass --model through when supported)."),
    effort: Optional[str] = typer.Option(None, "--effort", help="Effort level (ACP-discovered; non-ACP harnesses pass --effort through when supported)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively."),
    launch: bool = typer.Option(False, "-L", "--launch", help="Launch the harness in the worktree (default: print the harness command and land in an interactive shell inside the worktree)."),
    terminal: bool = typer.Option(False, "--terminal", help="Open the harness command in the OS terminal instead of running headless (mutually exclusive with --launch)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    all_wts: bool = typer.Option(False, "--all", help="Review every not-reviewed linked worktree in parallel (non-TTY)."),
    sequential: bool = typer.Option(False, "--sequential", help="With --all: review one-by-one instead of in parallel."),
    fix: bool = typer.Option(False, "--fix", help="With --all: children auto-fix identified issues after yielding."),
    force_all: bool = typer.Option(False, "--force-all", help="With --all: include already-reviewed and unresolved-comment worktrees too (still needs a PR/MR)."),
    post_comments: bool = typer.Option(False, "--post-comments", hidden=True, help="Append the auto-comment prompt segment (set by --all)."),
    fix_comments: bool = typer.Option(False, "--fix-comments", help="Fix open PR/MR review comments instead of reviewing: validate each finding, apply, resolve/close, commit and push."),
    then_fix_comments: bool = typer.Option(False, "--then-fix-comments", help="After a successful review run (exit 0), chain a fix-comments run in the same harness session. Headless runs it automatically; preview/terminal print the follow-up command for manual use. (deprecated: use flows; ignored inside flow templates)"),
    use_review_session: bool = typer.Option(False, "--use-review-session", help="With --fix-comments or --then-fix-comments: continue the latest review session instead of starting a fresh fix session."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path to resume or populate."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before terminal/headless harness commands (default: env_file in config.json, auto-created)."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the harness prompt."),
    no_status: bool = typer.Option(False, "--no-status", help="Skip automatic issue status transitions."),
) -> None:
    """Create worktree from PR/MR and launch review.

    Example:
      workagent review OWNER/REPO#33 --no-tty
      workagent review OWNER/REPO#33 --launch
      workagent review --all [--sequential] [--fix]
    """
    if not isinstance(session_file, str):
        session_file = None
    if terminal and launch:
        _fail("--terminal cannot be used with --launch (pick one run mode)", EXIT_USAGE)
    if sequential and not all_wts:
        _fail("--sequential requires --all", EXIT_USAGE)
    if fix and not all_wts:
        _fail("--fix requires --all", EXIT_USAGE)
    if force_all and not all_wts:
        _fail("--force-all requires --all", EXIT_USAGE)
    if session_file and all_wts:
        _fail("--session-file cannot be used with --all (one transcript per worktree — omit it and each launch gets its own file)", EXIT_USAGE)
    if fix and fix_comments:
        _fail("--fix cannot be used with --fix-comments", EXIT_USAGE)
    if then_fix_comments and fix_comments:
        _fail("--then-fix-comments cannot be used with --fix-comments (it already chains a fix after the review)", EXIT_USAGE)
    if then_fix_comments and all_wts:
        _fail("--then-fix-comments cannot be used with --all (chain per-PR instead: review <pr> --then-fix-comments)", EXIT_USAGE)
    if post_comments and fix_comments:
        _fail("--post-comments cannot be used with --fix-comments", EXIT_USAGE)
    if use_review_session and not (fix_comments or then_fix_comments):
        _fail("--use-review-session requires --fix-comments or --then-fix-comments", EXIT_USAGE)
    if ref is None and not all_wts:
        _fail("missing PR/MR ref or worktree key\n"
              "  Pass a ref, or use --all to review every not-reviewed worktree.",
              EXIT_USAGE)
    if all_wts:
        reviewable = _reviewable_keys(store.load_links(include_inactive=True), force=force_all)
        if not reviewable:
            eprint("nothing to review")
            return

        def _all_child_argv(pr: str) -> list[str]:
            argv = _child_argv(pr, fix=fix, harness=harness,
                               post_comments=(not fix_comments),
                               acp=acp, run_mode=run_mode,
                               model=model, effort=effort)
            # --all children always need these flags, independent of the
            # module-level _child_argv contract used by the unit test.
            if env_file:
                argv += ["--env-file", env_file]
            if no_status:
                argv += ["--no-status"]
            if fix_comments:
                return argv + ["--fix-comments"]
            return argv


        def _child_cmd(pr: str) -> str:
            return " ".join(shlex.quote(a) for a in _all_child_argv(pr))

        def _spawn(key: str, pr: str, step_index: int | None = None):
            repo = store.load_links().get(key, {}).get("repo", "")
            cwd = repo if repo and Path(repo).is_dir() else os.getcwd()
            child_env = None
            parent_rid = (os.environ.get("WORKAGENT_RUN_ID")
                          or os.environ.get("WORKAGENT_PARENT_RUN_ID") or "").strip()
            if parent_rid or step_index is not None:
                child_env = dict(os.environ)
                if parent_rid:
                    child_env["WORKAGENT_PARENT_RUN_ID"] = parent_rid
                if step_index is not None:
                    child_env["WORKAGENT_STEP_INDEX"] = str(step_index)
            # Child stdout/stderr inherit ours; the --post-comments child
            # appends the auto-comment segment to its review prompt.
            if child_env is not None:
                return subprocess.Popen(_all_child_argv(pr), cwd=cwd, env=child_env)
            return subprocess.Popen(_all_child_argv(pr), cwd=cwd)

        if dry_run:
            rows = [{"key": k, "pr_url": pr, "command": _child_cmd(pr)}
                    for k, pr in reviewable]
            _print_rows(rows, json_output, csv_output=False,
                        columns=["key", "pr_url", "command"],
                        title="Review plan (dry-run)",
                        empty="nothing to review")
            return
        if not is_exec_mode(launch, acp, run_mode):
            rows = [{"key": k, "pr_url": pr, "command": _child_cmd(pr),
                     "exit_code": ""} for k, pr in reviewable]
            _print_rows(rows, json_output, csv_output=False,
                        columns=["key", "pr_url", "command", "exit_code"],
                        title="Review runs", empty="nothing reviewed")
            from . import cli_harness as _h
            h_name = harness or store.default_harness()
            for k, pr in reviewable:
                _h.record_headless_attempt(
                    k, "review", _all_child_argv(pr),
                    f"harness command: {_child_cmd(pr)}",
                    exit_code=0, harness_name=h_name,
                    fix_comments=fix_comments,
                    metadata={"pr_url": pr, "mode": "all-preview"})
            return
        rows: list[dict] = []

        if sequential:
            for idx, (key, pr) in enumerate(reviewable):
                proc = _spawn(key, pr, step_index=idx)
                rows.append({"key": key, "pr_url": pr, "exit_code": proc.wait()})
        else:
            procs = [(key, pr, _spawn(key, pr, step_index=idx))
                     for idx, (key, pr) in enumerate(reviewable)]
            for key, pr, proc in procs:
                rows.append({"key": key, "pr_url": pr, "exit_code": proc.wait()})
        if not json_output:
            for row in rows:
                eprint(f"{row['key']}: review child exit {row['exit_code']}")
        _print_rows(rows, json_output, csv_output=False,
                    columns=["key", "pr_url", "exit_code"],
                    title="Review runs", empty="nothing reviewed")
        return
    session_ref = ref
    parse_err: HarnessError | None = None
    try:
        parsed = refs.parse_ref(ref)
    except HarnessError as e:
        parse_err = e
        parsed = None
    if parsed is None or parsed["kind"] not in ("pr", "mr"):
        # Worktree ref (key/branch/path): resolve to the recorded (or discovered) PR/MR.
        links = store.load_links(include_deleted=True)
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is not None:
            key = worktrees.pick_worktree(ref, resolved, links)
            entry = links.get(key, {})
            pr_url0 = worktrees.worktree_pr_url(key, entry) if entry else None
            if not pr_url0:
                branch0 = str((entry or {}).get("branch", "") or "")
                hint0 = f" (branch {branch0!r})" if branch0 else ""
                _fail(f"worktree {key}{hint0} has no recorded PR/MR.\n"
                      "  Create the PR first, or pass its URL "
                      "(e.g. https://github.com/OWNER/REPO/pull/NUM).", EXIT_USAGE)
            eprint(f"note: worktree {key} → {pr_url0}")
            ref = pr_url0
            parsed = refs.parse_ref(ref)
        elif parse_err is not None:
            raise parse_err
    if parsed is not None and parsed["kind"] == "issue_or_pr":
        gh_repo = str(parsed.get("repo", "") or "")
        if not gh_repo and parsed.get("tool") == "gh" and repo:
            # Bare number + explicit --repo: derive OWNER/REPO from the
            # repo's origin remote so `review 46 --repo my-checkout` works.
            try:
                remote = repos.remote_url(repos.resolve_repo(repo, Path.cwd(), depth=depth))
            except HarnessError:
                remote = None
            m = re.match(r"^(?:https?://(?:[^@/]+@)?github\.com/|ssh://(?:[^@/]+@)?github\.com/|[^@]+@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$",
                         (remote or ""), re.IGNORECASE) if remote else None
            gh_repo = m.group(1) if m else ""
        if parsed.get("tool") == "gh" and gh_repo:
            # OWNER/REPO#NUM (or github:O/R#N, or bare NUM with --repo):
            # ask gh whether NUM is a pull request and rewrite to its
            # canonical URL. Pure issues fail inside pr_url_for_number
            # with an actionable message.
            ref = refs.pr_url_for_number(gh_repo, parsed["number"])
            parsed = refs.parse_ref(ref)
        else:
            hint = "github.com/OWNER/REPO/pull/NUM" if not gh_repo else f"github.com/{gh_repo}/pull/NUM"
            _fail(f"{ref} is ambiguous — `review` needs a PR/MR URL (e.g. "
                  f"https://{hint}).", EXIT_USAGE)
    _pre_repo = _pinned_repo_for_pr(ref, parsed) if not repo else ""
    repo_dir, base_branch, tid, outcome = trackers.resolve_repo_for_ref(
        parsed, repo, Path.cwd(), depth=depth, yes=yes,
        persist=not dry_run, pinned=_pre_repo)
    if outcome == "recorded" and tid and not dry_run:
        eprint(f"note: linked tracker {tid} to repo {repo_dir}")
    base_branch = repos.default_branch(repo_dir)
    harness_name = harness or store.default_harness()
    pr_url = parsed["url"]
    info: dict = {}
    if parsed["kind"] in ("pr", "mr"):
        try:
            info = refs.fetch_pr_info(parsed, cwd=str(repo_dir))
        except HarnessError as e:
            eprint(f"warning: {e}")
    head_ref = info.get("head_ref", "")

    if dry_run:
        _print_result({"dry_run": True, "repo": str(repo_dir), "pr_url": pr_url,
                       "harness": harness_name, "head_ref": head_ref,
                       "tracker": tid, "tracker_link": outcome}, json_output)
        _review_status(_review_issue_ref(ref) or ref or "", fix_comments,
                       dry_run=True, no_status=no_status)
        return

    reuse_key = worktrees.resolve_worktree(head_ref or session_ref, store.load_links())
    if isinstance(reuse_key, str):
        _reuse_entry = store.load_links().get(reuse_key, {})
        _reuse_branch = _reuse_entry.get("branch", "") or ""
        if _reuse_branch and _reuse_branch != (head_ref or session_ref):
            # Fuzzy hit (bare-ticket substring) on another branch — e.g. IPG-1054
            # in ipg-commons vs projectx. Reusing it would review in the wrong
            # worktree and stamp the wrong row. Treat as no reuse (strict) and let
            # _ensure_branch_worktree create/reuse the right per-(repo, branch) row.
            # Empty recorded branch (legacy rows) still reuses, as before.
            reuse_key = None
        elif not _same_repo(_reuse_entry, repo_dir):
            reuse_key = None  # same branch name, other repo — not our worktree
    if isinstance(reuse_key, str):
        reuse_entry = store.load_links().get(reuse_key, {})
        if reuse_entry.get("worktree") and Path(reuse_entry["worktree"]).is_dir():
            worktree = reuse_entry["worktree"]
            branch = reuse_entry.get("branch", head_ref)
            review_key = _review_key_for(pr_url, worktree, branch, repo=str(repo_dir))
            _rec = {"pr_url": pr_url, "worktree": worktree,
                    "branch": branch, "repo": str(repo_dir)}
            _derived_issue = refs.derive_issue_url(branch, info, parsed)
            if _derived_issue and not reuse_entry.get("issue_url"):
                _rec["issue_url"] = _derived_issue
            store.record_link(review_key, _rec)
            if fix_comments:
                prompt = backend.prompt_for_fix_comments(pr_url, worktree=worktree, branch=branch)
                prompt += _prodtest_fix_warning(pr_url, str(repo_dir))
            else:
                prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)
                if _mr_touches_prodtest_plan(pr_url, str(repo_dir)):
                    prompt += _prodtest_checklist_block()
            if post_comments and not fix_comments:
                prompt += "\n\nAuto add all comments to the PR/MR at yielding and don't wait for user approval"
            if fix and not fix_comments:
                prompt += "\n\nAuto-fix all identified issues after yielding and don't wait for user approval."
            prompt = append_extra_prompt(prompt, extra_prompt)
            result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
                      "harness": harness_name, "command": "review",
                      "fix_comments": fix_comments}
            eprint(f"worktree: {worktree}  branch: {branch}")
            if then_fix_comments and not fix_comments and not no_tty and not terminal:
                # Interactive (no --no-tty, no --terminal): _run_harness execs
                # the harness and never returns, so the post-run _run_then_fix
                # below is unreachable. Print the manual follow-up up front
                # (same text as the post-run hint) so the run log carries it.
                _print_then_fix_hint(pr_url, harness_name, use_review_session)
            if is_exec_mode(launch, acp, run_mode):
                # Same staleness rule as sync: review a pulled tip, never a
                # stale local. Fast-forward/merge origin/<branch> first; a
                # pull conflict aborts loudly (exit 1) instead of launching
                # the harness on half-merged state. Without --launch (preview) the pull is skipped:
                # the command is printed without touching git.
                try:
                    pulled = sync_mod.pull_branch(Path(worktree), branch)
                except HarnessError as e:
                    _fail(f"{e}", EXIT_GENERAL)
                if pulled != "up-to-date":
                    eprint(f"{review_key}: pulled origin/{branch} ({pulled})")
                _guard_harness(review_key, worktree)
                _mark_reviewed(review_key, worktree)
            _apply_fix_session(result, review_key, fix_comments, use_review_session, session_file)
            _review_status(_review_issue_ref(review_key) or _review_issue_ref(ref) or "", fix_comments,
                           dry_run=False, no_status=no_status)
            _post_status = _review_issue_ref(review_key) or _review_issue_ref(ref) or ""
            try:
                _run_harness(harness_name, prompt, worktree, str(repo_dir),
                             no_tty, launch, result, json_output,
                             run_key=review_key, session_file=result.get("session_file"),
                             terminal=terminal, env_file=env_file,
                             acp=acp, run_mode=run_mode, model=model, effort=effort)
            except HarnessError:
                if is_exec_mode(launch, acp, run_mode):
                    _clear_reviewed(review_key)
                raise
            _review_post_fix(_post_status, fix_comments, launch, terminal, no_status,
                             acp=acp, run_mode=run_mode)
            _review_verdict_settle(_post_status, pr_url, str(repo_dir), no_status,
                                  fix_comments, launch, terminal,
                                  acp=acp, run_mode=run_mode)
            _run_then_fix(review_key, pr_url, harness_name, use_review_session,
                          env_file, extra_prompt, then_fix_comments,
                          launch, terminal, acp=acp, run_mode=run_mode,
                          model=model, effort=effort)
            return
    if not head_ref:
        _fail(f"could not determine the MR head branch for {pr_url}.\n"
              f"  Run `git fetch origin` in {repo_dir} and check `glab`/`gh` auth for that host.",
              EXIT_USAGE)
    review_key, worktree, branch = _ensure_branch_worktree(
        repo_dir, head_ref, base_branch, pr_url,
        issue_url=refs.derive_issue_url(head_ref, info, parsed))

    if fix_comments:
        prompt = backend.prompt_for_fix_comments(pr_url, worktree=worktree, branch=branch)
        prompt += _prodtest_fix_warning(pr_url, str(repo_dir))
    else:
        prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)
        if _mr_touches_prodtest_plan(pr_url, str(repo_dir)):
            prompt += _prodtest_checklist_block()

    if post_comments and not fix_comments:
        prompt += "\n\nAuto add all comments to the PR/MR at yielding and don't wait for user approval"
    if fix and not fix_comments:
        prompt += "\n\nAuto-fix all identified issues after yielding and don't wait for user approval."
    prompt = append_extra_prompt(prompt, extra_prompt)
    result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
              "harness": harness_name, "command": "review",
              "fix_comments": fix_comments}
    eprint(f"worktree: {worktree}  branch: {branch}")
    if then_fix_comments and not fix_comments and not no_tty and not terminal:
        _print_then_fix_hint(pr_url, harness_name, use_review_session)
    if is_exec_mode(launch, acp, run_mode):
        _guard_harness(review_key, worktree)
        _mark_reviewed(review_key, worktree)
    _apply_fix_session(result, review_key, fix_comments, use_review_session, session_file)
    _review_status(_review_issue_ref(review_key) or _review_issue_ref(ref) or "", fix_comments,
                   dry_run=False, no_status=no_status)
    _post_ref = _review_issue_ref(review_key) or _review_issue_ref(ref) or ""
    try:
        _run_harness(harness_name, prompt, worktree, str(repo_dir), no_tty,
                     launch, result, json_output, run_key=review_key,
                     session_file=result.get("session_file"), terminal=terminal,
                     env_file=env_file,
                     acp=acp, run_mode=run_mode, model=model, effort=effort)
    except HarnessError:
        if is_exec_mode(launch, acp, run_mode):
            _clear_reviewed(review_key)
        raise
    _review_post_fix(_post_ref, fix_comments, launch, terminal, no_status,
                     acp=acp, run_mode=run_mode)
    _review_verdict_settle(_post_ref, pr_url, str(repo_dir), no_status,
                           fix_comments, launch, terminal,
                           acp=acp, run_mode=run_mode)
    _run_then_fix(review_key, pr_url, harness_name, use_review_session,
                  env_file, extra_prompt, then_fix_comments,
                  launch, terminal, acp=acp, run_mode=run_mode,
                  model=model, effort=effort)


