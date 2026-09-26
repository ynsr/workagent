"""Jira refs, resume fallback, and upstream guard."""

import json
import os
import subprocess

from workagent import cli, gitwt, refs
from workagent.errors import HarnessError


def _repo_with_branch(tmp_path, name: str):
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "i"],
                   cwd=str(repo), check=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        **os.environ})
    subprocess.run(["git", "branch", name], cwd=str(repo), check=True)
    return repo


def test_jira_key_and_url_parse():
    for ref in ("IPG-980", "https://tribe.jibit.cloud/browse/IPG-980"):
        p = refs.parse_ref(ref)
        assert p == {"kind": "issue", "tool": "jira-cli", "repo": "",
                     "number": "IPG-980", "url": ref}
    assert refs.issue_key(refs.parse_ref("IPG-980")) == "jira:IPG-980"


def test_fetch_jira_issue(monkeypatch):
    import json as _json

    def fake_run(*args, **kw):
        assert args[:2] == ("jira-cli", "issue")
        return _json.dumps({"summary": "Do things", "description": "Body text"})
    monkeypatch.setattr(refs, "run_cmd", fake_run)
    out = refs.fetch_issue(refs.parse_ref("IPG-980"))
    assert out == {"title": "Do things", "body": "Body text"}


def test_existing_branch_resumes(monkeypatch, tmp_path):
    calls = []

    def fake_run(args_list, repo):
        calls.append(args_list)
        if "--resume" in args_list:
            return {"worktree_path": "/wt", "branch": "feat/1--x", "action": "resumed"}
        raise HarnessError("error: branch 'feat/1--x' already exists locally — use --resume")
    monkeypatch.setattr(gitwt, "_run_start", fake_run)
    monkeypatch.setattr(gitwt, "_ensure_upstream", lambda result, base: None)
    out = gitwt.start_worktree(_repo_with_branch(tmp_path, "feat/1--x"),
                               branch="feat/1--x", base="main")
    assert out["resumed"] is True and out["branch"] == "feat/1--x"
    assert any("--resume" in c for c in calls)


def test_unrelated_error_still_raises(monkeypatch, tmp_path):
    def fake_run(args_list, repo):
        raise HarnessError("error: something entirely different broke")
    monkeypatch.setattr(gitwt, "_run_start", fake_run)
    try:
        gitwt.start_worktree(_repo_with_branch(tmp_path, "feat/1--x"),
                             branch="feat/1--x", base="main")
    except HarnessError:
        return
    raise AssertionError("expected HarnessError")


def test_upstream_repair_without_network(tmp_path):
    import subprocess
    repo = tmp_path / "r"
    wt = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "i"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "feat/x",
                    str(wt), "main"], check=True, capture_output=True)
    # Simulate stale upstream pointing at the base branch (low-level config,
    # no remote needed).
    subprocess.run(["git", "-C", str(wt), "update-ref", "refs/remotes/origin/main", "HEAD"],
                   check=True)
    subprocess.run(["git", "-C", str(wt), "config", "branch.feat/x.remote", "origin"],
                   check=True)
    subprocess.run(["git", "-C", str(wt), "config", "branch.feat/x.merge", "refs/heads/main"],
                   check=True)
    result = {"worktree_path": str(wt), "branch": "feat/x"}
    gitwt._ensure_upstream(result, "main")
    assert result.get("upstream_fixed") is True
    assert gitwt._read_upstream(str(wt), "feat/x") == "origin/feat/x"


def test_start_dry_run_jira(isolated_config, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add changelog", "body": ""})
    r = CliRunner().invoke(cli.app, ["start", "IPG-980", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["key"] == "jira:IPG-980"
    assert out["dry_run"] is True


def test_fetch_base_runs_before_create(monkeypatch, tmp_path):
    """start fetches the base branch before creating the worktree."""
    calls: list[list[str]] = []

    def fake_run_cmd(*args, **kwargs):
        calls.append(list(args))
        if args[:3] == ("git", "fetch", "origin"):
            return ""
        if args[:2] == ("git", "rev-parse"):
            return "abc"
        return ""
    monkeypatch.setattr(gitwt, "require_git_wt", lambda: "git-wt")
    monkeypatch.setattr(gitwt, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(gitwt, "_run_start",
                        lambda args, repo: {"worktree_path": "/wt", "branch": "feat/1--x"})
    monkeypatch.setattr(gitwt, "_ensure_upstream", lambda result, base: None)
    gitwt.start_worktree(_repo_with_branch(tmp_path, "feat/1--x"),
                         issue="1", slug="x", base="main")
    fetch = [c for c in calls if c[:3] == ["git", "fetch", "origin"]]
    assert fetch and fetch[0][:4] == ["git", "fetch", "origin", "main"]


def test_fetch_base_failure_still_creates(monkeypatch, tmp_path):
    """Offline fetch failure warns but does not block the create."""
    from workagent.errors import HarnessError
    notes: list[str] = []

    def fake_run_cmd(*args, **kwargs):
        if args[:3] == ("git", "fetch", "origin"):
            raise HarnessError("offline")
        return ""
    monkeypatch.setattr(gitwt, "require_git_wt", lambda: "git-wt")
    monkeypatch.setattr(gitwt, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(gitwt, "_run_start",
                        lambda args, repo: {"worktree_path": "/wt", "branch": "feat/1--x"})
    monkeypatch.setattr(gitwt, "_ensure_upstream", lambda result, base: None)
    monkeypatch.setattr("workagent.cli_core.eprint", lambda m: notes.append(m))
    out = gitwt.start_worktree(_repo_with_branch(tmp_path, "feat/1--x"),
                               issue="1", slug="x", base="main")
    assert out["branch"] == "feat/1--x"
    assert any("main" in n for n in notes)
