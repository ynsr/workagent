"""Best-effort issue status transitions (never raises).

Canonical states: in progress / code review / ready to release / released / done.

Tracker mapping:
  jira-cli → discover-then-transition: `jira-cli issue KEY --format json`
             lists available transitions; pick the target whose name matches
             `state` (case-insensitive), run `jira-cli issue KEY transition <ID>`.
  gh       → label swap: `gh issue edit <target> --add-label "status: <state>"`
             `--remove-label` every other `status: *` sibling; `gh issue close
             --reason completed` when state == done.
  glab     → label swap: `glab issue update <target> --label "status: <state>"`
             `--unlabel` siblings (+ `glab issue close` when state == done).

Only issue refs transition (never PR/MR notes). All failures warn on stderr
and return False. `--dry-run` prints instead of writing everywhere.
"""
from __future__ import annotations

import json
import re

from . import refs as _refs
from .cli_core import eprint
from .errors import HarnessError, run_cmd

#: Canonical lifecycle states.
STATES = (
    "in progress",
    "code review",
    "ready to release",
    "released",
    "done",
)

_STATUS_PREFIX = "status: "

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]*-\d+)\b")
_GH_REF_RE = re.compile(r"\b([\w.\-]+/[\w.\-]+)#(\d+)\b")


def _parse(key_or_ref: str) -> dict | None:
    """Parse an issue ref; None + warning when unparseable or not an issue."""
    try:
        parsed = _refs.parse_ref((key_or_ref or "").strip())
    except HarnessError as e:
        eprint(f"warning: cannot set issue status ({e})")
        return None
    if parsed.get("kind") != "issue":
        return None
    return parsed


def _jira_transition_id(number: str, state: str) -> str | None:
    """Transition ID whose target matches *state*; None when not offered."""
    out = run_cmd("jira-cli", "issue", number, "--format", "json")
    try:
        data = json.loads(out or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HarnessError(f"cannot parse jira-cli output for {number}")
    want = (state or "").strip().lower()
    candidates = data.get("transitions") or data.get("availableTransitions") or []
    if isinstance(candidates, dict):
        candidates = list(candidates.values())
    for t in candidates if isinstance(candidates, list) else []:
        if not isinstance(t, dict):
            continue
        target = t.get("to") or t.get("toStatus") or {}
        name = t.get("name", "") if isinstance(target, str) else (
            target.get("name", "") if isinstance(target, dict) else "")
        if str(name).strip().lower() == want:
            tid = t.get("id", "")
            return str(tid) if str(tid) else None
        # Some jira-cli builds nest under status: {name: ...}.
        status = t.get("status") or {}
        sname = status.get("name", "") if isinstance(status, dict) else ""
        if str(sname).strip().lower() == want:
            tid = t.get("id", "")
            return str(tid) if str(tid) else None
    return None


def _label_siblings(state: str) -> list[str]:
    """Every other canonical `status: *` label (for removal on swap)."""
    want = f"{_STATUS_PREFIX}{(state or '').strip().lower()}"
    return [f"{_STATUS_PREFIX}{s}" for s in STATES
            if f"{_STATUS_PREFIX}{s}" != want]


def set_issue_status(key_or_ref: str, state: str, *, dry_run: bool = False) -> bool:
    """Transition the issue at *key_or_ref* to *state*; True on success.

    Best effort: warnings go to stderr, failures return False, never raises.
    With *dry_run*, prints the transition instead of running it and
    returns True.
    """
    state = (state or "").strip().lower()
    if state not in STATES:
        eprint(f"warning: unknown issue state {state!r} — skipping status update")
        return False
    parsed = _parse(key_or_ref)
    if parsed is None:
        return False
    tool = parsed.get("tool", "")
    number = parsed.get("number", "")
    url = parsed.get("url", "")
    try:
        if tool == "jira-cli":
            if dry_run:
                eprint(f"dry-run: would transition {number} -> {state}")
                return True
            tid = _jira_transition_id(number, state)
            if tid is None:
                eprint(f"warning: no {state!r} transition offered for {number} — skipping")
                return False
            run_cmd("jira-cli", "issue", number, "transition", tid)
        elif tool == "gh":
            target = url or number
            if dry_run:
                eprint(f"dry-run: would set {target} status -> {state}")
                return True
            siblings = _label_siblings(state)
            args = ["gh", "issue", "edit", target,
                    "--add-label", f"{_STATUS_PREFIX}{state}"]
            if siblings:
                args += ["--remove-label", ",".join(siblings)]
            run_cmd(*args)
            if state == "done":
                run_cmd("gh", "issue", "close", target, "--reason", "completed")
        elif tool == "glab":
            target = url or number
            if dry_run:
                eprint(f"dry-run: would set {target} status -> {state}")
                return True
            siblings = _label_siblings(state)
            args = ["glab", "issue", "update", target,
                    "--label", f"{_STATUS_PREFIX}{state}"]
            if siblings:
                args += ["--unlabel", ",".join(siblings)]
            run_cmd(*args)
            if state == "done":
                run_cmd("glab", "issue", "close", target)
        else:
            return False
    except HarnessError as e:
        eprint(f"warning: issue status update failed for {key_or_ref}: {e}")
        return False
    eprint(f"issue status -> {state}: {key_or_ref}")
    return True


def discover_released_refs(path: str, old_tip: str, new_tip: str) -> list[str]:
    """Issue refs touched by `old_tip..new_tip` on a release branch.

    Best effort, warns and returns [] on any failure. Strategy per commit:
    (1) back-map via `git branch -r --contains <sha>` against linked
    worktree branch names, else (2) regex subject/body scan for Jira keys /
    `owner/repo#N` / bare `#N`. Results deduped, order-preserved.
    """
    try:
        out = run_cmd("git", "-C", path, "rev-list", f"{old_tip}..{new_tip}")
    except HarnessError as e:
        eprint(f"warning: release issue discovery failed ({e})")
        return []
    shas = [s for s in (out or "").split() if s]
    if not shas:
        return []
    try:
        from . import store as _store
        links = _store.load_links(include_deleted=True)
    except Exception:
        links = {}
    branch_refs: dict[str, str] = {}
    for key, entry in (links or {}).items():
        branch = str((entry or {}).get("branch", "") or "").strip()
        issue = str((entry or {}).get("issue", "") or "").strip()
        if branch and issue:
            branch_refs.setdefault(branch, issue)
    found: list[str] = []
    seen: set[str] = set()

    def _add(ref: str) -> None:
        ref = (ref or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            found.append(ref)

    for sha in shas:
        mapped = False
        try:
            branches_out = run_cmd("git", "-C", path, "branch", "-r",
                                   "--contains", sha) or ""
        except HarnessError:
            branches_out = ""
        for line in branches_out.splitlines():
            name = line.strip().lstrip("* ").strip()
            short = name.split("/", 1)[1] if "/" in name else name
            ref = branch_refs.get(name) or branch_refs.get(short, "")
            if ref:
                _add(ref)
                mapped = True
        try:
            show = run_cmd("git", "-C", path, "log", "-1",
                           "--format=%s%n%b", sha) or ""
        except HarnessError:
            show = ""
        if not mapped:
            for m in _JIRA_KEY_RE.finditer(show):
                _add(m.group(1))
            for m in _GH_REF_RE.finditer(show):
                _add(f"{m.group(1)}#{m.group(2)}")
            for m in re.finditer(r"(?:^|\s)#(\d+)\b", show):
                _add(f"#{m.group(1)}")
    return found
