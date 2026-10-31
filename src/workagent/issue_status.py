"""Best-effort issue status transitions (never raises).

Canonical states: in progress / code review / ready to release / released / done.

Tracker mapping:
  jira     → direct move: `jira issue move KEY "State"` (state names are the
             canonical lifecycle states; error output lists valid states).
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
    """Parse an issue ref; None + warning when unparseable or not an issue.

    Link-key-shaped `IPG-934@projectx` refs are tolerated (repo suffix
    stripped): link rows rebuild `issue` from the suffixed ref_key.
    """
    ref = (key_or_ref or "").strip()
    if re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+@[\w.-]+", ref):
        ref = ref.split("@", 1)[0]
    try:
        parsed = _refs.parse_ref(ref)
    except HarnessError as e:
        eprint(f"warning: cannot set issue status ({e})")
        return None
    if parsed.get("kind") != "issue":
        return None
    return parsed


def _jira_state_variants(state: str) -> list[str]:
    """Candidate remote state spellings for a canonical lifecycle state.

    IPG workflow states: To Do / In Progress / Review / Product Test /
    Ready to Release / Blocked / Released / Done. Bare "Code Review" does
    not exist and is absent. Cross-state fallbacks pair Review with Ready
    to Release in both directions; the release mark prefers Released over
    Done.
    """
    s = (state or "").strip().lower()
    variants = {
        "in progress": ["In Progress", "In progress"],
        "code review": ["Review", "Ready to Release", "Ready to release"],
        "ready to release": ["Ready to Release", "Ready to release", "Review"],
        "released": ["Released", "Done"],
        "done": ["Done"],
    }
    return variants.get(s, [state])


def _available_states(msg: str) -> list[str]:
    """State names quoted in a failed `issue move` error
    (`Available states for issue …: 'A', 'B'`); [] when absent."""
    m = re.search(r"[Aa]vailable states.*?:(.*)$", msg or "")
    if not m:
        return []
    return re.findall(r"'([^']+)'", m.group(1))


def _jira_move(number: str, state: str) -> None:
    """Move a Jira issue to *state*.

    `issue move` takes no `--no-input` (all args are positional); a failed
    variant whose error lists the valid transitions narrows the remaining
    retries to those states, and the final error names them.
    """
    variants = _jira_state_variants(state)
    last: HarnessError | None = None
    valid: set[str] = set()
    for i, variant in enumerate(variants):
        if valid and variant.lower() not in valid:
            continue
        try:
            run_cmd("jira", "issue", "move", number, variant)
            return
        except HarnessError as e:
            last = e
            if i == 0:
                valid = {s.lower() for s in _available_states(str(e))}
            continue
    detail = f" (valid: {', '.join(sorted(valid))})" if valid else ""
    raise HarnessError(f"cannot move {number} to {state}{detail}"
                       + (f": {last}" if last else ""))


def _label_siblings(state: str) -> list[str]:
    """Every other canonical `status: *` label (for removal on swap)."""
    want = f"{_STATUS_PREFIX}{(state or '').strip().lower()}"
    return [f"{_STATUS_PREFIX}{s}" for s in STATES
            if f"{_STATUS_PREFIX}{s}" != want]


def _issue_state(number: str, tool: str, target: str) -> str:
    """Best-effort current issue state (`""` when unknown). Never raises."""
    try:
        if tool == "jira":
            out = run_cmd("jira", "issue", "view", number, "--raw")
            data = json.loads(out or "{}")
            fields = data.get("fields", {}) if isinstance(data, dict) else {}
            cur = fields.get("status", {}) if isinstance(fields, dict) else {}
            name = cur.get("name", "") if isinstance(cur, dict) else str(cur)
            return str(name or "").strip().lower()
        if tool == "gh":
            out = run_cmd("gh", "issue", "view", target, "--json", "state,labels")
            data = json.loads(out or "{}")
            if str(data.get("state", "")).upper() == "CLOSED":
                return "done"
            for lb in data.get("labels") or []:
                n = (lb.get("name", "") if isinstance(lb, dict) else str(lb))
                if n.startswith(_STATUS_PREFIX):
                    return n[len(_STATUS_PREFIX):].strip().lower()
    except Exception:
        pass
    return ""


def pr_review_state(pr_url: str, cwd: str | None = None) -> str:
    """Best-effort PR/MR review verdict: approved | changes | open | merged | "".

    gh: reviewDecision (APPROVED / CHANGES_REQUESTED). glab: best-effort from
    `glab mr view -F json` approval fields; unknown → "" (never raises).
    """
    try:
        parsed = _refs.parse_ref(pr_url)
    except HarnessError:
        return ""
    try:
        if parsed.get("tool") == "gh":
            out = run_cmd("gh", "pr", "view", parsed["url"], "--json",
                          "state,reviewDecision", cwd=cwd)
            data = json.loads(out or "{}")
            if str(data.get("state", "")).upper() == "MERGED":
                return "merged"
            dec = str(data.get("reviewDecision", "") or "").upper()
            if dec == "APPROVED":
                return "approved"
            if dec in ("CHANGES_REQUESTED", "REVIEW_REQUIRED_CHANGES"):
                return "changes"
            return "open"
        if parsed.get("tool") == "glab":
            out = run_cmd("glab", "mr", "view", parsed["url"], "-F", "json", cwd=cwd)
            data = json.loads(out or "{}")
            st = str(data.get("state", "") or "").lower()
            if st == "merged":
                return "merged"
            for key in ("approved", "approvals", "approved_by"):
                val = data.get(key)
                if val is True or (isinstance(val, list) and val):
                    return "approved"
            return "open" if st in ("opened", "open") else st
    except Exception:
        pass
    return ""


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
        if tool == "jira":
            if dry_run:
                eprint(f"dry-run: would move {number} -> {state}")
                return True
            _jira_move(number, state)
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


def settle_issue_post_work(issue_ref: str, pr_url: str = "", *, dry_run: bool = False) -> str:
    """Done-branch: `code review` when a PR/MR exists, else `done`.

    Returns the target state ("" when skipped). Best effort, never raises.
    """
    if not (issue_ref or "").strip():
        return ""
    target = "code review" if (pr_url or "").strip() else "done"
    ok = set_issue_status(issue_ref, target, dry_run=dry_run)
    return target if ok else ""


def settle_issue_post_review(issue_ref: str, verdict: str, *, dry_run: bool = False) -> str:
    """Review-branch: only from a review state; approved→`ready to release`, changes→`in progress`.

    The settled-from state is `review` in trackers that name it so (IPG),
    else the canonical `code review` alias.
    """
    verdict = (verdict or "").strip().lower()
    if verdict not in ("approved", "merged", "changes"):
        return ""
    if not (issue_ref or "").strip():
        return ""
    parsed = _parse(issue_ref)
    if parsed is None:
        return ""
    cur = _issue_state(parsed.get("number", ""), parsed.get("tool", ""),
                       parsed.get("url", "") or parsed.get("number", ""))
    if cur and cur not in ("code review", "review"):
        return ""
    target = "ready to release" if verdict in ("approved", "merged") else "in progress"
    ok = set_issue_status(issue_ref, target, dry_run=dry_run)
    return target if ok else ""


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
        # Link rows rebuild `issue` from the ref_key, repo suffix included
        # (`IPG-934@projectx`); strip it — parse_ref only knows bare keys.
        if re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+@[\w.-]+", ref):
            ref = ref.split("@", 1)[0]
        if ref and ref not in seen:
            seen.add(ref)
            found.append(ref)

    for sha in shas:
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
        # Always scan the subject/body too: a mapped commit can carry other
        # (or newer) keys than the link row, and the mapped ref may not
        # parse at all — never let one source silence the other.
        try:
            show = run_cmd("git", "-C", path, "log", "-1",
                           "--format=%s%n%b", sha) or ""
        except HarnessError:
            show = ""
        for m in _JIRA_KEY_RE.finditer(show):
            _add(m.group(1))
        for m in _GH_REF_RE.finditer(show):
            _add(f"{m.group(1)}#{m.group(2)}")
        for m in re.finditer(r"(?:^|\s)#(\d+)\b", show):
            _add(f"#{m.group(1)}")
    return found
