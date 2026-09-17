"""harness CLI — launch AI agent harnesses in git-wt worktrees."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, backend, gitwt, refs, repos, store
from .errors import HarnessError


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Launch AI agent harnesses in git-wt worktrees from issue/PR links.",
        epilog=(
            "Examples:\n"
            "  harness start https://github.com/OWNER/REPO/issues/22\n"
            "  harness start OWNER/REPO#22 --repo my-checkout\n"
            "  harness review https://github.com/OWNER/REPO/pull/33\n"
            "  harness cleanup OWNER/REPO#22 --force --yes\n"
            "  harness repo add --name projectx --path ~/projects/projectx\n"
            "  harness status\n\n"
            "Exit codes: 0=success, 1=error, 2=needs human input"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", default=False)
    parser.add_argument("-q", "--quiet", action="store_true", default=False)
    parser.add_argument("--json", action="store_true", default=False,
                        help="Output as JSON (stdout; logs go to stderr)")

    sub = parser.add_subparsers(dest="command", required=False)

    sp = sub.add_parser("start", help="Create worktree from issue and launch harness")
    sp.add_argument("ref", help="Issue key/URL, OWNER/REPO#NUM, or bare number")
    sp.add_argument("--repo", default=None, help="Registered name, local path, or clone URL")
    sp.add_argument("--depth", type=int, default=7, help="Clone depth for repo URLs (default: 7)")
    sp.add_argument("--base", default=None, help="Base branch (default: repo default)")
    sp.add_argument("--harness", default=None, help="Harness to run (default: configured; v1: omp)")
    sp.add_argument("--no-tty", action="store_true",
                    help="Run harness non-interactively (auto commit/push/MR prompt suffix)")
    sp.add_argument("--dry-run", action="store_true", help="Print plan without acting")
    sp.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    # NOTE: extra harness args are collected in main() by splitting argv on
    # the first bare `--` (argparse.REMAINDER would swallow --repo etc.).
    _stderr_flags(sp)

    sp = sub.add_parser("review", help="Create worktree from PR/MR and launch review")
    sp.add_argument("ref", help="PR/MR URL or OWNER/REPO#NUM")
    sp.add_argument("--repo", default=None, help="Registered name, local path, or clone URL")
    sp.add_argument("--depth", type=int, default=7, help="Clone depth for repo URLs (default: 7)")
    sp.add_argument("--harness", default=None, help="Harness to run (default: configured; v1: omp)")
    sp.add_argument("--no-tty", action="store_true", help="Run harness non-interactively")
    sp.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    sp = sub.add_parser("cleanup", help="Close issue + remove worktree/branch/PR")
    sp.add_argument("ref", help="Issue ID/URL or PR/MR URL")
    sp.add_argument("--force", action="store_true", help="Skip state validation")
    sp.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    sp.add_argument("--dry-run", action="store_true", help="Print plan without acting")
    _stderr_flags(sp)

    sp = sub.add_parser("repo", help="Manage registered offline repos")
    repo_sub = sp.add_subparsers(dest="repo_command", required=True)
    a = repo_sub.add_parser("add", help="Register an offline repo")
    a.add_argument("--name", required=True)
    a.add_argument("--path", required=True, type=Path)
    a.add_argument("--tracker", default=None, help="Issue tracker project key/URL to map")
    _stderr_flags(a)
    li = repo_sub.add_parser("list", help="List registered repos")
    _stderr_flags(li)
    rm = repo_sub.add_parser("remove", help="Unregister a repo")
    rm.add_argument("name")
    _stderr_flags(rm)

    sp = sub.add_parser("status", help="Show linked issue↔PR↔worktree state")
    sp.add_argument("ref", nargs="?", default=None, help="Issue/PR ref (omit: all links)")
    _stderr_flags(sp)

    sp = sub.add_parser("doctor", help="Check install sync + tool availability")
    _stderr_flags(sp)

    sp = sub.add_parser("completion", help="Print a shell completion script")
    sp.add_argument("shell", choices=("bash", "zsh", "fish"))

    sp = sub.add_parser("completion-install", help="Install completion into rc file")
    sp.add_argument("shell", nargs="?", default=None)
    sp.add_argument("--rcfile", type=Path, default=None)
    sp.add_argument("--yes", action="store_true")
    _stderr_flags(sp)

    argv = sys.argv[1:]
    harness_args: list[str] = []
    if "--" in argv and argv.index("--") > 0:
        cut = argv.index("--")
        harness_args = argv[cut + 1:]
        argv = argv[:cut]
    args = parser.parse_args(argv)
    args.harness_args = harness_args
    from . import doctor as _doctor
    warn = _doctor.dev_warning()
    if warn:
        eprint(warn)

    if not args.command:
        parser.print_help()
        sys.exit(0)
    try:
        result = _dispatch(args)
    except HarnessError as e:
        eprint(f"error: {e}")
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        eprint("interrupted")
        sys.exit(130)
    if result is not None:
        _output(args, result)


def _stderr_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS)


def _dispatch(args: argparse.Namespace):
    if args.command == "start":
        return _cmd_start(args)
    if args.command == "review":
        return _cmd_review(args)
    if args.command == "cleanup":
        return _cmd_cleanup(args)
    if args.command == "repo":
        return _cmd_repo(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "doctor":
        from . import doctor as _doctor
        return _doctor.check(args)
    if args.command == "completion":
        from . import completions as _c
        sys.stdout.write(_c.script(args.shell))
        return None
    if args.command == "completion-install":
        from . import completions as _c
        return _c.install(args.shell, args.rcfile, args.yes)
    raise HarnessError(f"unknown command: {args.command}", exit_code=2)


# ── start ─────────────────────────────────────────────────────────────

def _cmd_start(args: argparse.Namespace) -> dict | None:
    parsed = refs.parse_ref(args.ref)
    if parsed["kind"] in ("pr", "mr"):
        raise HarnessError(f"{args.ref} looks like a PR/MR — use `harness review`", exit_code=2)
    repo = repos.resolve_repo(args.repo, Path.cwd())
    base = args.base or repos.default_branch(repo)
    cur = repos.current_branch(repo) if repos.repo_root(Path.cwd()) == repo else None
    if cur and cur != base and not args.yes and not args.dry_run and sys.stdin.isatty():
        eprint(f"note: repo is on '{cur}', worktree will branch from '{base}'.")

    issue = refs.fetch_issue(parsed)
    key = refs.issue_key(parsed)
    harness_name = args.harness or store.load_config().get("default_harness", "omp")

    # Jira: pass a full browse URL to git-wt --link (it detects the tracker
    # from the URL path); jira-cli itself takes the bare key.
    if parsed["tool"] == "jira-cli" and not parsed["url"].startswith("http"):
        link_url = f"https://tribe.jibit.cloud/browse/{parsed['number']}"
    else:
        link_url = parsed["url"] if parsed["tool"] == "jira-cli" or parsed["repo"] else None

    issue_id, slug = gitwt.build_branch_for_issue(parsed, issue["title"])
    if parsed["tool"] == "jira-cli" and not issue_id:
        issue_id = parsed["number"]

    if args.dry_run:
        return {"dry_run": True, "repo": str(repo), "base": base,
                "issue": {"title": issue["title"]}, "harness": harness_name,
                "key": key, "link": link_url}
    wt = gitwt.start_worktree(repo, issue=issue_id, slug=slug, link=link_url, base=base)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", "")
    store.record_link(key, {"issue": args.ref, "worktree": worktree,
                            "branch": branch, "repo": str(repo)})
    # Remember the repo under a derived name for future --repo picks.
    try:
        repos.register_repo(repos.repo_name(repo), repo)
    except HarnessError:
        pass

    prompt = backend.prompt_for_issue(issue["title"], issue["body"], args.ref)
    if args.no_tty:
        prompt += "\n\nAfter task done, commit, push and create an MR/PR to the default branch"
    eprint(f"worktree: {worktree}  branch: {branch}")
    extra = [a for a in (getattr(args, "harness_args", None) or []) if a != "--"]
    backend.launch(harness_name, prompt, worktree or str(repo), args.no_tty, extra)
    return {"worktree_path": worktree, "branch": branch, "base": base,
            "key": key, "harness": harness_name}


# ── review ────────────────────────────────────────────────────────────

def _cmd_review(args: argparse.Namespace) -> dict | None:
    parsed = refs.parse_ref(args.ref)
    if parsed["kind"] == "issue":
        # Could still be a PR number via shorthand; try PR info, fall back to issue.
        pass
    repo = repos.resolve_repo(args.repo, Path.cwd())
    base = repos.default_branch(repo)
    harness_name = args.harness or store.load_config().get("default_harness", "omp")

    pr_url = parsed["url"] if parsed["repo"] else args.ref
    info: dict = {}
    if parsed["repo"] and parsed["kind"] in ("pr", "mr"):
        try:
            info = refs.fetch_pr_info(parsed)
        except HarnessError as e:
            eprint(f"warning: {e}")
    head_ref = info.get("head_ref", "")

    if args.dry_run:
        return {"dry_run": True, "repo": str(repo), "pr_url": pr_url,
                "harness": harness_name, "head_ref": head_ref}

    if head_ref:
        wt = gitwt.start_worktree(repo, branch=head_ref, base=base)
    else:
        # No head ref known: create a review worktree off the base branch.
        wt = gitwt.start_worktree(repo, branch=None, issue=None, slug="review", base=base)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", head_ref)
    store.record_link(f"pr:{pr_url}", {"pr_url": pr_url, "worktree": worktree,
                                       "branch": branch, "repo": str(repo)})
    try:
        repos.register_repo(repos.repo_name(repo), repo)
    except HarnessError:
        pass

    prompt = backend.prompt_for_review(pr_url)
    eprint(f"worktree: {worktree}  branch: {branch}")
    extra = [a for a in (getattr(args, "harness_args", None) or []) if a != "--"]
    backend.launch(harness_name, prompt, worktree or str(repo), args.no_tty, extra)
    return {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
            "harness": harness_name}


# ── cleanup ───────────────────────────────────────────────────────────

def _cmd_cleanup(args: argparse.Namespace) -> dict:
    parsed = refs.parse_ref(args.ref)
    key = refs.issue_key(parsed)
    links = store.load_links()
    entry = links.get(key) or links.get(f"pr:{parsed['url']}" if parsed["repo"] else key)
    if entry is None:
        raise HarnessError(
            f"no linked state for {args.ref}.\n"
            "  Pass the worktree repo explicitly or run start/review first.",
            exit_code=2,
        )
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    pr_url = entry.get("pr_url", "")
    if not branch:
        raise HarnessError(f"linked entry for {args.ref} has no branch", exit_code=2)

    if args.dry_run:
        return {"dry_run": True, "repo": str(repo), "branch": branch,
                "pr_url": pr_url, "force": args.force}

    if not args.yes and not args.force and sys.stdin.isatty():
        try:
            answer = input(f"Remove worktree + delete branch '{branch}'? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            raise HarnessError("aborted", exit_code=2)

    result = gitwt.cleanup_worktree(repo, branch, delete_branch=True,
                                    force=args.force, yes=True)
    _close_issue(parsed, args.force)
    _close_pr(parsed, pr_url, args.force)

    remaining = {k: v for k, v in store.load_links().items()
                 if k != key and k != f"pr:{parsed['url']}"}
    store.save_links(remaining)
    return {"branch": branch, "cleanup": result, "closed": args.ref}


def _close_issue(parsed: dict, force: bool) -> None:
    if parsed["tool"] == "jira-cli":
        # Best effort: comment the resolution; transitions vary per project,
        # so leave status change to the user unless --force.
        try:
            from .errors import run_cmd as _run
            _run("jira-cli", "issue", parsed["number"], "add-comment",
                 "--body", "Resolved via harness cleanup.")
            eprint(f"commented on {parsed['number']} (status left unchanged)")
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
            if not force:
                raise
            eprint(f"warning: {e}")
    elif parsed["tool"] == "glab":
        eprint("note: glab issue close not automated in v1; close it in the UI.")


def _close_pr(parsed: dict, pr_url: str, force: bool) -> None:
    url = pr_url or (parsed["url"] if parsed["kind"] in ("pr", "mr") else "")
    if not url:
        return
    try:
        from .errors import run_cmd as _run
        if "github.com" in url:
            _run("gh", "pr", "close", url)
        else:
            _run("glab", "mr", "close", url)
        eprint(f"closed {url}")
    except HarnessError as e:
        if not force:
            raise
        eprint(f"warning: {e}")


# ── repo / status ─────────────────────────────────────────────────────

def _cmd_repo(args: argparse.Namespace):
    if args.repo_command == "add":
        path = args.path.expanduser()
        if not (path / ".git").exists() and not path.is_dir():
            raise HarnessError(f"not a repo path: {path}", exit_code=2)
        repos.register_repo(args.name, path)
        if args.tracker:
            cfg = store.load_config()
            cfg.setdefault("trackers", {})[args.tracker] = {"repos": [args.name]}
            store.save_config(cfg)
        return {"registered": args.name, "path": str(path)}
    if args.repo_command == "list":
        cfg = store.load_config()
        items = [{"name": n, **v} for n, v in cfg.get("repos", {}).items()]
        if getattr(args, "json", False):
            return items
        return "\n".join(f"{i['name']}: {i.get('path', '?')}" for i in items) or "(no repos registered)"
    if args.repo_command == "remove":
        cfg = store.load_config()
        if args.name not in cfg.get("repos", {}):
            raise HarnessError(f"unknown repo: {args.name}", exit_code=2)
        del cfg["repos"][args.name]
        store.save_config(cfg)
        return {"removed": args.name}
    raise HarnessError(f"unknown repo subcommand: {args.repo_command}", exit_code=2)


def _cmd_status(args: argparse.Namespace):
    links = store.load_links()
    if args.ref:
        parsed = refs.parse_ref(args.ref)
        key = refs.issue_key(parsed)
        entry = links.get(key) or links.get(f"pr:{parsed['url']}", {})
        if getattr(args, "json", False):
            return {"key": key, **entry}
        if not entry:
            return f"no linked state for {args.ref}"
        return "\n".join(f"{k}: {v}" for k, v in entry.items())
    if getattr(args, "json", False):
        return links
    if not links:
        return "(no linked sessions)"
    return "\n".join(f"{k}: {v.get('worktree', '?')} [{v.get('branch', '?')}]" for k, v in links.items())


# ── output ────────────────────────────────────────────────────────────

def _output(args: argparse.Namespace, data) -> None:
    if getattr(args, "json", False):
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif isinstance(data, dict):
        for k, v in data.items():
            print(f"{k}: {v}")
    elif isinstance(data, list):
        for row in data:
            print(row if isinstance(row, str) else json.dumps(row))
    else:
        print(data)


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


if __name__ == "__main__":
    main()
