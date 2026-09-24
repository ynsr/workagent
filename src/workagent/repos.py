"""git repo helpers: detect repo, default branch, resolve target repo."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import store
from .errors import HarnessError, run_cmd


def repo_root(cwd: Path | None = None) -> Path | None:
    try:
        out = run_cmd("git", "rev-parse", "--show-toplevel", cwd=cwd or Path.cwd())
    except HarnessError:
        return None
    return Path(out) if out else None


def repo_name(path: Path) -> str:
    return path.resolve().name


def remote_url(path: Path) -> str | None:
    try:
        return run_cmd("git", "-C", str(path), "remote", "get-url", "origin")
    except HarnessError:
        return None


def default_branch(path: Path) -> str:
    """Detect default branch: gh/glab → origin/HEAD → common names."""
    host_cli = _detect_host_cli(path)
    if host_cli == "gh":
        try:
            out = run_cmd("gh", "repo", "view", "--json", "defaultBranchRef",
                          "--jq", ".defaultBranchRef.name", cwd=path)
            if out:
                return out
        except HarnessError:
            pass
    elif host_cli == "glab":
        try:
            out = run_cmd("glab", "repo", "view", "--output", "json", cwd=path)
            if out:
                name = json.loads(out).get("default_branch")
                if name:
                    return name
        except (HarnessError, json.JSONDecodeError, AttributeError):
            pass
    try:
        out = run_cmd("git", "-C", str(path), "symbolic-ref", "refs/remotes/origin/HEAD")
        if out:
            return out.split("/")[-1]
    except HarnessError:
        pass
    for candidate in ("main", "master", "develop"):
        for ref in (f"origin/{candidate}", candidate):
            try:
                run_cmd("git", "-C", str(path), "rev-parse", "--verify", ref)
                return candidate
            except HarnessError:
                continue
    raise HarnessError(f"cannot determine default branch for {path}", exit_code=2)


def current_branch(path: Path) -> str | None:
    try:
        return run_cmd("git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD")
    except HarnessError:
        return None


def branch_tip(worktree: str) -> str:
    """HEAD sha of *worktree*; "" when it cannot be determined."""
    try:
        return run_cmd("git", "-C", str(worktree), "rev-parse", "HEAD") or ""
    except HarnessError:
        return ""

def ahead_behind(path: Path, default_branch: str) -> dict | None:
    """Behind/ahead counts vs remote-tracking origin/<default> (no fetch).

    Returns {"behind": n, "ahead": n}, or None when the branch or
    remote-tracking ref is missing.
    """
    base = f"origin/{default_branch}"
    try:
        run_cmd("git", "-C", str(path), "rev-parse", "--verify", "-q", base)
    except HarnessError:
        return None
    try:
        counts = run_cmd("git", "-C", str(path), "rev-list", "--left-right",
                         "--count", f"{base}...HEAD")
    except HarnessError:
        return None
    behind, _, ahead = counts.partition("\t")
    return {"behind": int(behind or 0), "ahead": int(ahead or 0)}


def _common_git_dir(root: Path, common: str) -> Path:
    """Absolute path of git's --git-common-dir (relative output → repo root)."""
    p = Path(common)
    return (root / p).resolve() if not p.is_absolute() else p.resolve()


def worktree_branch(path: Path) -> str | None:
    """Branch of *path* only when it sits in a linked git worktree.

    A linked worktree's ``--git-common-dir`` points at the main repo's .git,
    not the checkout's own ``.git``. The main checkout (even on a feature
    branch) returns None — starting from it still creates a new branch.
    """
    root = repo_root(path)
    try:
        common = run_cmd("git", "-C", str(path), "rev-parse", "--git-common-dir")
    except HarnessError:
        return None
    if not common or _common_git_dir(root, common) == (root / ".git").resolve():
        return None
    return current_branch(path)


def main_repo_root(path: Path) -> Path:
    """Main checkout for *path*: linked worktrees resolve to their repo.

    A worktree's ``--git-common-dir`` is the main repo's ``.git``; its parent
    is the main checkout. Everything (repo registry, links, git-wt --repo)
    should address the main repo, never a worktree inside it.
    """
    root = repo_root(path)
    common = run_cmd("git", "-C", str(path), "rev-parse", "--git-common-dir")
    if common and _common_git_dir(root, common) != (root / ".git").resolve():
        return _common_git_dir(root, common).parent
    return root


def resolve_repo(explicit: str | None, cwd: Path, depth: int = 7) -> Path:
    """Resolve which offline repo to use.

    --repo accepts a registered name, a local path, or a clone URL.
    Without it: cwd repo if inside one, else interactive pick of registered
    repos (TTY) or an error (non-TTY).

    Note: ``start``/``review`` never reach this fallback — they resolve
    via ``trackers.resolve_for_tracker`` (linked-worktree → single linked
    repo), which never defaults to the CWD (issue #26).
    """
    registry = store.load_repos()
    if explicit:
        if explicit in registry:
            p = Path(registry[explicit]["path"]).expanduser()
            if not p.is_dir():
                raise HarnessError(f"registered repo '{explicit}' path missing: {p}")
            return p
        p = Path(explicit).expanduser()
        if p.is_dir():
            return p
        # Treat as clone URL
        return clone_url(explicit, depth)
    root = repo_root(cwd)
    if root is not None:
        return main_repo_root(root)
    import sys
    names = list(registry)
    if not names:
        raise HarnessError(
            "not in a git repo and no repos registered.\n"
            "  Register one: workagent repo add --name <name> --path <path>\n"
            "  Or pass: workagent start <issue> --repo <path|url|name>",
            exit_code=2,
        )
    if not sys.stdin.isatty():
        raise HarnessError(
            f"not in a git repo; pass --repo explicitly (registered: {', '.join(names)})",
            exit_code=2,
        )
    print("Select a repo:", file=sys.stderr)
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name} ({registry[name].get('path', '?')})", file=sys.stderr)
    try:
        choice = input("repo [number/name, or paste path/URL]: ").strip()
    except EOFError:
        raise HarnessError("no repo selected", exit_code=2)
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        return Path(registry[names[int(choice) - 1]]["path"]).expanduser()
    return resolve_repo(choice or None, cwd, depth)


def clone_url(url: str, depth: int = 7) -> Path:
    """Shallow-clone a repo URL next to other checkouts; remember it."""
    dest_base = Path.home() / "projects" / "harness-clones"
    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = dest_base / name
    if not dest.is_dir():
        dest_base.mkdir(parents=True, exist_ok=True)
        run_cmd("git", "clone", "--depth", str(int(depth)), url, str(dest))
    register_repo(name, dest)
    return dest

def register_repo(name: str, path: Path, tracker_key: str = "") -> None:
    """Register *name* → *path* (+ mandatory tracker derivation).

    Post-migration the SQLite repos row is the registry (``tracker_key``
    NOT NULL): an explicit key wins, else the origin remote is derived
    via ``trackers.default_tracker_for_repo``. Pre-migration both the
    config.json registry and the tracker mapping are updated (dual-write).
    """
    from . import trackers as _trackers
    tid = tracker_key or _trackers.default_tracker_for_repo(path)
    if not tid:
        raise HarnessError(
            "cannot derive tracker from origin remote: pass tracker_key (repo add --tracker)", exit_code=2)
    if store._sqlite_path() is not None:
        from . import store_sqlite as sq
        sq.register_repo_row(sq.db_path(), name, str(path), tid,
                             remote=remote_url(path), tool=_detect_host_cli(path))
        return
    cfg = store.load_config()
    repos = cfg.setdefault("repos", {})
    repos[name] = {"path": str(path), "remote": remote_url(path),
                   "tool": _detect_host_cli(path)}
    store.save_config(cfg)
    store.add_tracker_repo(tid, str(path))


def unregister_repo(name: str) -> bool:
    """Drop *name* from the registry; True when it existed."""
    if store._sqlite_path() is not None:
        from . import store_sqlite as sq
        return sq.remove_repo_row(sq.db_path(), name)
    cfg = store.load_config()
    if name not in cfg.get("repos", {}):
        return False
    del cfg["repos"][name]
    store.save_config(cfg)
    return True


def repo_names() -> list[str]:
    """Registered repo names (local state only; used for shell completion)."""
    return list(store.load_repos())

def _remote_host(url: str) -> str | None:
    """Hostname from an https/ssh git URL (https://h/p, git@h:p, ssh://h/p)."""
    m = (re.match(r"^[a-z][a-z0-9+.-]*://(?:[^/@]+@)?([^/:?#]+)", url, re.IGNORECASE)
         or re.match(r"^[^/@]+@([^/:]+):", url))
    return m.group(1).lower() if m else None


_GH_HOSTS_FILE = Path.home() / ".config" / "gh" / "hosts.yml"
_GLAB_HOSTS_FILE = Path.home() / ".config" / "glab-cli" / "config.yml"


def _known_gh_hosts(path: Path | None = None) -> set[str]:
    """Top-level host keys of gh's hosts.yml (hosts sit at column 0)."""
    try:
        return set(re.findall(r"^(\S+):\s*$",
                              (path or _GH_HOSTS_FILE).read_text(), re.MULTILINE))
    except OSError:
        return set()


def _known_glab_hosts(path: Path | None = None) -> set[str]:
    """Host keys under glab config.yml's ``hosts:`` section (4-space indent)."""
    try:
        lines = (path or _GLAB_HOSTS_FILE).read_text().splitlines()
    except OSError:
        return set()
    hosts, in_hosts = set(), False
    for line in lines:
        if re.match(r"^\S", line):
            in_hosts = line.rstrip().endswith("hosts:")
            continue
        if in_hosts:
            m = re.match(r"^ {4}([^\s#]+):\s*$", line)
            if m:
                hosts.add(m.group(1).lower())
    return hosts


_NO_TOOL_DIRS: set[str] = set()


def _detect_host_cli(path: Path) -> str | None:
    """Host CLI for *path*'s remote: gh only for GitHub hosts, glab otherwise.

    The origin host is matched against each CLI's known hosts (gh
    hosts.yml, glab config.yml); unknown hosts fall back to the legacy
    auth-status probe (gh first). Local file reads only — no CLI spawn
    unless the host is unknown. Probe misses (~12s: gh 1.7s + glab 10s)
    are memoized per directory for the process lifetime so repeated
    status/sync calls in one run pay it once.
    """
    if str(path) in _NO_TOOL_DIRS:
        return None
    host = None
    url = remote_url(path)
    if url:
        host = _remote_host(url)
    if host:
        if host in _known_gh_hosts():
            return "gh"
        if host in _known_glab_hosts():
            return "glab"
    for cli in ("gh", "glab"):
        try:
            proc = subprocess.run([cli, "auth", "status"], cwd=str(path),
                                  capture_output=True, timeout=15)
            if proc.returncode == 0:
                return cli
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    _NO_TOOL_DIRS.add(str(path))
    return None
