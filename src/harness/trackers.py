"""Tracker↔repo relation guards.

Persisted in state/tracker_repos.json as
``{tracker_id: {"repos": [<repo path>, ...]}}``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import pick, refs, repos, store
from .errors import HarnessError


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



def check_or_record(tid: str, repo: str, yes: bool = False, persist: bool = True) -> str:
    """Enforce the tracker↔repo relation for *tid* against *repo*.

    Returns ``"ok"`` when already linked, ``"recorded"`` when a new
    mapping was (or, with ``persist=False``, would be) persisted.
    Raises ``HarnessError`` (exit 2) when the repo differs from the
    stored mapping and the user does not confirm.
    """
    norm = str(Path(repo).expanduser().resolve()) if repo else repo
    if not tid:
        return "ok"
    cfg = store.load_config()
    trackers = cfg.setdefault("trackers", {})
    entry = trackers.get(tid)
    if entry is None:
        if persist:
            trackers[tid] = {"repos": [norm]}
            store.save_config(cfg)
        return "recorded"
    known = [str(Path(r).expanduser().resolve()) for r in entry.get("repos", [])]
    if norm in known:
        return "ok"
    if yes or _confirm(tid, known, norm):
        if persist:
            entry.setdefault("repos", []).append(norm)
            store.save_config(cfg)
        return "recorded"
    raise HarnessError("aborted", exit_code=2)


def linked_repos(tid: str) -> list[str]:
    """Stored repo paths for *tid* (raw, unnormalized)."""
    return list(store.load_config().get("trackers", {}).get(tid, {}).get("repos", []))


def resolve_for_tracker(tid: str, explicit: str | None, cwd: Path,
                        depth: int = 7, yes: bool = False,
                        persist: bool = True) -> tuple[Path, str]:
    """Pick the repo for *tid* following the cwd/linked-repos flow.

    1. ``--repo`` given → resolve it, guard via :func:`check_or_record`.
    2. cwd inside a linked repo (worktrees resolve to main checkout) → use it.
    3. cwd inside another repo → y/N to use it (No → step 4); non-TTY
       without ``--yes`` aborts unless cwd is not a repo at all.
    4. cwd outside any repo → single linked repo wins; multiple → pick;
       none → type a path/name/URL (TTY) or abort (non-TTY).
    Returns ``(repo_path, outcome)`` where outcome mirrors
    :func:`check_or_record`.
    """
    if explicit:
        target = repos.resolve_repo(explicit, cwd, depth=depth)
        return target, check_or_record(tid, str(target), yes=yes, persist=persist)
    root = repos.repo_root(cwd)
    main = None
    if root is not None:
        try:
            main = repos.main_repo_root(root)
        except HarnessError:
            main = root
    if main is None:
        known = linked_repos(tid)
        if len(known) == 1:
            single = repos.resolve_repo(known[0], cwd, depth=depth)
            eprint_note(f"note: using linked repo {single} for tracker {tid}")
            return single, check_or_record(tid, str(single), yes=True, persist=persist)
        if len(known) > 1:
            return _pick_linked(tid, known, cwd, depth=depth, yes=yes, persist=persist)
        return _ask_manual(tid, cwd, depth=depth, persist=persist)
    known_now = [str(Path(rr).expanduser().resolve()) for rr in linked_repos(tid)]
    if str(main.expanduser().resolve()) in known_now:
        return main, "ok"
    # Cwd repo not linked: y/N to use it; "no" falls through to linked repos.
    if _confirm_use_cwd(tid, known_now, str(main), yes=yes):
        return main, check_or_record(tid, str(main), yes=True, persist=persist)
    known = linked_repos(tid)
    if len(known) == 1:
        single = repos.resolve_repo(known[0], cwd, depth=depth)
        eprint_note(f"note: using linked repo {single} for tracker {tid}")
        return single, check_or_record(tid, str(single), yes=True, persist=persist)
    if len(known) > 1:
        return _pick_linked(tid, known, cwd, depth=depth, yes=yes, persist=persist)
    return _ask_manual(tid, cwd, depth=depth, persist=persist)


def _pick_linked(tid: str, known: list[str], cwd: Path, depth: int = 7,
                 yes: bool = False, persist: bool = True) -> tuple[Path, str]:
    """Step 3B: choose one of several linked repos."""
    if not _is_tty():
        raise HarnessError(
            f"tracker {tid} is linked to multiple repos: {', '.join(known)}.\n"
            "  Re-run with --repo <name|path> or --yes to use the first.",
            exit_code=2,
        )
    if yes:
        first = repos.resolve_repo(known[0], cwd, depth=depth)
        return first, check_or_record(tid, str(first), yes=True, persist=persist)
    idx = pick.pick(f"tracker {tid} is linked to multiple repos:", known)
    if idx is None:
        raise HarnessError("aborted", exit_code=2)
    target = repos.resolve_repo(known[idx], cwd, depth=depth)
    return target, check_or_record(tid, str(target), yes=True, persist=persist)


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


def _confirm_use_cwd(tid: str, known: list[str], repo: str, yes: bool = False) -> bool:
    """Step 2 y/N: use the unlinked cwd repo for *tid*?"""
    if yes:
        return True
    if not _is_tty():
        # Non-interactive: cwd repo unusable without consent — signal fall
        # through by returning False when linked repos exist, else abort.
        if known:
            eprint_note(f"note: cwd repo {repo} not linked to {tid}; using linked repo.")
            return False
        raise HarnessError(
            f"tracker {tid} is not linked to repo {repo}.\n"
            "  Re-run with --yes to link it, or pass --repo with a linked repo.",
            exit_code=2,
        )
    if known:
        prompt = (f"cwd repo {repo} is not linked to {tid} "
                  f"(linked: {', '.join(known)}); use cwd anyway? [y/N] ")
    else:
        prompt = f"tracker {tid} is not linked to any repo; link cwd repo {repo}? [y/N] "
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")
