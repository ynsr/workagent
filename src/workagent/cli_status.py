"""status helpers + `status` command (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import typer

from . import backend, refs, repos, store, swr, worktrees
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
from .cli_harness import _harness_cell
from .cli_repo_util import (
    _CI_STYLES,
    _CI_SYMBOLS,
    _PR_STYLES,
    _ci_swr,
    _ci_ttl,
    _negative_swr,
    _negative_ttl,
    _repo_default_branch,
    _repo_tool,
    _status_open_ttl,
    _status_swr,
    _status_ttl,
)
from .errors import HarnessError, run_cmd

_COMP = _init_completions()


def _patched(name: str):
    """Test seam: resolve via the `cli` shim so monkeypatch.setattr(cli, …) applies."""
    from . import cli as _cli
    return getattr(_cli, name)
_complete_refs = _COMP["refs"]

from .cli_status_cells import (  # noqa: F401,E402
    _delivery_status,
    _fmt_counts,
    _fmt_pr,
    _git_tip,
    _recorded_pr,
    _seed_recorded_pr,
)

def _age_freshness(ts: str, fresh_ttl: int, swr_win: int) -> Literal["fresh", "stale", "expired"]:
    if not ts:
        return "expired"
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except ValueError:
        return "expired"
    if age < timedelta(seconds=fresh_ttl):
        return "fresh"
    if age < timedelta(seconds=swr_win):
        return "stale"
    return "expired"


def _pr_freshness(
    cached: dict | None,
    branch_tip: str | None = None,
    base_tip: str | None = None,
) -> Literal["fresh", "stale", "expired"]:
    """Tri-state freshness for a pr_cache entry: fresh | stale | expired."""
    if not cached:
        return "expired"
    if branch_tip is not None and cached.get("branch_tip") != branch_tip:
        return "expired"
    if base_tip is not None and cached.get("base_tip") != base_tip:
        return "expired"
    pr = cached.get("pr")
    if pr:
        state = str((pr or {}).get("state") or "").lower()
        fresh_ttl = _status_ttl() if state in ("merged", "closed") else _status_open_ttl()
        swr_win = _status_swr()
    else:
        fresh_ttl = _negative_ttl()
        swr_win = _negative_swr()
    return _age_freshness(str(cached.get("checked_at", "") or ""), fresh_ttl, swr_win)


def _cache_usable(cached: dict) -> bool:
    """Cached PR cell still servable (within its SWR window)."""
    return _pr_freshness(cached) != "expired"


def _fetch_origins(links: dict, refresh: bool) -> None:
    """--refresh-pr companion: fetch origin once per distinct repo so
    behind/ahead counts and PR lookups see fresh remote tips (worktrees
    share remote-tracking refs via the common git dir). Soft-fails."""
    if not refresh:
        return
    seen: set[str] = set()
    for e in links.values():
        wt = e.get("worktree", "")
        if not wt or not Path(wt).exists():
            continue
        repo = e.get("repo", "") or wt
        if repo in seen:
            continue
        seen.add(repo)
        try:
            _patched("run_cmd")("git", "-C", wt, "fetch", "origin", "--prune", echo=False)
        except HarnessError as err:
            eprint(f"warning: fetch failed for {repo}: {err}")


def _ci_freshness(
    cached: dict | None,
    branch_tip: str | None = None,
) -> Literal["fresh", "stale", "expired"]:
    """Tri-state freshness for cached CI status."""
    if not cached:
        return "expired"
    sha = branch_tip or ""
    if (cached.get("ci_sha") or "") != sha:
        return "expired"
    return _age_freshness(str(cached.get("ci_checked_at", "") or ""), _ci_ttl(), _ci_swr())


def _ci_usable(cached: dict) -> bool:
    """Cached CI still servable within its SWR window."""
    return _age_freshness(str(cached.get("ci_checked_at", "") or ""), _ci_ttl(), _ci_swr()) != "expired"


def _query_pr(repo: str, branch: str) -> tuple[dict | None, str | None]:
    """(pr, tool) via the detected host CLI, falling back to the other one.

    Returns (None, None) when no host CLI is available; raises HarnessError
    when a CLI exists but every lookup fails (caller keeps cached data).
    """
    tool = _patched("_repo_tool")(repo)
    if not tool or not branch:
        return None, None
    try:
        return refs.latest_pr(refs.fetch_pr_list_for_branch(
            tool, branch, cwd=repo, timeout=refs.STATUS_TIMEOUT)), tool
    except HarnessError as e:
        eprint(f"warning: {tool} pr lookup failed for {branch}: {e}")
        other = "glab" if tool == "gh" else "gh"
        if shutil.which(other) is None:
            raise
        try:
            return refs.latest_pr(refs.fetch_pr_list_for_branch(
                other, branch, cwd=repo, timeout=refs.STATUS_TIMEOUT)), other
        except HarnessError as e2:
            eprint(f"warning: {other} pr lookup failed for {branch}: {e2}")
            raise


def _pr_cells(entry: dict, refresh_pr: bool = False) -> dict:
    """Commits + PR cells for one session, backed by pr_cache.

    Uses tri-state freshness (fresh | stale | expired):
    - fresh: within fresh TTL (10m open PR, 3d merged/closed PR, 30m negative)
      and git tips match -> return cached immediately.
    - stale: past fresh TTL, within SWR window, tips match -> return cached
      immediately and revalidate via swr.serve_stale_or_refresh (background
      thread in `serve`, non-spawning in CLI).
    - expired (or tips changed / --refresh-pr): query inline.
    """
    wt = entry.get("worktree", "")
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cells = {"commits": "-", "ab": None, "pr": "-", "pr_data": None,
             "base_branch": None, "_tip": None, "_tool": None}
    cached = store.lookup_pr_cache(branch, repo)
    wt_ok = bool(wt) and Path(wt).exists()
    if wt and not wt_ok:
        cells["commits"] = "gone"
    cached_target = ((cached or {}).get("pr") or {}).get("target_branch") or ""
    target_ok = bool(cached) and (
        not cached_target or cached_target == (cached.get("base_branch") or "")
    )
    if not wt_ok and branch and not refresh_pr:
        freshness = _pr_freshness(cached) if target_ok else "expired"
        if freshness in ("fresh", "stale") or entry.get("deleted_at"):
            if cached:
                base_branch = cached.get("base_branch") or ""
                pr = cached.get("pr")
                cells["_tool"] = cached.get("tool")
                cells.update(pr=_fmt_pr(pr), pr_data=pr, base_branch=base_branch or None)
                if freshness == "stale" and not entry.get("deleted_at"):
                    def _bg_pr_gone() -> None:
                        try:
                            p, u = _query_pr(repo, branch)
                        except HarnessError:
                            return
                        if p is not None or u is not None:
                            tgt = (p or {}).get("target_branch") or base_branch or _patched("_repo_default_branch")(repo) or None
                            store.cache_pr_status(branch, p, tool=u if p else None, repo=repo, base_branch=tgt)
                    swr.serve_stale_or_refresh(cached, False, _bg_pr_gone, f"pr:pr:{store.pr_cache_key(repo, branch)}")
            return _seed_recorded_pr(cells, entry)
    if wt_ok and branch and target_ok and cached:
        base_branch = cached.get("base_branch") or ""
        branch_tip = _patched("_git_tip")(wt, branch) or _patched("_git_tip")(wt, "HEAD")
        base_tip = _patched("_git_tip")(wt, f"origin/{base_branch}") if base_branch else None
        tips_match = (cached.get("branch_tip") == branch_tip and cached.get("base_tip") == base_tip)
        freshness = _pr_freshness(cached, branch_tip, base_tip) if tips_match else "expired"
        if tips_match and (freshness in ("fresh", "stale") or refresh_pr):
            ab = {"behind": int(cached.get("behind") or 0),
                  "ahead": int(cached.get("ahead") or 0)}
            pr = cached.get("pr")
            cells["_tip"], cells["_tool"] = branch_tip, cached.get("tool")
            cells.update(commits=_fmt_counts(ab), ab=ab, pr=_fmt_pr(pr),
                         pr_data=pr, base_branch=base_branch or None)
            if not refresh_pr:
                if freshness == "stale":
                    def _bg_pr() -> None:
                        try:
                            p, u = _query_pr(repo, branch)
                        except HarnessError:
                            return
                        if p is None and u is None and cached.get("pr"):
                            return
                        new_base = (p or {}).get("target_branch") or base_branch
                        if new_base != base_branch:
                            new_ab = repos.ahead_behind(Path(wt), new_base, branch) if new_base else ab
                            new_base_tip = _patched("_git_tip")(wt, f"origin/{new_base}") if new_base else None
                        else:
                            new_ab = ab
                            new_base_tip = base_tip
                        store.cache_pr_status(
                            branch, p, tool=u if p else None, repo=repo,
                            base_branch=new_base, branch_tip=branch_tip,
                            base_tip=new_base_tip,
                            behind=(new_ab or {}).get("behind", 0),
                            ahead=(new_ab or {}).get("ahead", 0),
                        )
                    swr.serve_stale_or_refresh(cached, False, _bg_pr, f"pr:pr:{store.pr_cache_key(repo, branch)}")
                return _seed_recorded_pr(cells, entry)
            try:
                pr, used = _query_pr(repo, branch)
            except HarnessError:
                return _seed_recorded_pr(cells, entry)
            if pr is None and used is None and cached and cached.get("pr"):
                return _seed_recorded_pr(cells, entry)
            new_base = (pr or {}).get("target_branch") or base_branch
            if new_base != base_branch:
                base_branch = new_base
                base_tip = _patched("_git_tip")(wt, f"origin/{base_branch}") if base_branch else None
                ab = repos.ahead_behind(Path(wt), base_branch, branch) if base_branch else ab
            store.cache_pr_status(branch, pr, tool=used, repo=repo,
                                  base_branch=base_branch,
                                  branch_tip=branch_tip, base_tip=base_tip,
                                  behind=(ab or {}).get("behind", 0),
                                  ahead=(ab or {}).get("ahead", 0))
            cells["_tip"], cells["_tool"] = branch_tip, used
            cells.update(commits=_fmt_counts(ab), ab=ab, pr=_fmt_pr(pr),
                         pr_data=pr, base_branch=base_branch or None)
            return _seed_recorded_pr(cells, entry)
    if branch:
        query_failed = False
        try:
            pr, used = _query_pr(repo, branch)
        except HarnessError:
            query_failed = True
            pr, used = (cached or {}).get("pr"), (cached or {}).get("tool")
        no_cli_fallback = False
        if pr is None and used is None and cached and cached.get("pr"):
            no_cli_fallback = True
            pr, used = cached["pr"], cached.get("tool")
        target = (pr or {}).get("target_branch") or ""
        db = (target
              or (cached or {}).get("base_branch")
              or _patched("_repo_default_branch")(repo)) or None
        ab = repos.ahead_behind(Path(wt), db, branch) if (wt_ok and db) else None
        needs_base_heal = bool(cached and db and db != (cached.get("base_branch") or ""))
        if wt_ok:
            branch_tip = _patched("_git_tip")(wt, branch) or _patched("_git_tip")(wt, "HEAD")
            if not query_failed and (not no_cli_fallback or needs_base_heal):
                store.cache_pr_status(branch, pr, tool=used if pr else None, repo=repo,
                                      base_branch=db,
                                      branch_tip=branch_tip,
                                      base_tip=_patched("_git_tip")(wt, f"origin/{db}") if db else None,
                                      behind=(ab or {}).get("behind", 0),
                                      ahead=(ab or {}).get("ahead", 0))
            cells["_tip"], cells["_tool"] = branch_tip, used if pr else None
            cells.update(commits=_fmt_counts(ab), ab=ab)
        elif not query_failed and (pr is not None or used is not None):
            store.cache_pr_status(branch, pr, tool=used if pr else None, repo=repo,
                                  base_branch=db)
            cells["_tool"] = used if pr else None
        elif pr is not None:
            cells["_tool"] = used
        cells.update(pr=_fmt_pr(pr), pr_data=pr, base_branch=db)
    return _seed_recorded_pr(cells, entry)


def _status_cells(key: str, entry: dict, refresh_pr: bool = False) -> dict:
    """_pr_cells plus the CI pipeline and review-comment cells.

    CI resolves after the PR: no PR -> no CI cell. A cached ``ci`` is
    reused while the branch tip is unchanged, the check is within the
    stale-while-revalidate window (max(10 min * 10, 1 day)) and
    --refresh-pr is not given; otherwise the pipeline is fetched once and
    persisted via store.cache_ci_status. Review stats ({reviews,
    unresolved, resolved} via refs.fetch_pr_comment_stats) follow the same
    tip/refresh rules and ride in ``reviews``/``reviews_detail``.
    ``issue_status`` is the lowercased tracker status of the linked issue
    (None for branch-only rows), cached 10 min and re-queried on
    --refresh-pr.
    """
    cells = _pr_cells(entry, refresh_pr)
    cells["ci"], cells["ci_url"] = _ci_cell(entry, cells, refresh_pr, key=key)
    cells["reviews_detail"] = _reviews_cell(entry, cells, refresh_pr, key=key)
    cells["reviews"] = _fmt_reviews(cells["reviews_detail"])
    cells["issue_status"] = _issue_cell(key, entry, refresh_pr)
    cells["prodtest_detail"] = _prodtest_cell(entry)
    cells["prodtest"] = _fmt_prodtest(cells["prodtest_detail"])
    cells["failing_checks"] = _failing_checks_cell(key)
    cells["unresolved_threads"] = _unresolved_threads_cell(key, cells["reviews_detail"])
    return cells


def _failing_checks_cell(key: str) -> list[str]:
    """Names of currently failing normalized PR checks for *key*."""
    if not key:
        return []
    try:
        checks = store.list_pr_checks(key)
    except Exception:
        return []
    from . import store_timeline
    return store_timeline.failing_check_names(checks)


def _unresolved_threads_cell(key: str, reviews_detail: dict | None) -> int:
    """Count of unresolved review threads for *key*."""
    if isinstance(reviews_detail, dict) and "unresolved" in reviews_detail:
        try:
            return int(reviews_detail.get("unresolved") or 0)
        except (TypeError, ValueError):
            pass
    if not key:
        return 0
    try:
        return len(store.list_pr_review_threads(key, unresolved_only=True))
    except Exception:
        return 0


def _prodtest_cell(entry: dict) -> dict | None:
    """Prodtest item for a worktree entry, keyed by issue (else ref key).

    Returns the stored item (status/risk/revision/last_run) or None when
    no item exists. Never raises — a store failure yields no cell.
    """
    from . import cli_cleanup as _cc
    try:
        item = store.get_prodtest(_cc._prodtest_issue_key(
            str(entry.get("key", "") or ""), entry))
    except Exception:
        return None
    if not item:
        return None
    out = dict(item)
    out["last_run"] = item.get("last_run_id", "") or ""
    return out


def _fmt_prodtest(item: dict | None) -> str:
    if not item:
        return "-"
    return str(item.get("status", "") or "-")


def _ci_cell(entry: dict, cells: dict, refresh_pr: bool, key: str = "") -> tuple[str | None, str]:
    """(status, job_url) for the resolved PR's latest CI pipeline."""
    pr = cells.get("pr_data")
    if not pr or not pr.get("url"):
        return None, ""
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cached = store.lookup_pr_cache(branch, repo)
    wt_ok = bool(entry.get("worktree")) and Path(entry["worktree"]).exists()
    branch_tip = cells.get("_tip")
    if branch_tip is None and wt_ok:
        branch_tip = _patched("_git_tip")(entry["worktree"], "HEAD")
    tip_for_cache = branch_tip if wt_ok else None
    url = pr["url"]
    tool = ((cached or {}).get("tool")
            or ("gh" if "github.com" in url else "glab"))

    def _fetch_and_store_ci() -> tuple[str | None, str]:
        from . import refs_ci as _refs_ci
        if refs.fetch_ci is _refs_ci.fetch_ci:
            ci, ci_url, checks = refs.fetch_ci_details(tool, url, entry.get("repo", ""))
            if ci == "failure" and checks:
                _refs_ci.populate_failing_check_logs(
                    tool, checks, cwd=entry.get("repo", ""), fallback_url=ci_url
                )
        else:
            ci, ci_url = refs.fetch_ci(tool, url, entry.get("repo", ""))
            checks = []
        prev_cached: dict | None = None
        if branch:
            prev_cached = store.cache_ci_status(branch, ci, sha=branch_tip or "", ci_url=ci_url, repo=repo)
        if key and ci is not None:
            try:
                for c in checks:
                    if not c.get("commit_sha") and branch_tip:
                        c["commit_sha"] = branch_tip
                store.replace_pr_checks(key, checks)
                prior_source = prev_cached if prev_cached is not None else (cached or {})
                prior_ci = prior_source.get("ci")
                if ci != prior_ci and ci in ("failure", "success", "running"):
                    from . import store_timeline
                    ev_type = {"failure": "ci_failed", "success": "ci_passed", "running": "ci_running"}[ci]
                    failed_names = store_timeline.failing_check_names(checks)
                    if ci == "failure" and failed_names:
                        summary = f"CI failed: {', '.join(failed_names[:3])}"
                    elif ci == "failure":
                        summary = "CI failed"
                    elif ci == "success":
                        summary = "CI passed"
                    else:
                        summary = "CI running"
                    store.append_worktree_event(
                        key, ev_type, summary,
                        {"ci": ci, "prior_ci": prior_ci, "ci_url": ci_url, "failing_checks": failed_names},
                    )
            except Exception:
                pass
        return ci, ci_url

    if not refresh_pr and cached:
        if entry.get("deleted_at") and "ci" in cached:
            return cached.get("ci"), cached.get("ci_url") or ""
        freshness = _ci_freshness(cached, tip_for_cache)
        if freshness == "fresh":
            return cached.get("ci"), cached.get("ci_url") or ""
        if freshness == "stale":
            swr.serve_stale_or_refresh(cached, False, _fetch_and_store_ci, f"pr:ci:{store.pr_cache_key(repo, branch)}")
            return cached.get("ci"), cached.get("ci_url") or ""
    if not refresh_pr and entry.get("deleted_at"):
        return None, ""
    return _fetch_and_store_ci()


def _reviews_freshness(
    cached: dict | None,
    branch_tip: str | None = None,
) -> Literal["fresh", "stale", "expired"]:
    """Tri-state freshness for cached review-comment stats."""
    if not cached or not all(k in cached for k in ("reviews", "unresolved", "resolved")):
        return "expired"
    sha = branch_tip or ""
    if (cached.get("reviews_sha") or "") != sha:
        return "expired"
    return _age_freshness(str(cached.get("reviews_checked_at", "") or ""), _ci_ttl(), _ci_swr())


def _reviews_usable(cached: dict) -> bool:
    """Cached review-comment stats still servable within SWR window."""
    return _age_freshness(str(cached.get("reviews_checked_at", "") or ""), _ci_ttl(), _ci_swr()) != "expired"


def _reviews_cell(entry: dict, cells: dict, refresh_pr: bool, key: str = "") -> dict | None:
    """{reviews, unresolved, resolved} for the resolved PR (issue #28)."""
    pr = cells.get("pr_data")
    if not pr or not pr.get("url"):
        return None
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cached = store.lookup_pr_cache(branch, repo)
    wt_ok = bool(entry.get("worktree")) and Path(entry["worktree"]).exists()
    branch_tip = cells.get("_tip")
    if branch_tip is None and wt_ok:
        branch_tip = _patched("_git_tip")(entry["worktree"], "HEAD")
    tip_for_cache = branch_tip if wt_ok else None
    url = pr["url"]
    tool = ((cached or {}).get("tool")
            or ("gh" if "github.com" in url else "glab"))

    def _fetch_and_store_reviews() -> dict | None:
        from . import refs_reviews as _refs_reviews
        if refs.fetch_pr_comment_stats is _refs_reviews.fetch_pr_comment_stats:
            stats, threads = refs.fetch_pr_review_details(tool, url, entry.get("repo", ""))
        else:
            stats = refs.fetch_pr_comment_stats(tool, url, entry.get("repo", ""))
            threads = []
        prev_cached: dict | None = None
        if branch:
            prev_cached = store.cache_review_stats(branch, stats, sha=branch_tip or "", repo=repo)
        if key and stats is not None:
            try:
                store.replace_pr_review_threads(key, threads)
                prior_source = prev_cached if prev_cached is not None else (cached or {})
                prior_unres = int(prior_source.get("unresolved") or 0)
                new_unres = int(stats.get("unresolved") or 0)
                if new_unres > prior_unres:
                    noun = "comment" if new_unres == 1 else "comments"
                    store.append_worktree_event(
                        key, "review_comments_arrived",
                        f"{new_unres} unresolved review {noun}",
                        {"unresolved": new_unres, "prior_unresolved": prior_unres},
                    )
                elif prior_unres > 0 and new_unres == 0:
                    store.append_worktree_event(
                        key, "review_comments_resolved",
                        "All review comments resolved",
                        {"unresolved": 0, "prior_unresolved": prior_unres},
                    )
            except Exception:
                pass
        return stats

    has_rev = bool(cached and all(k in cached for k in ("reviews", "unresolved", "resolved")))
    if not refresh_pr and cached and has_rev:
        cached_stats = {
            "reviews": int(cached.get("reviews") or 0),
            "unresolved": int(cached.get("unresolved") or 0),
            "resolved": int(cached.get("resolved") or 0),
        }
        if entry.get("deleted_at"):
            return cached_stats
        freshness = _reviews_freshness(cached, tip_for_cache)
        if freshness == "fresh":
            return cached_stats
        if freshness == "stale":
            swr.serve_stale_or_refresh(cached, False, _fetch_and_store_reviews, f"pr:reviews:{store.pr_cache_key(repo, branch)}")
            return cached_stats
    if not refresh_pr and entry.get("deleted_at"):
        return None
    return _fetch_and_store_reviews()


def _issue_freshness(cached: dict | None) -> Literal["fresh", "stale", "expired"]:
    """Tri-state freshness for cached linked-issue status."""
    if not cached or "issue_status" not in cached:
        return "expired"
    return _age_freshness(str(cached.get("issue_status_checked_at", "") or ""), _ci_ttl(), _ci_swr())


def _issue_usable(cached: dict) -> bool:
    """Cached issue status still servable within SWR window."""
    return _age_freshness(str(cached.get("issue_status_checked_at", "") or ""), _ci_ttl(), _ci_swr()) != "expired"


def _issue_cell(key: str, entry: dict, refresh_pr: bool = False) -> str | None:
    """Lowercased tracker status for the linked issue, else None."""
    stored = str(entry.get("issue", "") or "").strip()
    stored_url = str(entry.get("issue_url", "") or "").strip()
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cached = store.lookup_pr_cache(branch, repo) if branch else None

    def _fetch_and_store_issue() -> str | None:
        from . import issue_status as _st
        parsed = None
        for cand in (stored, key, stored_url or None):
            if not cand:
                continue
            try:
                parsed = _st._parse(cand, quiet=True)
            except Exception:
                parsed = None
            if parsed is not None:
                break
        if parsed is None:
            return (cached or {}).get("issue_status") or None
        tool = parsed.get("tool", "")
        if tool not in ("jira", "gh"):
            return (cached or {}).get("issue_status") or None
        cur = _st._issue_state(parsed.get("number", ""), tool,
                               parsed.get("url", "") or parsed.get("number", ""))
        if cur is None:
            return (cached or {}).get("issue_status") or None
        if branch:
            store.cache_issue_status(branch, cur, repo=repo)
        return cur or None

    if not refresh_pr and cached and "issue_status" in cached:
        if entry.get("deleted_at"):
            return cached.get("issue_status") or None
        freshness = _issue_freshness(cached)
        if freshness == "fresh":
            return cached.get("issue_status") or None
        if freshness == "stale":
            swr.serve_stale_or_refresh(cached, False, _fetch_and_store_issue, f"pr:issue:{store.pr_cache_key(repo, branch)}")
            return cached.get("issue_status") or None
    if not refresh_pr and entry.get("deleted_at"):
        return None
    return _fetch_and_store_issue()


def _fmt_reviews(stats: dict | None) -> str:
    if not stats:
        return "-"
    return f"{stats.get('reviews', 0)}|{stats.get('unresolved', 0)}|{stats.get('resolved', 0)}"


def _session_rows(links: dict, show_worktree: bool, refresh: bool
                  ) -> tuple[list[dict], list[str]]:
    rows = []
    for k, v in links.items():
        cells = _status_cells(k, v, refresh)
        row = {"key": k, "branch": v.get("branch", "?"),
               "harness": _harness_cell(k, v.get("worktree", "")),
               "commits": cells["commits"], "pr": cells["pr"],
               "ci": cells["ci"] or "", "ci_url": cells["ci_url"] or "",
               "reviews": cells["reviews"],
               "prodtest": cells["prodtest"],
               "prodtest_detail": cells["prodtest_detail"],
               "_pr_state": (cells["pr_data"] or {}).get("state", "")}
        if show_worktree:
            row["worktree"] = v.get("worktree", "?")
        rows.append(row)
    columns = (["key", "worktree", "branch", "harness", "commits", "pr", "ci", "reviews"]
               if show_worktree
               else ["key", "branch", "harness", "commits", "pr", "ci", "reviews"])
    return rows, columns


def _colorize_session(row: dict) -> dict:
    out = dict(row)
    st = row.get("_pr_state", "")
    if st in _PR_STYLES:
        c = _PR_STYLES[st]
        out["pr"] = f"[{c}]{row['pr']}[/{c}]"
    ci = row.get("ci") or ""
    if ci in _CI_STYLES:
        s = _CI_STYLES[ci]
        out["ci"] = f"[{s}]{_CI_SYMBOLS[ci]}[/{s}]"
    else:
        out["ci"] = "-"
    if not out.get("harness"):
        out["harness"] = "—"
    return out


def _enrich_entry(
    key: str,
    entry: dict,
    refresh: bool,
    active_runs: list[dict] | None = None,
    active_flows: list[dict] | None = None,
) -> dict:
    cells = _status_cells(key, entry, refresh)
    out = {**entry, "harness": _harness_cell(key, entry.get("worktree", "")),
           "commits": cells["commits"], "pr": cells["pr"],
           "ci": cells["ci"], "ci_url": cells["ci_url"],
           "reviews": cells["reviews"],
           "issue_status": cells["issue_status"],
           "prodtest": cells["prodtest"],
           "prodtest_detail": cells["prodtest_detail"],
           "wt_valid": worktrees.is_valid_worktree(entry.get("worktree", "")),
           "commits_detail": cells["ab"], "pr_detail": cells["pr_data"],
           "reviews_detail": cells["reviews_detail"],
           "failing_checks": cells["failing_checks"],
           "unresolved_threads": cells["unresolved_threads"]}
    out.update(_delivery_status(key, out, active_runs=active_runs, active_flows=active_flows))
    return out


def _session_detail(
    key: str,
    entry: dict,
    refresh: bool,
    active_runs: list[dict] | None = None,
    active_flows: list[dict] | None = None,
) -> dict:
    cells = _status_cells(key, entry, refresh)
    detail = {"key": key, **entry,
              "harness": _harness_cell(key, entry.get("worktree", "")),
              "commits": cells["commits"],
              "pr": _fmt_pr(cells["pr_data"]), "commits_detail": cells["ab"],
              "pr_detail": cells["pr_data"], "base_branch": cells["base_branch"],
              "ci": cells["ci"], "ci_url": cells["ci_url"],
              "reviews": cells["reviews"],
              "reviews_detail": cells["reviews_detail"],
              "failing_checks": cells["failing_checks"],
              "unresolved_threads": cells["unresolved_threads"],
              "issue_status": cells["issue_status"],
              "prodtest": cells["prodtest"],
              "prodtest_detail": cells["prodtest_detail"],
              "wt_valid": worktrees.is_valid_worktree(entry.get("worktree", "")),
              "issue_url": refs.issue_url(key, entry.get("issue"))}
    if not cells["pr_data"]:
        detail["create_hint"] = _create_hint(entry)
    detail.update(_delivery_status(key, detail, active_runs=active_runs, active_flows=active_flows))
    return detail


def _print_detail(detail: dict) -> None:
    from rich.console import Console
    from rich.markup import escape
    c = Console()
    c.print(f"[bold]{escape(detail['key'])}[/bold]")
    for k in ("worktree", "branch", "repo"):
        if detail.get(k):
            c.print(f"  {k}: {escape(str(detail[k]))}")
    c.print(f"  harness: {escape(str(detail.get('harness') or '—'))}")
    issue = detail.get("issue_url") or detail.get("issue")
    if issue:
        c.print(f"  issue: {escape(str(issue))}")
    pd = detail.get("pr_detail")
    if pd:
        style = _PR_STYLES.get(pd.get("state", ""), "white")
        c.print(f"  PR: [{style}]#{pd['number']} {escape(pd.get('title', ''))} "
                f"({pd.get('state', '')})[/{style}] by "
                f"{escape(pd.get('author', '?'))}")
        c.print(f"  url: {escape(pd.get('url', ''))}")
    ci = detail.get("ci")
    if ci:
        style = _CI_STYLES.get(ci, "white")
        c.print(f"  ci: [{style}]{_CI_SYMBOLS.get(ci, ci)} {escape(ci)}[/{style}]")
    rd = detail.get("reviews_detail")
    if rd:
        c.print(f"  reviews: {rd.get('reviews', 0)} done / "
                f"{rd.get('unresolved', 0)} unresolved / {rd.get('resolved', 0)} resolved")
    cd = detail.get("commits_detail")
    if cd:
        base = escape(detail.get("base_branch") or "the base branch")
        c.print(f"  commits: {cd['behind']} behind / {cd['ahead']} ahead "
                f"of {base}")
    hint = detail.get("create_hint")
    if hint:
        c.print(f"  no PR/MR — create one: {escape(str(hint))}")


def _create_hint(entry: dict) -> str | None:
    """URL or shell command to open a new MR/PR for the session's branch.

    GitHub: web create-PR URL. GitLab: glab CLI command (self-hosted
    instances have no stable web create URL with a prefilled source
    branch).
    """
    branch = entry.get("branch", "")
    if not branch:
        return None
    remote = repos.remote_url(Path(entry["repo"])) if entry.get("repo") else None
    if not remote:
        return None
    host = repos._remote_host(remote) or ""
    if "github.com" in host:
        m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?/?$", remote)
        if m:
            return (f"https://github.com/{m.group(1)}/{m.group(2)}/compare/"
                    f"{branch}?expand=1")
        return None
    m = re.search(r"[:/](?P<path>[^/]+/[^/]+?)(?:\.git)?/?$", remote)
    if not m:
        return None
    path = m.group("path")
    if "gitlab" in host:
        return f"glab mr create --source-branch {shlex.quote(branch)}"
    return (f"glab mr create --repo {shlex.quote(host)}/"
            f"{shlex.quote(path)} --source-branch {shlex.quote(branch)}")

# ── status ────────────────────────────────────────────────────────────


@app.command("status")
@_catch_harness_errors
def status(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="Issue/PR ref or worktree key (omit: all links)."),
    worktree: bool = typer.Option(False, "--worktree", help="Show the worktree column."),
    refresh_pr: bool = typer.Option(False, "--refresh-pr",
                                    help="Fetch origin, then re-query PR status instead of using the cache."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """Show linked issue↔PR↔worktree state (Rich table by default).

    Columns: behind|ahead vs the repo's remote-tracking base branch (the
    PR/MR target when known) and the latest PR/MR for the branch (cached;
    --refresh-pr fetches origin and re-queries).
    """
    links = store.load_links()
    if ref:
        try:
            key, entry, parsed = worktrees.resolve_any(ref, links)
        except worktrees.NoLinkedState:
            _fail(f"no linked state for {ref}", EXIT_USAGE)
        if not key or not entry:
            if parsed is None:
                _fail(f"no linked state for {ref}", EXIT_USAGE)
            want = refs.issue_key(parsed)
            kin = [k for k in refs.matching_issue_keys(want, links) if links.get(k)]
            key = kin[0] if len(kin) == 1 else want
            entry = links.get(key) or links.get(f"pr:{parsed['url']}", {})
            if not entry:
                _fail(f"no linked state for {ref}", EXIT_USAGE)
        _fetch_origins({key: entry}, refresh_pr)
        detail = _session_detail(key, entry, refresh=refresh_pr)
        if json_output:
            print(json.dumps(detail, indent=2, ensure_ascii=False))
            return
        _print_detail(detail)
        return
    _fetch_origins(links, refresh_pr)
    if json_output:
        print(json.dumps({k: _enrich_entry(k, v, refresh_pr)
                          for k, v in links.items()}, indent=2, ensure_ascii=False))
        return
    rows, columns = _session_rows(links, worktree, refresh_pr)
    _print_rows(rows, json_output, csv_output, columns,
                "Linked worktrees", "(no linked worktrees)",
                colorize=_colorize_session)
    eprint("commits: B|A = B commits behind, A commits ahead of the base branch")


