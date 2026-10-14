"""Tracker↔repo relation guards.

Persisted in state/tracker_repos.json as
``{tracker_id: {"repos": [<repo path>, ...]}}``.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from . import refs, repos, store
from .errors import HarnessError, run_cmd


def default_tracker_for_repo(path: str | Path, tool: str | None = None) -> str:
    """Best-effort tracker id from a local repo's origin remote.

    GitHub remotes → ``github:OWNER/REPO``; GitLab remotes →
    ``gitlab:<host>/<group>/<repo>``. ``""`` when the remote is missing
    or matches neither host CLI.
    """
    from . import repos as _repos
    p = Path(path).expanduser()
    url = _repos.remote_url(p)
    if not url:
        return ""
    host = _repos._remote_host(url) or ""
    cli = tool or _repos._detect_host_cli(p)
    if host == "github.com" or cli == "gh":
        m = re.match(r"^(?:https?://github\.com/|ssh://(?:[^/@]+@)?github\.com[:/]|[^@]+@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$", url, re.IGNORECASE)
        if m:
            return f"github:{m.group(1)}"
        return ""
    if cli == "glab" or host in _repos._known_glab_hosts():
        m = re.match(r"^(?:[a-z][a-z0-9+.-]*://(?:[^/@]+@)?([^/:?#]+)[:/]|[^/@]+@([^/:]+):)(.+?)(?:\.git)?/?$", url, re.IGNORECASE)
        if m:
            repo = re.sub(r"/-/.*$", "", m.group(3)).strip("/")
            return f"gitlab:{host}/{repo}" if host and repo else ""
        return ""
    return ""


def normalize_id(raw: str) -> str:
    """Normalize user input to a canonical tracker id.

    Accepts canonical forms (``jira:IPG``, ``github:o/r``,
    ``gitlab:host/group/repo``) plus shorthands: a bare ``PREFIX`` or
    ``PREFIX-123`` maps to ``jira:PREFIX``; ``o/r`` or ``o/r#22`` maps to
    ``github:o/r``.
    """
    s = (raw or "").strip()
    if s.startswith(("jira:", "github:", "gitlab:")):
        return s
    if re.fullmatch(r"[A-Z][A-Z0-9_]*(-\d+)?", s):
        return f"jira:{s.split('-')[0]}"
    m = re.fullmatch(r"([^/\s]+/[^/\s#]+)(#\d+)?", s)
    if m:
        return f"github:{m.group(1)}"
    try:
        tid = tracker_id(refs.parse_ref(s))
    except Exception:
        return s
    return tid or s

def tracker_id(parsed: dict) -> str:
    """Derive the tracker id for a parsed ref; ``""`` when unknowable."""
    tool = parsed.get("tool", "")
    if tool == "jira-cli":
        prefix = (parsed.get("number", "") or "").split("-", 1)[0]
        return f"jira:{prefix}" if prefix else ""
    repo = parsed.get("repo", "") or ""
    if not repo:
        return ""
    if tool == "glab":
        host = urlparse(parsed.get("url", "")).netloc.lower()
        return f"gitlab:{host}/{repo}" if host else f"gitlab:{repo}"
    return f"github:{repo}"

def _guard_host_scoped_tracker(tid: str, repo: str) -> None:
    """Refuse to file a repo under a host-scoped tracker it cannot belong to.

    ``github:O/R`` and ``gitlab:host/group/repo`` name their remote; filing
    a repo whose origin derives a *different* host-scoped tracker is a
    wrong-repo write — raise exit 2 instead of recording it. ``jira:``
    prefix trackers are exempt by design: one Jira project spans many
    repos/hosts, so a GitHub checkout under ``jira:IPG`` can be legitimate.
    Repos without a derivable remote are left alone (the caller decides).
    """
    from .errors import HarnessError
    if not (tid.startswith(("github:", "gitlab:")) and repo):
        return
    try:
        derived = default_tracker_for_repo(repo)
    except Exception:
        return
    if not derived or derived == tid:
        return
    raise HarnessError(
        f"tracker {tid} does not match repo remote ({derived}).\n"
        f"  Pass --repo with a repo linked to {tid}, or record {derived} instead.",
        exit_code=2,
    )



def resolve_repo_for_ref(parsed: dict, explicit: str | None, cwd: Path,
                         depth: int = 7, yes: bool = False,
                         persist: bool = True, pinned: str = "") -> tuple[Path, str, str, str]:
    """Resolve ``(repo_dir, base_branch, tracker_id, outcome)`` for a parsed ref.

    Shared by start/review/sync (B2): tracker id from the parsed ref, the
    repo via :func:`resolve_for_tracker` (an explicit ``--repo`` always
    wins over a worktree-pinned repo), then the repo default branch.
    """
    tid = tracker_id(parsed)
    repo_dir, outcome = resolve_for_tracker(
        tid, explicit or pinned or None, cwd, depth=depth, yes=yes, persist=persist)
    return repo_dir, repos.default_branch(repo_dir), tid, outcome


def check_or_record(tid: str, repo: str, yes: bool = False, persist: bool = True) -> str:
    """Enforce the tracker↔repo relation for *tid* against *repo*.

    Returns ``"ok"`` when already linked, ``"recorded"`` when a new
    mapping was (or, with ``persist=False``, would be) persisted.
    Raises ``HarnessError`` (exit 2) when the repo differs from the
    stored mapping and the user does not confirm.

    Post-migration the SQLite trackers table is the source of truth
    (via :func:`store.add_tracker_repo`); pre-migration the same helper
    dual-writes config.json.
    """
    norm = str(Path(repo).expanduser().resolve()) if repo else repo
    if not tid:
        return "ok"
    _guard_host_scoped_tracker(tid, norm)
    known = [str(Path(r).expanduser().resolve()) for r in linked_repos(tid)]
    if not known:
        if persist:
            store.add_tracker_repo(tid, norm)
        return "recorded"
    if norm in known:
        return "ok"
    if yes or _confirm(tid, known, norm):
        if persist:
            store.add_tracker_repo(tid, norm)
        return "recorded"
    raise HarnessError("aborted", exit_code=2)


def linked_repos(tid: str) -> list[str]:
    """Stored repo paths for *tid* (raw, unnormalized)."""
    return list(store.load_trackers().get(tid, {}).get("repos", []))


def default_repo_for_ref(issue_ref: str) -> str:
    """Default repo path for *issue_ref* without touching the CWD.

    Rule 0: the ref is a linked worktree (key, branch name, or path —
    the Launch Review/Sync case) → that worktree's repo. Rule 1: the
    issue is already linked to a live worktree → that worktree's repo.
    Rule 2: the ref's tracker has exactly one linked repo → that repo.
    Otherwise ``""`` (the caller must ask for `--repo`).
    """
    from . import worktrees as _worktrees
    links = store.load_links()
    resolved = _worktrees.resolve_worktree(issue_ref, links)
    if isinstance(resolved, str):
        repo = str((links.get(resolved) or {}).get("repo", ""))
        if repo:
            return repo
    try:
        parsed = refs.parse_ref(issue_ref)
        if parsed.get("kind") in ("pr", "mr"):
            # PR/MR ref not resolvable via a worktree key/branch/path:
            # fetch the head branch and resolve a linked worktree on it
            # (Review prefill). Fetch failure falls through to the rules
            # below — never raises.
            try:
                head = (refs.fetch_pr_info(parsed) or {}).get("head_ref", "")
            except Exception:
                head = ""
            if head:
                resolved_head = _worktrees.resolve_worktree(head, links)
                if isinstance(resolved_head, str):
                    repo = str((links.get(resolved_head) or {}).get("repo", ""))
                    if repo:
                        return repo
        want = refs.issue_key(parsed)
    except Exception:
        return ""
    for key in refs.matching_issue_keys(want, links):
        repo = str(((links.get(key)) or {}).get("repo", ""))
        if repo:
            return repo
    tid = tracker_id(parsed)
    if not tid:
        return ""
    known = linked_repos(tid)
    if len(known) == 1:
        return known[0]
    return ""


def repos_for_ref(issue_ref: str) -> tuple[str, list[str]]:
    """``(default_repo, candidate_repos)`` for *issue_ref*.

    *default_repo* follows :func:`default_repo_for_ref`; *candidate_repos*
    is the tracker's linked-repo list (Rule 0/Rule 1 single repo folded
    in when outside it) so a picker can offer every viable `--repo`.
    """
    from . import worktrees as _worktrees
    default = default_repo_for_ref(issue_ref)
    cands: list[str] = []
    try:
        tid = tracker_id(refs.parse_ref(issue_ref))
    except Exception:
        tid = ""
    if tid:
        cands = list(linked_repos(tid))
    if default and default not in cands:
        links = store.load_links()
        resolved = _worktrees.resolve_worktree(issue_ref, links)
        if isinstance(resolved, str) or issue_ref in links:
            cands = [default, *cands]
    return default, cands

def resolve_for_tracker(tid: str, explicit: str | None, cwd: Path,
                        depth: int = 7, yes: bool = False,
                        persist: bool = True) -> tuple[Path, str]:
    """Pick the repo for *tid* — never defaulting to the CWD.

    1. ``--repo`` given → resolve it, guard via :func:`check_or_record`.
    2. Exactly one repo linked to *tid* → use it (silent auto-select).
    3. Several linked → usage error naming the candidates (explicit
       ``--repo`` required, even under ``--yes``/non-TTY).
    4. None linked → ask for an explicit repo (name/path/URL) with no
       CWD default; ``--yes``/non-TTY aborts with a usage error.
    """
    if explicit:
        repo = repos.resolve_repo(explicit, cwd, depth=depth)
        return repo, check_or_record(tid, str(repo), yes=yes, persist=persist)
    known = [str(Path(r).expanduser()) for r in linked_repos(tid)]
    if len(known) == 1:
        return Path(known[0]).expanduser(), "ok"
    if len(known) > 1:
        return _pick_linked(tid, known, cwd, depth=depth, yes=yes, persist=persist)
    if yes or not _is_tty():
        from .errors import HarnessError
        raise HarnessError(
            f"tracker {tid} has no linked repo — pass --repo <name|path|URL>.",
            exit_code=2)
    return _ask_manual(tid, cwd, depth=depth, persist=persist)


def _pick_linked(tid: str, known: list[str], cwd: Path, depth: int = 7,
                 yes: bool = False, persist: bool = True) -> tuple[Path, str]:
    """Step 3B: choose one of several linked repos."""
    # Issue #29: never silently pick the first repo — an explicit --repo
    # is required even under --yes/non-TTY (no Run-logs surprise).
    raise HarnessError(
        f"tracker {tid} is linked to multiple repos: {', '.join(known)}.\n"
        "  Re-run with --repo <name|path> to pick one.",
        exit_code=2,
    )


def _ask_manual(tid: str, cwd: Path, depth: int = 7,
                persist: bool = True) -> tuple[Path, str]:
    """Step 3C: no linked repos — ask for one (TTY) or abort."""
    if not _is_tty():
        raise HarnessError(
            f"tracker {tid} has no linked repo.\n"
            "  Re-run with --repo <name|path|url>.",
            exit_code=2,
        )
    try:
        answer = input(f"tracker {tid} has no linked repo — enter repo [name/path/URL]: ").strip()
    except EOFError:
        answer = ""
    if not answer:
        raise HarnessError("aborted", exit_code=2)
    target = repos.resolve_repo(answer, cwd, depth=depth)
    return target, check_or_record(tid, str(target), yes=True, persist=persist)


def _is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def eprint_note(msg: str) -> None:
    print(msg, file=sys.stderr)


def _confirm(tid: str, known: list[str], repo: str) -> bool:
    """Ask on a TTY whether *repo* may join *tid*'s mapping."""
    try:
        tty = sys.stdin.isatty()
    except (AttributeError, OSError, ValueError):
        return False
    if not tty:
        raise HarnessError(
            f"tracker {tid} is linked to {', '.join(known)}; "
            f"refusing to use a different repo ({repo}).\n"
            "  Re-run with --yes to link it, or pass --repo with a linked repo.",
            exit_code=2,
        )
    try:
        answer = input(
            f"tracker {tid} is linked to {', '.join(known)}; "
            f"use repo {repo} anyway? [y/N] "
        ).strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")




# ── my issues / candidates listing (issue #7 task 5) ──────────────────

# Initial (default) fetcher query: assigned to me OR created by me, all
# statuses, last 90 days — the widest slice the Recent-issues filter can
# serve from cache. Selections outside it (longer range, "all issues"
# scope) re-query the tracker with the user's own query.
_INITIAL_ISSUES_DAYS = 90


def _issues_jql(days: int = _INITIAL_ISSUES_DAYS, mine: bool = True) -> str:
    """JQL for the issues listing: *days*-day window, optionally limited to
    issues assigned to or reported by me. JQL relative offsets are days."""
    who = "(reporter = currentUser() OR assignee = currentUser()) AND " if mine else ""
    return f"{who}created >= -{days}d ORDER BY created DESC"


def _issues_jql_split(days: int = _INITIAL_ISSUES_DAYS) -> list[tuple[str, str]]:
    """Mine-scoped JQL as two origin-tagged queries: assignee-me and
    reporter-me. The tag becomes the row's `assigned_to_me` /
    `created_by_me` flag, so the UI can split "assigned to me" from
    "reported by me" without knowing my login."""
    base = f"created >= -{days}d ORDER BY created DESC"
    return [("assigned_to_me", f"assignee = currentUser() AND {base}"),
            ("created_by_me", f"reporter = currentUser() AND {base}")]

def parse_created(value: str) -> datetime | None:
    """Parse an ISO-8601 `created` stamp (jira `+0000`, gh/glab `Z`) to an
    aware UTC datetime; None when unparseable."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if re.search(r"[+-]\d{4}$", text):
        text = text[:-2] + ":" + text[-2:]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _jira_name(person) -> str:
    """Display/name login of a Jira user object (name preferred, display fallback)."""
    if not isinstance(person, dict):
        return ""
    return str(person.get("name") or person.get("displayName") or "")


def _jira_row(key: str, title: str, status: str, created: str,
              reporter: str = "", description: str = "", assignee: str = "") -> dict:
    return {"key": f"jira:{key}", "title": title, "status": status,
            "created": created,
            "url": refs.issue_url(f"jira:{key}") or "",
            "reporter": reporter, "assignee": assignee, "description": description}


def _parse_jira_payload(out: str | None) -> list[dict] | None:
    """Raw-API JSON (dict `{"issues":[…]}` or bare list) → rows; None when
    the output is not a usable payload (treated as seam unavailable)."""
    try:
        data = json.loads(out or "")
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if isinstance(data, dict):
        data = data.get("issues", [])
    if not isinstance(data, list):
        return None
    rows = []
    for it in data:
        fields = it.get("fields") or {}
        key = str(it.get("key") or "")
        if not key:
            continue
        rows.append(_jira_row(key, str(fields.get("summary") or ""),
                              str((fields.get("status") or {}).get("name") or ""),
                              str(fields.get("created") or ""),
                              _jira_name(fields.get("reporter")),
                              str(fields.get("description") or ""),
                              _jira_name(fields.get("assignee"))))
    return rows


def _parse_jira_plain(out: str) -> list[dict] | None:
    """`issue list --plain --no-headers` rows: 2+ space (or tab) column
    padding, right-anchored columns key|…summary…|status|created.
    None when the command produced output but none of it parses (broken
    seam), so the tier loop falls through and warns instead of silently
    reporting zero issues."""
    if not out.strip():
        return []
    rows = []
    for line in out.splitlines():
        parts = [p for p in re.split(r"\s{2,}|\t", line.strip()) if p]
        if len(parts) < 4 or not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", parts[0]):
            continue
        key, status, created = parts[0], parts[-2], parts[-1]
        rows.append(_jira_row(key, " ".join(parts[1:-2]), status, created))
    return rows or None


def _jira_my_issues(warnings: list[str], days: int = _INITIAL_ISSUES_DAYS,
                    mine: bool = True) -> list[dict]:
    """Jira rows via the first working seam: raw `request GET`, then
    jira-cli's `search --format json` (shim installs), then stock
    `issue list --plain`. All failing → [] + one collected warning.

    Mine-scoped rows carry their query origin: `assigned_to_me` /
    `created_by_me` (True when the row came back from the assignee /
    reporter query; both when it matched both — your PAN-1095 case).
    The *issue list* seam carries no user fields, so its rows tag both
    True (they matched the union JQL either way)."""
    jqls = (_issues_jql_split(days) if mine else [(None, _issues_jql(days, False))])
    out_rows: dict[str, dict] = {}
    errors = []
    for tag, jql in jqls:
        attempts = (
            ("request", ("jira-cli", "request", "GET",
                         f"search?jql={quote(jql)}"
                         "&maxResults=1000&fields=key,summary,status,created,reporter,assignee,description")),
            ("search", ("jira-cli", "search", jql,
                        "--limit", "1000", "--format", "json")),
            ("issue list", ("jira-cli", "issue", "list", "--plain", "--no-headers",
                            "--no-truncate", "--columns", "key,summary,status,created",
                            "--jql", jql)),
        )
        got: list[dict] | None = None
        used_plain = False
        for kind, args in attempts:
            try:
                out = run_cmd(*args)
            except HarnessError as e:
                errors.append(f"{kind}: {e}")
                continue
            got = _parse_jira_payload(out) if kind != "issue list" \
                else _parse_jira_plain(out or "")
            used_plain = (kind == "issue list")
            if got is not None:
                break
            errors.append(f"{kind}: unavailable/invalid output")
        if got is None:
            continue
        for row in got:
            key = row["key"]
            if tag is None:
                # All-scope / plain-seam: origin unknown → both True.
                row["assigned_to_me"] = True
                row["created_by_me"] = True
            elif used_plain:
                row["assigned_to_me"] = True
                row["created_by_me"] = True
            else:
                row[tag] = True
            if key in out_rows:
                prev = out_rows[key]
                prev["assigned_to_me"] = prev.get("assigned_to_me") or row.get("assigned_to_me")
                prev["created_by_me"] = prev.get("created_by_me") or row.get("created_by_me")
            else:
                row.setdefault("assigned_to_me", False)
                row.setdefault("created_by_me", False)
                out_rows[key] = row
    if not out_rows and errors:
        warnings.append("jira: " + "; ".join(errors))
    return list(out_rows.values())


def _gh_my_issues(warnings: list[str], days: int = _INITIAL_ISSUES_DAYS,
                  mine: bool = True) -> list[dict]:
    """GitHub issues in the last *days* (any state): authored or assigned
    to me by default (*mine*), any author otherwise. gh search cannot OR
    across qualifiers, so the mine path unions the author and assignee
    searches (deduped by key) — the only place that costs 2 remote calls.

    Mine-scoped rows carry their search origin: `assigned_to_me` /
    `created_by_me` (author search → created, assignee search → assigned;
    both when the row matched both). All-scope rows tag both True."""
    from datetime import datetime, timedelta, timezone
    fields = "repository,number,title,body,author,assignees,state,updatedAt,url,createdAt"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    scopes = ((("--author=@me", "created_by_me"), ("--assignee=@me", "assigned_to_me")) if mine else ((None, None),))
    data: list[tuple] = []
    for scope, tag in scopes:
        args = ["gh", "search", "issues",
                f"created:>={cutoff}", "--limit", "1000", "--json", fields]
        if scope:
            args.insert(3, scope)
        try:
            out = run_cmd(*args)
        except HarnessError as e:
            warnings.append(f"github: {e}")
            continue
        try:
            items = json.loads(out or "[]")
        except json.JSONDecodeError:
            warnings.append("github: cannot parse gh search output")
            continue
        if isinstance(items, list):
            data += [(it, tag) for it in items]
    rows: dict[str, dict] = {}
    for it, tag in data:
        repo = (it.get("repository") or {}).get("nameWithOwner") or ""
        num = it.get("number")
        if not repo or num is None:
            continue
        key = f"github:{repo}#{num}"
        row = rows.get(key)
        if row is None:
            row = {"key": key, "title": it.get("title", ""),
                   "url": it.get("url", ""),
                   "status": str(it.get("state") or "open").lower(),
                   "created": it.get("createdAt", ""),
                   "author": (it.get("author") or {}).get("login", "") or "",
                   "reporter": (it.get("author") or {}).get("login", "") or "",
                   "assignees": [str(a.get("login") or "") for a in (it.get("assignees") or []) if a.get("login")],
                   "assigned_to_me": False, "created_by_me": False,
                   "description": it.get("body", "") or ""}
            rows[key] = row
        if tag is None:
            row["assigned_to_me"] = True
            row["created_by_me"] = True
        else:
            row[tag] = True
    return list(rows.values())


_CACHE_TTL_SECONDS = 3600
_CACHE_SWR_SECONDS = max(_CACHE_TTL_SECONDS * 10, 24 * 3600)


def _cache_key(source: str, days: int, mine: bool) -> str:
    """Cache variant for a (source, window, scope) query — selections
    outside the initial superset land on their own key, so a broad
    requery only ever hits the network once per variant."""
    return f"{source}:{'mine' if mine else 'all'}{max(1, int(days))}"


def _cached_source(source: str, fetch, warn: list,
                   force: bool, cache_key: str | None = None) -> list[dict]:
    """Per-source issue rows: cached rows are served within
    max(1h * 10, 1 day); older/absent entries trigger a live fetch that
    refreshes the cache (the next call serves the fresh rows, so a stale
    table never blocks on the network).

    Pre-cutover (no state.db) or any cache failure → live fetch, never raise.
    """
    from datetime import datetime, timezone
    from . import store_sqlite as sq
    db = sq.db_path()
    key = cache_key or source
    if not force and db.exists():
        try:
            rows, fetched_at = sq.get_issue_cache(db, key)
            if rows and fetched_at:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(fetched_at)).total_seconds()
                if age < _CACHE_SWR_SECONDS:
                    return rows
        except Exception:
            pass
    try:
        rows = fetch(warn)
    except Exception as e:
        warn.append(f"{source}: {e}")
        return []
    try:
        if db.exists():
            sq.set_issue_cache(db, key, rows)
    except Exception:
        pass
    return rows


def _tracker_host(url: str) -> str:
    """Lowercased host of a tracker remote_url ("" when unparseable)."""
    try:
        from urllib.parse import urlparse
        return (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""


def _domain_blocked(host: str, blocked: list[str]) -> bool:
    """True when *host* equals or is a subdomain of any blocked domain."""
    h = (host or "").lower().strip()
    if not h:
        return False
    for d in blocked:
        dd = (d or "").lower().strip().lstrip(".")
        if dd and (h == dd or h.endswith("." + dd)):
            return True
    return False


def _enabled_sources(warnings: list[str] | None = None) -> set[str]:
    """Vendors with at least one usable linked tracker.

    A source stays live while ANY linked tracker of that vendor is usable
    (enabled, fetch on, host not domain-blocked); no linked tracker → on
    (unchanged pre-registration behavior). A vendor whose trackers exist
    but are all unusable is reported once in *warnings* so an empty
    Recent-issues table explains itself instead of silently blanking.
    """
    from collections import OrderedDict
    try:
        from . import store as _store
        trackers = _store.load_trackers()
        cfg = _store.load_config()
    except Exception:
        return {"jira", "github"}
    blocked = list((cfg or {}).get("disabled_tracker_domains", []) or [])
    matched: OrderedDict[str, bool] = OrderedDict()
    for tid, v in trackers.items():
        vendor = str((v or {}).get("vendor", "") or "").lower()
        for source in ("jira", "github"):
            match = (vendor == source) or (source == "github" and str(tid).startswith("gitlab:"))
            if source == "jira" and str(tid).startswith("jira:") and not vendor:
                match = True
            if not match:
                continue
            usable = bool((v or {}).get("is_enabled", True)) \
                and bool((v or {}).get("fetch_enabled", True)) \
                and not _domain_blocked(_tracker_host(str((v or {}).get("remote_url", "") or "")), blocked)
            matched[source] = matched.get(source, False) or usable
    dead = sorted(s for s, usable in matched.items() if not usable)
    if dead and warnings is not None:
        skipped = ", ".join(dead)
        msg = f"skipped disabled issue source(s): {skipped}"
        if msg not in warnings:
            warnings.append(msg)
    return {"jira", "github"} - set(dead)


def _source_enabled(source: str) -> bool:
    """Fetch gate for an issue source (any-usable-tracker wins; see _enabled_sources)."""
    return source in _enabled_sources()

def list_my_issues(warnings: list[str] | None = None,
                   force: bool = False,
                   days: int = _INITIAL_ISSUES_DAYS,
                   mine: bool = True) -> list[dict]:
    """My issues across sources as {key, title, url, status, created,
    reporter, author, description}.

    Default (*days*=90, *mine*=True) serves the initial superset from the
    variant cache: jira issues assigned to or reported by me (all
    statuses, last 90 days) + GitHub issues authored or assigned to me
    (any state, last 90 days). A caller whose selection is not covered by
    it (longer window, "all issues" scope) re-queries with its own
    *days*/*mine* — each variant caches under its own key, so a broad
    requery costs at most one network round per (window, scope).
    GitLab issues are intentionally omitted (Jira covers work tracking;
    GitLab surfaces via PR/MR candidates).
    A source is skipped only when every linked tracker of that vendor is
    unusable (disabled, fetch off, or domain-blocked); the skip is reported
    in *warnings* so the UI explains the empty table.
    Per-source failure contributes [] plus a warning — to stderr when
    *warnings* is None, else appended to the caller's list; never raises.
    Cached rows are served within max(1h * 10, 1 day); older entries
    trigger a live fetch unless *force* is set.
    """
    days = max(1, int(days or _INITIAL_ISSUES_DAYS))
    warn = warnings if warnings is not None else []
    rows: list[dict] = []
    live = _enabled_sources(warn)
    if "jira" in live:
        rows += _cached_source("jira", lambda w: _jira_my_issues(w, days, mine),
                               warn, force, _cache_key("jira", days, mine))
    if "github" in live:
        rows += _cached_source("github", lambda w: _gh_my_issues(w, days, mine),
                               warn, force, _cache_key("github", days, mine))
    if warnings is None:
        for w in warn:
            print(f"warning: {w}", file=sys.stderr)
    return rows
