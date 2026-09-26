"""status helpers + `status` command (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from . import backend, refs, repos, store, worktrees
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
    _CI_TTL_SECONDS,
    _NEGATIVE_TTL_SECONDS,
    _PR_STYLES,
    _STATUS_TTL_SECONDS,
    _repo_default_branch,
    _repo_tool,
)
from .errors import HarnessError, run_cmd

_COMP = _init_completions()


def _patched(name: str):
    """Test seam: resolve via the `cli` shim so monkeypatch.setattr(cli, …) applies."""
    from . import cli as _cli
    return getattr(_cli, name)
_complete_refs = _COMP["refs"]

def _fmt_pr(pr: dict | None) -> str:
    if not pr:
        return "-"
    kind = "MR" if "merge_requests" in (pr.get("url") or "") else "PR"
    state = pr.get("state") or ""
    if state:
        return f"{kind} #{pr['number']} ({state})"
    return f"{kind} #{pr['number']}"


def _recorded_pr(url: str) -> dict | None:
    """Parse a recorded PR/MR URL into a minimal pr dict (state unknown)."""
    for rx, group in ((refs._GITLAB_MR, 3), (refs._GITHUB_PR, 2)):
        m = rx.match(url or "")
        if m:
            return {"number": int(m.group(group)), "state": "", "url": url}
    return None


def _seed_recorded_pr(cells: dict, entry: dict) -> dict:
    """A recorded pr_url wins when the cache/query produced no PR."""
    if not cells.get("pr_data") and entry.get("pr_url"):
        cells["pr_data"] = _recorded_pr(entry["pr_url"])
        cells["pr"] = _fmt_pr(cells["pr_data"])
    return cells


def _fmt_counts(ab: dict | None) -> str:
    if not ab:
        return "-"
    return f"{ab['behind']}|{ab['ahead']}"


def _git_tip(wt: str, ref: str) -> str | None:
    try:
        return _patched("run_cmd")("git", "-C", wt, "rev-parse", "--verify", "-q", ref) or None
    except HarnessError:
        return None


def _cache_fresh(cached: dict) -> bool:
    ts = cached.get("checked_at", "")
    if not ts:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except ValueError:
        return False
    ttl = _STATUS_TTL_SECONDS if cached.get("pr") else _NEGATIVE_TTL_SECONDS
    return age < timedelta(seconds=ttl)


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


def _ci_fresh(cached: dict) -> bool:
    """Cached CI still valid: checked within _CI_TTL_SECONDS."""
    ts = cached.get("ci_checked_at", "")
    if not ts:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except ValueError:
        return False
    return age < timedelta(seconds=_CI_TTL_SECONDS)


def _query_pr(repo: str, branch: str) -> tuple[dict | None, str | None]:
    """(pr, tool) via the detected host CLI, falling back to the other one.

    Returns (None, None) when no host CLI is available; raises HarnessError
    when a CLI exists but every lookup fails (caller keeps cached data).
    """
    tool = _patched("_repo_tool")(repo)
    if not tool or not branch:
        return None, None
    try:
        return refs.latest_pr(refs.fetch_pr_list_for_branch(tool, branch, cwd=repo)), tool
    except HarnessError as e:
        eprint(f"warning: {tool} pr lookup failed for {branch}: {e}")
        other = "glab" if tool == "gh" else "gh"
        if shutil.which(other) is None:
            raise
        try:
            return refs.latest_pr(refs.fetch_pr_list_for_branch(other, branch, cwd=repo)), other
        except HarnessError as e2:
            eprint(f"warning: {other} pr lookup failed for {branch}: {e2}")
            raise


def _pr_cells(entry: dict, refresh_pr: bool = False) -> dict:
    """Commits + PR cells for one session, backed by pr_cache.json.

    A cache entry (keyed by branch) is reused while the session-branch tip
    and the remote-tracking base tip are unchanged and the entry is younger
    than its TTL (3d for a cached PR, 30min for a cached no-PR result),
    and --refresh-pr is not given. Valid cache: two `git rev-parse` calls,
    no host-CLI spawn, no API call. --refresh-pr re-queries only the PR
    (counts still reuse when tips are unchanged). When the cache or the
    live query yields no PR but the link records a `pr_url`, the recorded
    URL fills the PR cell. ``_tip``/``_tool`` are internal, consumed by the
    CI cell resolution in :func:`_status_cells`.
    """
    wt = entry.get("worktree", "")
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cells = {"commits": "-", "ab": None, "pr": "-", "pr_data": None,
             "base_branch": None, "_tip": None, "_tool": None}
    cached = store.load_pr_cache().get(branch) if branch else None
    wt_ok = bool(wt) and Path(wt).exists()
    if wt and not wt_ok:
        cells["commits"] = "gone"
    # Cached base must agree with the PR/MR target branch; a mismatch
    # (e.g. base cached as `main` while the MR targets `develop`) is stale.
    cached_target = ((cached or {}).get("pr") or {}).get("target_branch") or ""
    use_cache = (cached and _cache_fresh(cached)
                 and (not cached_target
                      or cached_target == (cached.get("base_branch") or "")))
    if wt_ok and branch and use_cache:
        base_branch = cached.get("base_branch") or ""
        branch_tip = _patched("_git_tip")(wt, branch) or _patched("_git_tip")(wt, "HEAD")
        base_tip = _patched("_git_tip")(wt, f"origin/{base_branch}") if base_branch else None
        if (cached.get("branch_tip") == branch_tip
                and cached.get("base_tip") == base_tip):
            ab = {"behind": int(cached.get("behind") or 0),
                  "ahead": int(cached.get("ahead") or 0)}
            pr = cached.get("pr")
            cells["_tip"], cells["_tool"] = branch_tip, cached.get("tool")
            cells.update(commits=_fmt_counts(ab), ab=ab, pr=_fmt_pr(pr),
                         pr_data=pr, base_branch=base_branch or None)
            if not refresh_pr:
                return _seed_recorded_pr(cells, entry)
            try:
                pr, used = _query_pr(repo, branch)
            except HarnessError:
                return _seed_recorded_pr(cells, entry)
            store.cache_pr_status(branch, pr, tool=used,
                                  base_branch=base_branch,
                                  branch_tip=branch_tip, base_tip=base_tip,
                                  behind=ab["behind"], ahead=ab["ahead"])
            cells["_tip"], cells["_tool"] = branch_tip, used
            cells.update(pr=_fmt_pr(pr), pr_data=pr)
            return _seed_recorded_pr(cells, entry)
    if branch:
        try:
            pr, used = _query_pr(repo, branch)
        except HarnessError:
            pr, used = (cached or {}).get("pr"), (cached or {}).get("tool")
        if pr is None and used is None and cached and cached.get("pr"):
            pr, used = cached["pr"], cached.get("tool")
        target = (pr or {}).get("target_branch") or ""
        db = (target
              or (cached or {}).get("base_branch")
              or _patched("_repo_default_branch")(repo)) or None
        ab = repos.ahead_behind(Path(wt), db, branch) if (wt_ok and db) else None
        if wt_ok:
            branch_tip = _patched("_git_tip")(wt, branch) or _patched("_git_tip")(wt, "HEAD")
            store.cache_pr_status(branch, pr, tool=used if pr else None,
                                  base_branch=db,
                                  branch_tip=branch_tip,
                                  base_tip=_patched("_git_tip")(wt, f"origin/{db}") if db else None,
                                  behind=(ab or {}).get("behind", 0),
                                  ahead=(ab or {}).get("ahead", 0))
            cells["_tip"], cells["_tool"] = branch_tip, used if pr else None
            cells.update(commits=_fmt_counts(ab), ab=ab)
        cells.update(pr=_fmt_pr(pr), pr_data=pr, base_branch=db)
    return _seed_recorded_pr(cells, entry)


def _status_cells(entry: dict, refresh_pr: bool = False) -> dict:
    """_pr_cells plus the CI pipeline and review-comment cells.

    CI resolves after the PR: no PR -> no CI cell. A cached ``ci`` is
    reused while the branch tip is unchanged, the check is younger than
    _CI_TTL_SECONDS (10 min) and --refresh-pr is not given; otherwise the
    pipeline is fetched once and persisted via store.cache_ci_status.
    Review stats ({reviews, unresolved, resolved} via
    refs.fetch_pr_comment_stats) follow the same 10-min/tip/refresh rules
    and ride in ``reviews``/``reviews_detail``.
    """
    cells = _pr_cells(entry, refresh_pr)
    cells["ci"] = _ci_cell(entry, cells, refresh_pr)
    cells["reviews_detail"] = _reviews_cell(entry, cells, refresh_pr)
    cells["reviews"] = _fmt_reviews(cells["reviews_detail"])
    return cells


def _ci_cell(entry: dict, cells: dict, refresh_pr: bool) -> str | None:
    """CI pipeline status for the resolved PR: success|failure|running|not_started.

    None when there is no PR (no url) or the lookup failed (soft failure —
    fetch_ci_status warns on stderr and returns None; nothing is cached so
    the next run retries).
    """
    pr = cells.get("pr_data")
    if not pr or not pr.get("url"):
        return None
    branch = entry.get("branch", "")
    cached = store.load_pr_cache().get(branch) if branch else None
    branch_tip = cells.get("_tip")
    if branch_tip is None and entry.get("worktree") \
            and Path(entry["worktree"]).exists():
        branch_tip = _patched("_git_tip")(entry["worktree"], "HEAD")
    if not refresh_pr and cached and _ci_fresh(cached) \
            and cached.get("ci_sha") == branch_tip:
        return cached.get("ci")
    url = pr["url"]
    tool = ((cached or {}).get("tool")
            or ("gh" if "github.com" in url else "glab"))
    ci = refs.fetch_ci_status(tool, url, entry.get("repo", ""))
    if branch:
        store.cache_ci_status(branch, ci, sha=branch_tip or "")
    return ci

def _reviews_fresh(cached: dict) -> bool:
    """Cached review-comment stats still valid: checked within _CI_TTL_SECONDS."""
    ts = cached.get("reviews_checked_at", "")
    if not ts:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except ValueError:
        return False
    return age < timedelta(seconds=_CI_TTL_SECONDS)


def _reviews_cell(entry: dict, cells: dict, refresh_pr: bool) -> dict | None:
    """{reviews, unresolved, resolved} for the resolved PR (issue #28).

    None when there is no PR (no url) or the lookup failed (soft failure —
    fetch_pr_comment_stats warns on stderr and returns None; nothing is
    cached so the next run retries). Cached 10 min while the tip matches.
    """
    pr = cells.get("pr_data")
    if not pr or not pr.get("url"):
        return None
    branch = entry.get("branch", "")
    cached = store.load_pr_cache().get(branch) if branch else None
    branch_tip = cells.get("_tip")
    if branch_tip is None and entry.get("worktree") \
            and Path(entry["worktree"]).exists():
        branch_tip = _patched("_git_tip")(entry["worktree"], "HEAD")
    if not refresh_pr and cached and _reviews_fresh(cached) \
            and cached.get("reviews_sha") == branch_tip \
            and all(k in cached for k in ("reviews", "unresolved", "resolved")):
        return {"reviews": int(cached.get("reviews") or 0),
                "unresolved": int(cached.get("unresolved") or 0),
                "resolved": int(cached.get("resolved") or 0)}
    url = pr["url"]
    tool = ((cached or {}).get("tool")
            or ("gh" if "github.com" in url else "glab"))
    stats = refs.fetch_pr_comment_stats(tool, url, entry.get("repo", ""))
    if branch:
        store.cache_review_stats(branch, stats, sha=branch_tip or "")
    return stats


def _fmt_reviews(stats: dict | None) -> str:
    if not stats:
        return "-"
    return f"{stats.get('reviews', 0)}|{stats.get('unresolved', 0)}|{stats.get('resolved', 0)}"


def _session_rows(links: dict, show_worktree: bool, refresh: bool
                  ) -> tuple[list[dict], list[str]]:
    rows = []
    for k, v in links.items():
        cells = _status_cells(v, refresh)
        row = {"key": k, "branch": v.get("branch", "?"),
               "harness": _harness_cell(k, v.get("worktree", "")),
               "commits": cells["commits"], "pr": cells["pr"],
               "ci": cells["ci"] or "", "reviews": cells["reviews"],
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


def _enrich_entry(key: str, entry: dict, refresh: bool) -> dict:
    cells = _status_cells(entry, refresh)
    return {**entry, "harness": _harness_cell(key, entry.get("worktree", "")),
            "commits": cells["commits"], "pr": cells["pr"],
            "ci": cells["ci"], "reviews": cells["reviews"],
            "wt_valid": worktrees.is_valid_worktree(entry.get("worktree", "")),
            "commits_detail": cells["ab"], "pr_detail": cells["pr_data"],
            "reviews_detail": cells["reviews_detail"]}

def _session_detail(key: str, entry: dict, refresh: bool) -> dict:
    cells = _status_cells(entry, refresh)
    detail = {"key": key, **entry,
              "harness": _harness_cell(key, entry.get("worktree", "")),
              "commits": cells["commits"],
              "pr": _fmt_pr(cells["pr_data"]), "commits_detail": cells["ab"],
              "pr_detail": cells["pr_data"], "base_branch": cells["base_branch"],
              "ci": cells["ci"], "reviews": cells["reviews"],
              "reviews_detail": cells["reviews_detail"],
              "wt_valid": worktrees.is_valid_worktree(entry.get("worktree", "")),
              "issue_url": refs.issue_url(key, entry.get("issue"))}
    if not cells["pr_data"]:
        detail["create_hint"] = _create_hint(entry)
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
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is not None:
            key = worktrees.pick_worktree(ref, resolved, links)
            entry = links[key]
        else:
            try:
                parsed = refs.parse_ref(ref)
            except HarnessError:
                _fail(f"no linked state for {ref}", EXIT_USAGE)
            key = refs.issue_key(parsed)
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


