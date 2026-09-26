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
        want = refs.issue_key(parsed)
    except Exception:
        return ""
    for key, entry in links.items():
        if key != issue_ref and key != want:
            continue
        repo = str((entry or {}).get("repo", ""))
        if repo:
            return repo
    tid = tracker_id(parsed)
    if not tid:
        return ""
    known = linked_repos(tid)
    if len(known) == 1:
        return known[0]
    return ""


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

# Corrected from the task brief against the live Jira (tribe.jibit.cloud):
# its status is "To Do" (not "To-Do"), and JQL "-2m" means 2 minutes
# (no month unit) — the intended window is two months.
_MY_ISSUES_JQL = ('reporter = currentUser() AND status in ("To Do", "In Progress") '
                  'AND created >= -60d ORDER BY created DESC')


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


def _jira_row(key: str, title: str, status: str, created: str) -> dict:
    return {"key": f"jira:{key}", "title": title, "status": status,
            "created": created,
            "url": refs.issue_url(f"jira:{key}") or ""}


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
                              str(fields.get("created") or "")))
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


def _jira_my_issues(warnings: list[str]) -> list[dict]:
    """Jira rows via the first working seam: raw `request GET`, then
    jira-cli's `search --format json` (shim installs), then stock
    `issue list --plain`. All failing → [] + one collected warning."""
    attempts = (
        ("request", ("jira-cli", "request", "GET",
                     f"search?jql={quote(_MY_ISSUES_JQL)}"
                     "&maxResults=100&fields=key,summary,status,created")),
        ("search", ("jira-cli", "search", _MY_ISSUES_JQL,
                    "--limit", "100", "--format", "json")),
        ("issue list", ("jira-cli", "issue", "list", "--plain", "--no-headers",
                        "--no-truncate", "--columns", "key,summary,status,created",
                        "--jql", _MY_ISSUES_JQL)),
    )
    errors = []
    for kind, args in attempts:
        try:
            out = run_cmd(*args)
        except HarnessError as e:
            errors.append(f"{kind}: {e}")
            continue
        rows = _parse_jira_payload(out) if kind != "issue list" \
            else _parse_jira_plain(out or "")
        if rows is not None:
            return rows
        errors.append(f"{kind}: unavailable/invalid output")
    warnings.append("jira: " + "; ".join(errors))
    return []


def _gh_my_issues(warnings: list[str]) -> list[dict]:
    try:
        out = run_cmd("gh", "search", "issues", "--author=@me", "--state=open",
                      "--limit", "100", "--json",
                      "repository,number,title,updatedAt,url,createdAt")
    except HarnessError as e:
        warnings.append(f"github: {e}")
        return []
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        warnings.append("github: cannot parse gh search output")
        return []
    rows = []
    for it in data if isinstance(data, list) else []:
        repo = (it.get("repository") or {}).get("nameWithOwner") or ""
        num = it.get("number")
        if not repo or num is None:
            continue
        rows.append({"key": f"github:{repo}#{num}", "title": it.get("title", ""),
                     "url": it.get("url", ""), "status": "open",
                     "created": it.get("createdAt", "")})
    return rows


_CACHE_TTL_SECONDS = 3600


def _cached_source(source: str, fetch, warn: list,
                   force: bool) -> list[dict]:
    """Per-source issue rows: fresh cache hit wins, else live fetch + store.

    Pre-cutover (no state.db) or any cache failure → live fetch, never raise.
    """
    from datetime import datetime, timezone
    from . import store_sqlite as sq
    db = sq.db_path()
    if not force and db.exists():
        try:
            rows, fetched_at = sq.get_issue_cache(db, source)
            if rows and fetched_at:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(fetched_at)).total_seconds()
                if age < _CACHE_TTL_SECONDS:
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
            sq.set_issue_cache(db, source, rows)
    except Exception:
        pass
    return rows


def list_my_issues(warnings: list[str] | None = None,
                   force: bool = False) -> list[dict]:
    """My open issues across sources as {key, title, url, status, created}.

    jira (reporter=me, To Do/In Progress, last 2 months) + GitHub issues
    authored by me (state=open). GitLab issues are intentionally omitted
    (Jira covers work tracking; GitLab surfaces via PR/MR candidates).
    Per-source failure contributes [] plus a warning — to stderr when
    *warnings* is None, else appended to the caller's list; never raises.
    Fresh per-source cache rows (<1h) win unless *force* is set.
    """
    warn = warnings if warnings is not None else []
    rows = (_cached_source("jira", _jira_my_issues, warn, force)
            + _cached_source("github", _gh_my_issues, warn, force))
    rows.sort(key=lambda r: r["created"], reverse=True)
    if warnings is None:
        for w in warn:
            print(f"warning: {w}", file=sys.stderr)
    return rows
