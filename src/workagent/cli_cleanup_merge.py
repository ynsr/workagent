"""PR/MR merge + close helpers for cleanup (moved verbatim from cli_cleanup)."""
from __future__ import annotations

from . import refs
from .cli_core import EXIT_USAGE, _fail, eprint
from .errors import HarnessError

def _pr_missing(msg: str) -> bool:
    """True when a host-CLI error means the PR/MR is gone (404/not found)."""
    msg = (msg or "").lower()
    return ("404" in msg or "not found" in msg or "could not resolve" in msg
            or "couldn't find" in msg or "could not find" in msg)


def _pr_conflicted(fresh: dict | None) -> bool:
    """True when fresh merge state reports destination-branch conflicts."""
    if not fresh:
        return False
    vals = " ".join(str(fresh.get(k) or "") for k in
                    ("mergeable", "merge_state")).lower()
    return any(t in vals for t in ("conflict", "dirty", "cannot_be_merged",
                                   "cannot be merged", "unmergable"))


_BLOCKED_MERGE_TOKENS = (
    "discussions_not_resolved",
    "unresolved",
    "blocking_discussions",
    "pipeline_failure",
    "need_rebase",
    "requested_changes",
    "blocked_status",
    "broken_status",
    "approvals",
    "draft",
    "blocked",
)


def _merge_blocked_reason(fresh: dict | None) -> str:
    """Non-conflict reason the host will refuse a merge, or "".

    Separate from _pr_conflicted (destination-branch conflicts): covers
    unresolved threads, pipeline gates, approvals, drafts, and rebase
    requirements surfaced via detailed_merge_status / mergeStateStatus.
    Unknown/empty state ("") never blocks — offline/cache-miss cleanup
    still falls back to close-and-remove.
    """
    if not fresh:
        return ""
    vals = " ".join(str(fresh.get(k) or "") for k in
                    ("mergeable", "merge_state")).lower()
    if not vals.strip():
        return ""
    for tok in _BLOCKED_MERGE_TOKENS:
        if tok in vals:
            return tok
    return ""


def _fresh_pr_state(pr_url: str, host_cwd: str) -> dict | None:
    """Fresh PR/MR state (never cached); None on lookup failure (warned)."""
    if not pr_url:
        return None
    try:
        return refs.fetch_pr_merge_state(pr_url, cwd=host_cwd)
    except HarnessError as e:
        eprint(f"warning: cannot read PR/MR state for {pr_url}: {e}")
        return None


def _resolve_branch_pr(branch: str, recorded_url: str, host_cwd: str,
                       tool: str | None = None) -> tuple[str, dict | None, bool]:
    """(pr_url, fresh, picked) for a source branch.

    Lists every PR/MR sourced from *branch* and picks the latest open,
    else the latest overall. Falls back to the recorded URL (fresh lookup)
    when the list lookup fails or is empty. `picked` is True
    when the URL came from the branch list rather than the recorded link.
    """
    if branch:
        tools = ([tool] if tool in ("gh", "glab") else []) + ["gh", "glab"]
        seen: set[str] = set()
        for t in tools:
            if t in seen:
                continue
            seen.add(t)
            try:
                prs = refs.fetch_pr_list_for_branch(t, branch, cwd=host_cwd)
            except HarnessError as e:
                eprint(f"warning: {t} pr lookup failed for {branch}: {e}")
                continue
            picked = refs.pick_branch_pr(prs)
            if picked and picked.get("url"):
                url = picked["url"]
                from . import cli as _cli3
                return url, _cli3._fresh_pr_state(url, host_cwd), url != recorded_url
            if prs:
                break
    from . import cli as _cli4
    return recorded_url, (_cli4._fresh_pr_state(recorded_url, host_cwd) if recorded_url else None), False


_LONG_LIVED_SOURCES = frozenset({"dev", "develop", "main", "master"})


def _source_needs_squash(source_branch: str) -> bool:
    """True unless *source_branch* is long-lived (dev/develop/main/master).

    Squash is destructive: branches that stay relevant keep full history
    and merge with a merge commit. Everything else squashes.
    """
    return (source_branch or "").strip().lower() not in _LONG_LIVED_SOURCES


def _merge_pr(pr_url: str, squash: bool = True, cwd: str | None = None,
              source_branch: str = "", no_squash: bool = False) -> None:
    """Merge an open PR/MR (squash default); verify host state; raise on failure.

    Already-merged/closed is tolerated (returns with a note) since callers
    may decide on cached PR state up to the 3d status TTL: the host PR can
    have gone terminal without local tip movement. Other failures raise.

    ``cwd`` must be inside the MR/PR's repo so gh/glab bind the right
    host/remote — without it they probe the server's cwd (often an
    unrelated checkout) and fail with "no git remote points to a known
    host" or act on the wrong repo.

    Squash is mandatory for non-long-lived sources: an explicit
    ``no_squash`` there is a usage error (exit 2). Long-lived sources
    (dev/develop/main/master) keep full history and merge with a merge
    commit. Unknown/empty *source_branch* squashes (fail-safe: only
    a known long-lived source opts out).

    glab quirk (IPG-1017): `glab mr merge` can exit 0 printing a merged
    summary while the MR stays `opened` — e.g. `merge_when_pipeline_succeeds`
    or `detailed_merge_status: commits_status` (auto-merge queued, not
    executed). The caller set merged_now from our silent return, deleted
    the remote branch, and orphaned the work: `develop` never got the
    commits. So after a clean CLI exit we re-query the host and raise
    unless the MR reports merged (or already-merged/closed tolerance).
    """
    long_lived = bool(source_branch) and not _source_needs_squash(source_branch)
    if long_lived:
        # Long-lived sources keep full history: merge commit.
        squash = False
    elif no_squash:
        _fail(f"--no-squash refused for source branch {source_branch!r}: "
              "cleanup squash-merges non-long-lived branches into "
              "default/protected branches (long-lived sources: "
              "dev/develop/main/master).", EXIT_USAGE)
    else:
        squash = True
    from . import cli as _cli  # shim: tests patch workagent.cli.run_cmd
    try:
        if "github.com" in pr_url:
            # gh requires an explicit strategy non-interactively; squash
            # default, merge commit for long-lived sources (--merge).
            _cli.run_cmd("gh", "pr", "merge", pr_url,
                    "--squash" if squash else "--merge", cwd=cwd)
        else:
            # glab defaults to a merge commit; pass --squash explicitly,
            # omit the flag (no --no-squash exists) for merge commits.
            # --auto-merge=false avoids the silent auto-merge queue when a
            # pipeline is running (same quirk as release, IPG-1017): without
            # it glab can exit 0 while the MR stays opened, and the caller
            # would go on to delete the remote branch before the host merges.
            _cli.run_cmd("glab", "mr", "merge", pr_url,
                    *([] if not squash else ["--squash"]),
                    "--auto-merge=false", cwd=cwd)
    except HarnessError as e:
        msg = str(e).lower()
        if ("already merged" in msg or "already been merged" in msg
                or "already closed" in msg or "already been closed" in msg):
            eprint(f"note: {pr_url} already merged/closed; skipping merge.")
            return
        if "405" in msg or "method not allowed" in msg:
            raise HarnessError(
                f"{pr_url} merge refused by the host (405 Method Not Allowed): "
                f"the MR is not mergeable right now — e.g. unresolved discussions, "
                f"a pipeline/approvals gate, or a merge-method restriction. "
                f"Resolve it on the host, then re-run cleanup; nothing was torn down. "
                f"Host said: {e}")
        raise
    from . import cli_cleanup as _cc  # shim: tests patch cli_cleanup._fresh_pr_state
    verified = _cc._fresh_pr_state(pr_url, str(cwd) if cwd else "")
    if verified is not None and (verified.get("state") or "").lower() not in (
            "merged", "closed"):
        raise HarnessError(
            f"{pr_url} merge accepted by the host CLI but the MR is still "
            f"{verified.get('state')!r} on the host "
            f"(mergeable={verified.get('mergeable')!r}) — remote branch kept.\n"
            "  The merge may be queued (e.g. merge-when-pipeline-succeeds). "
            "Re-run cleanup after the host reports merged.")
    eprint(f"merged {pr_url}")


def _close_issue(parsed: dict, force: bool) -> None:
    if parsed["tool"] == "jira":
        # Best effort: comment the resolution; transitions vary per project,
        # so leave status change to the user unless --force.
        try:
            from .errors import run_cmd as _run
            _run("jira", "issue", "comment", "add", parsed["number"],
                 "Resolved via workagent cleanup.", "--no-input")
        except HarnessError as e:
            if not force:
                raise
            eprint(f"warning: {e}")
    elif parsed["tool"] == "gh" and parsed["repo"] and parsed["kind"] in ("issue", "issue_or_pr"):
        try:
            from .errors import run_cmd as _run
            target = [parsed["number"], "--repo", parsed["repo"]] if parsed["kind"] == "issue_or_pr" \
                else [parsed["url"]]
            _run("gh", "issue", "close", *target)
            eprint(f"closed issue {parsed['url'] or parsed['number']}")
        except HarnessError as e:
            msg = str(e).lower()
            # Note: `gh issue close` on an already-closed issue exits 0
            # (stdout "! ... already closed"), so this only fires for
            # genuinely missing issues (nonzero "Could not resolve").
            if ("already closed" in msg or "already been closed" in msg
                    or "not found" in msg or "404" in msg or "could not resolve" in msg):
                eprint(f"note: issue {parsed['url'] or parsed['number']} already closed; continuing.")
                return
            if not force:
                raise
            eprint(f"warning: {e}")
    elif parsed["tool"] == "glab":
        eprint("note: glab issue close not automated in v1; close it in the UI.")


def _close_pr(parsed: dict, pr_url: str, force: bool, cwd: str | None = None) -> None:
    url = pr_url or (parsed.get("url", "") if parsed.get("kind") in ("pr", "mr") else "")
    if not url:
        return
    try:
        from .errors import run_cmd as _run
        if "github.com" in url:
            _run("gh", "pr", "close", url, cwd=cwd)
        else:
            _run("glab", "mr", "close", url, cwd=cwd)
        eprint(f"closed {url}")
    except HarnessError as e:
        msg = str(e).lower()
        if ("already closed" in msg or "already been closed" in msg
                or "already been merged" in msg or "already merged" in msg
                or "404" in msg or "not found" in msg):
            eprint(f"note: {url} already closed; continuing with local cleanup.")
            return
        if not force:
            raise
        eprint(f"warning: {e}")
