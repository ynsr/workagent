"""Closed-loop background PR & CI auto-watcher (issue #154 / T11).

Observes active worktrees with opt-in ``auto_fix_ci`` or ``auto_fix_reviews``,
refreshes per-worktree CI + review-thread snapshots each pass, computes a
deduplication signature ``sha256(head_sha + ci_state +
sorted(unresolved_thread_ids) + sorted(failing_check_names))``, skips worktrees
with an active harness lock, paused flow, or matching ``last_nudge_signature``,
dispatches a detached/headless fix run, and records a ``nudge_routed`` event
(``nudge_failed`` on spawn errors) in ``worktree_events``.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from . import store, store_timeline

logger = logging.getLogger(__name__)

_UNSET: Any = object()
DEFAULT_WATCH_INTERVAL = 60.0


def _tool_for_pr_url(pr_url: str, cached: dict[str, Any] | None = None) -> str | None:
    """Derive the host CLI (gh/glab) for a PR/MR URL, preferring cached tool."""
    from . import refs_ci
    return refs_ci.infer_pr_tool(pr_url, cached) or None


def _cached_pr_url(cached: dict[str, Any] | None) -> str:
    """Extract PR/MR URL from a pr_cache entry (nested pr.url or top-level fallback)."""
    if not isinstance(cached, dict):
        return ""
    pr = cached.get("pr")
    nested = pr.get("url") if isinstance(pr, dict) else ""
    return str(nested or cached.get("url") or cached.get("pr_url") or "").strip()


def _refresh_worktree_inputs(
    ref_key: str,
    entry: dict[str, Any],
    branch: str,
    cached: dict[str, Any] | None,
    head_sha: str,
    *,
    refresh_fn: Callable[..., tuple[str | None, str, list[dict[str, Any]], dict | None, list[dict[str, Any]]]] | None = None,
) -> tuple[str | None, str, list[dict[str, Any]], dict | None, list[dict[str, Any]]]:
    """Refresh CI + review-thread snapshots for one worktree (soft-fail).

    Returns ``(ci_state, ci_url, checks, review_stats, threads)``. By default
    fetches via ``refs.fetch_ci_details`` / ``refs.fetch_pr_review_details``
    and persists through ``store.cache_ci_status`` / ``store.cache_review_stats``
    / ``store.replace_pr_checks`` / ``store.replace_pr_review_threads``; a
    caller-supplied *refresh_fn* may replace the fetch step (tests). Any
    lookup failure keeps the last-known snapshots — nothing is cached and the
    pass proceeds on stale data with a logged warning.
    """
    if refresh_fn is not None:
        return refresh_fn(ref_key, entry, branch, cached, head_sha)
    pr_url = str(entry.get("pr_url") or "").strip()
    if not pr_url.startswith("http"):
        pr_url = _cached_pr_url(cached)
    tool = _tool_for_pr_url(pr_url, cached)
    if not pr_url or not tool:
        return None, "", [], None, []
    cwd = str(entry.get("repo") or entry.get("worktree") or "").strip() or None
    try:
        from . import refs as _refs, refs_ci as _refs_ci
        ci_state, ci_url, checks = _refs.fetch_ci_details(tool, pr_url, cwd)
        if ci_state == "failure" and checks:
            _refs_ci.populate_failing_check_logs(
                tool, checks, cwd=cwd, fallback_url=ci_url
            )
        stats, threads = _refs.fetch_pr_review_details(tool, pr_url, cwd)
    except Exception as exc:
        logger.warning("watcher refresh failed for %s (%s): %s", ref_key, pr_url, exc)
        return None, "", [], None, []
    try:
        if ci_state is not None and branch:
            for c in checks:
                if not c.get("commit_sha") and head_sha:
                    c["commit_sha"] = head_sha
            store.cache_ci_status(branch, ci_state, sha=head_sha or "", ci_url=ci_url,
                                  repo=str(entry.get("repo") or ""))
            store.replace_pr_checks(ref_key, checks)
        if stats is not None and branch:
            store.cache_review_stats(branch, stats, sha=head_sha or "",
                                     repo=str(entry.get("repo") or ""))
            store.replace_pr_review_threads(ref_key, threads)
    except Exception as exc:
        logger.warning("watcher refresh persist failed for %s: %s", ref_key, exc)
    return ci_state, ci_url, checks, stats, threads


def compute_nudge_signature(
    head_sha: str,
    ci_state: str,
    unresolved_thread_ids: Sequence[str],
    failing_checks: Sequence[str | dict[str, Any]] | None = None,
) -> str:
    """Return deterministic sha256 signature for deduplicating watcher nudges."""
    ids = ",".join(sorted(str(tid).strip() for tid in unresolved_thread_ids if str(tid).strip()))
    fails: list[str] = []
    for fc in failing_checks or ():
        if isinstance(fc, dict):
            name = str(fc.get("name") or "").strip()
            concl = str(fc.get("conclusion") or fc.get("status") or "").strip().lower()
            fails.append(f"{name}:{concl}" if concl else name)
        else:
            fails.append(str(fc).strip())
    fail_part = ",".join(sorted(f for f in fails if f))
    raw = f"{(head_sha or '').strip()}|{(ci_state or '').strip().lower()}|{ids}|{fail_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def effective_auto_fix(
    entry: dict[str, Any],
    repo_entry: dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    """Resolve effective ``(auto_fix_ci, auto_fix_reviews)`` for a worktree.

    Explicit boolean overrides on ``entry`` (stored in ``worktrees.payload``)
    take precedence over the repo-level defaults; ``None`` or missing inherits
    from ``repo_entry`` (which defaults to ``False``).
    """
    repo_ci = bool((repo_entry or {}).get("auto_fix_ci", False))
    repo_rev = bool((repo_entry or {}).get("auto_fix_reviews", False))
    wt_ci = entry.get("auto_fix_ci")
    wt_rev = entry.get("auto_fix_reviews")
    return (
        bool(wt_ci) if isinstance(wt_ci, bool) else repo_ci,
        bool(wt_rev) if isinstance(wt_rev, bool) else repo_rev,
    )


def update_worktree_watcher_fields(
    ref_key: str,
    *,
    auto_fix_ci: bool | None = _UNSET,
    auto_fix_reviews: bool | None = _UNSET,
    last_nudge_signature: str | None = _UNSET,
) -> dict[str, Any]:
    """Persist watcher overrides and/or ``last_nudge_signature`` on a worktree payload.

    Passing ``None`` for ``auto_fix_ci`` or ``auto_fix_reviews`` clears the
    per-worktree override so it inherits from the repo again.
    Raises ``KeyError`` if *ref_key* is not found.
    """
    links = store.load_links(include_inactive=True)
    if ref_key not in links:
        raise KeyError(f"unknown worktree: {ref_key}")
    entry = dict(links[ref_key])
    if auto_fix_ci is not _UNSET:
        if auto_fix_ci is None:
            entry.pop("auto_fix_ci", None)
        else:
            entry["auto_fix_ci"] = bool(auto_fix_ci)
    if auto_fix_reviews is not _UNSET:
        if auto_fix_reviews is None:
            entry.pop("auto_fix_reviews", None)
        else:
            entry["auto_fix_reviews"] = bool(auto_fix_reviews)
    if last_nudge_signature is not _UNSET:
        if last_nudge_signature is None:
            entry.pop("last_nudge_signature", None)
        else:
            entry["last_nudge_signature"] = str(last_nudge_signature)
    store.record_link(ref_key, entry)
    return entry


def _repo_for_entry(
    entry: dict[str, Any],
    repos_map: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    """Match a worktree entry to its registered ``(repo_name, repo_dict)``."""
    repo_path = str(entry.get("repo") or "").strip()
    if repo_path:
        norm = str(Path(repo_path).resolve())
        for name, info in repos_map.items():
            p = str(info.get("path") or "").strip()
            if p and (p == repo_path or str(Path(p).resolve()) == norm):
                return name, info
    repo_key = str(entry.get("repo_key") or "").strip()
    if repo_key and repo_key in repos_map:
        return repo_key, repos_map[repo_key]
    return "", None


def _head_sha_for_entry(entry: dict[str, Any], pr_cached: dict[str, Any] | None) -> str:
    """Return current head SHA from pr_cache or git rev-parse HEAD."""
    if pr_cached and pr_cached.get("branch_tip"):
        return str(pr_cached["branch_tip"])
    wt = str(entry.get("worktree") or "").strip()
    if wt and Path(wt).is_dir():
        try:
            out = subprocess.check_output(
                ["git", "-C", wt, "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).strip()
            if out:
                return out
        except Exception:
            pass
    return ""


def _markdown_fence(text: str) -> str:
    """Return a backtick fence strictly longer than any run in *text* (min 3)."""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _build_ci_fix_prompt(failing_checks: Sequence[dict[str, Any]]) -> str:
    """Build a concise CI-fix prompt including failing check names, URLs, and 50-line log tails."""
    from . import refs_ci
    lines = [
        "CI checks are failing on this PR. Diagnose and fix the failures, run the relevant tests, and push the fix.",
        "",
        "Failing checks:",
    ]
    for chk in failing_checks:
        name = str(chk.get("name") or "unnamed")
        conclusion = str(chk.get("conclusion") or chk.get("status") or "failure")
        url = str(chk.get("url") or "")
        tail = refs_ci._tail_log_lines(str(chk.get("log_tail") or ""), max_lines=refs_ci.CI_LOG_TAIL_LINES)
        header = f"- {name} ({conclusion})" + (f" — URL: {url}" if url else "")
        lines.append(header)
        if tail:
            fence = _markdown_fence(tail)
            lines.append(fence)
            lines.append(tail)
            lines.append(fence)
    return "\n".join(lines)


def _default_dispatch(ref_key: str, kind: str, argv: list[str], entry: dict[str, Any]) -> subprocess.Popen:
    """Spawn a detached workagent CLI child process for the watcher fix run.

    Returns the ``Popen`` handle; raises on spawn failure so the pass records
    an ``error`` row (and never persists ``last_nudge_signature`` for it).
    """
    return subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_watch_once(
    *,
    dry_run: bool = False,
    repo_filter: str | None = None,
    refresh: bool = True,
    dispatch_fn: Callable[[str, str, list[str], dict[str, Any]], Any] | None = None,
    refresh_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute one observer pass across all active worktrees.

    With ``refresh=True`` (default) each opted-in worktree refreshes its CI
    + review-thread snapshots before deciding; ``refresh=False`` reuses the
    last-known snapshots (offline/tests). A failed refresh keeps last-known
    snapshots and flags the row ``stale_inputs=True``.

    Returns ``{"checked": int, "dispatched": int, "skipped": int, "errors": int, "results": [...]}``.
    """
    links = store.load_links()
    repos_map = store.load_repos()
    db_path = store._sqlite_path()
    dispatcher = dispatch_fn or _default_dispatch

    results: list[dict[str, Any]] = []
    dispatched = 0
    skipped = 0
    errors = 0

    for ref_key, entry in sorted(links.items()):
        repo_name, repo_entry = _repo_for_entry(entry, repos_map)
        if repo_filter and repo_name != repo_filter:
            continue

        auto_ci, auto_rev = effective_auto_fix(entry, repo_entry)
        if not auto_ci and not auto_rev:
            skipped += 1
            results.append({"ref": ref_key, "action": "skip", "reason": "disabled"})
            continue

        # Guard 1: active harness lock
        if store.active_harness(ref_key):
            skipped += 1
            results.append({"ref": ref_key, "action": "skip", "reason": "active_harness"})
            continue

        # Guard 2: paused flow
        try:
            flows = store.list_flows(worktree=ref_key)
        except Exception:
            flows = []
        if any(str(f.get("status") or "").lower() == "paused" for f in flows):
            skipped += 1
            results.append({"ref": ref_key, "action": "skip", "reason": "paused_flow"})
            continue

        branch = str(entry.get("branch") or "")
        repo_path = str(entry.get("repo") or "")
        cached = store.lookup_pr_cache(branch, repo_path) if branch else None
        head_sha = _head_sha_for_entry(entry, cached)

        stale_inputs = False
        if refresh and (auto_ci or auto_rev):
            fresh = _refresh_worktree_inputs(ref_key, entry, branch, cached,
                                             head_sha, refresh_fn=refresh_fn)
            fresh_ci, _fresh_url, fresh_checks, fresh_stats, _fresh_threads = fresh
            if fresh_ci is None and not fresh_checks and fresh_stats is None:
                # Refresh fetched nothing new: stay on last-known snapshots.
                stale_inputs = True
            else:
                # Re-read the persisted snapshots updated by the refresh.
                cached = store.lookup_pr_cache(branch, repo_path) if branch else cached

        checks = store_timeline.list_pr_checks(db_path, ref_key) if db_path else []
        failing = [c for c in checks if store_timeline.is_failing_check(c)]
        ci_state = str((cached or {}).get("ci") or "").lower()
        if not ci_state and failing:
            ci_state = "failure"

        unresolved = (
            store_timeline.list_pr_review_threads(db_path, ref_key, unresolved_only=True)
            if db_path
            else []
        )
        thread_ids = [str(t["thread_id"]) for t in unresolved if t.get("thread_id")]

        failing_names = [str(c.get("name")) for c in failing if c.get("name")]
        signature = compute_nudge_signature(head_sha, ci_state, thread_ids, failing_names)

        # Guard 3: deduplication via last_nudge_signature
        if signature and signature == str(entry.get("last_nudge_signature") or ""):
            skipped += 1
            results.append({
                "ref": ref_key,
                "action": "skip",
                "reason": "already_nudged",
                "signature": signature,
            })
            continue

        kind: str | None = None
        argv: list[str] = []
        summary = ""
        if auto_ci and (ci_state == "failure" or failing):
            kind = "ci_failure"
            prompt = _build_ci_fix_prompt(failing)
            argv = [
                sys.executable, "-m", "workagent",
                "adhoc", ref_key, "--acp", "--yes", "--prompt", prompt,
            ]
            names = [str(c.get("name")) for c in failing if c.get("name")]
            summary = f"Auto-fix CI routed ({', '.join(names) if names else 'CI failure'})"
        elif auto_rev and thread_ids:
            kind = "review_comments"
            argv = [
                sys.executable, "-m", "workagent",
                "review", ref_key, "--fix-comments", "--acp", "--yes",
            ]
            summary = f"Auto-fix review comments routed ({len(thread_ids)} unresolved thread(s))"

        if kind is None:
            skipped += 1
            results.append({
                "ref": ref_key,
                "action": "skip",
                "reason": "clean",
                "signature": signature,
                "stale_inputs": stale_inputs,
            })
            continue

        if dry_run:
            results.append({
                "ref": ref_key,
                "action": "dry_run",
                "kind": kind,
                "signature": signature,
                "argv": argv,
                "summary": summary,
            })
            continue

        try:
            proc = dispatcher(ref_key, kind, argv, entry)
            pid = getattr(proc, "pid", None) if proc is not None else None
        except Exception as exc:
            logger.warning("watcher dispatch failed for %s (%s): %s", ref_key, kind, exc)
            errors += 1
            results.append({
                "ref": ref_key,
                "action": "error",
                "kind": kind,
                "error": str(exc),
            })
            if db_path:
                try:
                    store_timeline.append_worktree_event(
                        db_path, ref_key, "nudge_failed", summary=str(exc)[:300],
                        details={"kind": kind, "signature": signature, "head_sha": head_sha},
                    )
                except Exception:
                    pass
            continue

        update_worktree_watcher_fields(ref_key, last_nudge_signature=signature)
        if db_path:
            store_timeline.append_worktree_event(
                db_path,
                ref_key,
                "nudge_routed",
                summary=summary,
                details={
                    "kind": kind,
                    "signature": signature,
                    "head_sha": head_sha,
                    "ci_state": ci_state,
                    "unresolved_threads": len(thread_ids),
                    "failing_checks": [str(c.get("name")) for c in failing if c.get("name")],
                    "stale_inputs": stale_inputs,
                    "dispatch_pid": pid,
                },
            )
        dispatched += 1
        results.append({
            "ref": ref_key,
            "action": "dispatched",
            "kind": kind,
            "signature": signature,
            "summary": summary,
            "pid": pid,
            "stale_inputs": stale_inputs,
        })

    return {
        "checked": len(results),
        "dispatched": dispatched,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }


async def watch_loop_async(
    stop_event: asyncio.Event,
    *,
    interval: float = DEFAULT_WATCH_INTERVAL,
    repo_filter: str | None = None,
) -> None:
    """Async background loop for ``workagent serve`` lifespan.

    Cancels cleanly when *stop_event* is set or the task is cancelled.
    """
    poll = max(1.0, float(interval or DEFAULT_WATCH_INTERVAL))
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_watch_once, repo_filter=repo_filter)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("watcher pass failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
