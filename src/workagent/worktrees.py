"""Worktree identity: unique by ref key, branch name, and path."""
from __future__ import annotations

from pathlib import Path

from . import pick as _pick
from . import refs, store
from .errors import HarnessError


def _norm_path(p: str) -> str:
    return str(Path(p).expanduser().resolve()) if p else ""


def recorded_key(worktree: str, branch: str, links: dict) -> str | None:
    """Link key already recording this worktree/branch, else None.

    Branch is the unique worktree identity (exact match first); the
    normalized path is the fallback. Reusing the recorded row keeps
    review from inserting a second row for the same path/branch (which
    collides on the worktrees.branch UNIQUE key).
    """
    for k, v in links.items():
        if isinstance(v, dict) and branch and branch == (v.get("branch", "") or ""):
            return k
    want = _norm_path(worktree)
    if want:
        for k, v in links.items():
            if isinstance(v, dict) and want == _norm_path(v.get("worktree", "") or ""):
                return k
    return None


def resolve_worktree(ref: str, links: dict) -> str | list[str] | None:
    """Resolve ref to a worktree key via key, branch, or path.

    Exact ref key first, then parseable issue/PR refs, then branch-name
    match, then path match, then substring over keys/branches/paths.
    Returns the key, a list on ambiguity, or None.
    """
    if ref in links:
        return ref
    try:
        parsed = refs.parse_ref(ref)
    except HarnessError:
        parsed = None
    needle = ref
    exact: str | None = None
    if parsed is not None:
        key = refs.issue_key(parsed)
        pr_key = f"pr:{parsed['url']}" if parsed.get("repo") else key
        if key in links:
            exact = key
        elif pr_key in links:
            exact = pr_key
        needle = parsed.get("number", "") or ref
    for k, v in links.items():
        if needle and needle == (v.get("branch", "") or ""):
            return k
    if needle:
        want = _norm_path(needle)
        for k, v in links.items():
            if want and want == _norm_path(v.get("worktree", "") or ""):
                return k
    matches = [k for k, v in links.items()
               if needle in k
               or needle in (v.get("worktree", "") or "")
               or needle in (v.get("branch", "") or "")]
    if not matches:
        bare = needle.split("/")[-1].split("--")[0].strip()
        if bare and bare != needle:
            matches = [k for k, v in links.items()
                       if bare in k
                       or bare in (v.get("worktree", "") or "")
                       or bare in (v.get("branch", "") or "")]
    if exact is not None and exact not in matches:
        matches = [exact, *matches]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches


def pick_worktree(ref: str, resolved: str | list[str], links: dict | None = None) -> str:
    """Disambiguate multiple fuzzy matches interactively."""
    import sys
    from .cli import _fail, EXIT_USAGE
    if isinstance(resolved, str):
        return resolved
    if not sys.stdin.isatty():
        _fail(f"ambiguous ref {ref!r} matches: {', '.join(resolved)}.\n"
              "  Re-run with the exact worktree key.", EXIT_USAGE)
    options = [(k, (links or {}).get(k, {}).get("branch", "")) for k in resolved]
    idx = _pick.pick(f"multiple worktrees match {ref!r}:", options)
    if idx is None:
        _fail("aborted", EXIT_USAGE)
    return resolved[idx]


def effective_repo_for_entry(entry: dict, default: Path | None = None) -> Path | None:
    """Authoritative main repo for a link entry.

    A live worktree resolves via ``--git-common-dir`` to its real main
    checkout, which wins over the recorded ``repo`` (links created while
    the server cwd was an unrelated checkout record the wrong repo).
    Falls back to *default* (or the recorded repo) when no worktree.
    """
    from . import repos
    wt = (entry.get("worktree", "") or "")
    if wt and Path(wt).expanduser().is_dir():
        try:
            return repos.main_repo_root(Path(wt).expanduser())
        except HarnessError:
            pass
    rec = (entry.get("repo", "") or "")
    if rec:
        return Path(rec).expanduser()
    return default


def is_valid_worktree(path: str) -> bool:
    """True when path exists and is a live git worktree."""
    from .errors import run_cmd
    p = Path(path).expanduser()
    if not path or not p.is_dir():
        return False
    if run_cmd("git", "-C", str(p), "rev-parse", "--git-common-dir",
               check=False) is None:
        return False
    common = run_cmd("git", "-C", str(p), "rev-parse",
                     "--path-format=absolute", "--git-common-dir",
                     check=False)
    if not common:
        return False
    out = run_cmd("git", "-C", str(p), "worktree", "list", "--porcelain",
                  check=False) or ""
    want = str(p.resolve())
    listed = [ln[9:] for ln in out.splitlines() if ln.startswith("worktree ")]
    return any(str(Path(x).resolve()) == want for x in listed)


def worktree_pr_url(key: str, entry: dict) -> str | None:
    """Recorded PR/MR URL; falls back to a live branch query."""
    if entry.get("pr_url"):
        return entry["pr_url"]
    repo = entry.get("repo", "")
    branch = entry.get("branch", "")
    if not (repo and branch and Path(repo).exists()):
        return None
    from .cli import _repo_tool
    tool = _repo_tool(repo)
    if not tool:
        return None
    try:
        pr = refs.latest_pr(refs.fetch_pr_list_for_branch(tool, branch, cwd=repo))
    except HarnessError:
        return None
    if pr:
        store.record_link(key, {"pr_url": pr["url"]})
        return pr["url"]
    return None
