"""Jira refs, resume fallback, and upstream guard."""

import json

from harness import cli, gitwt, refs
from harness.errors import HarnessError


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
    out = gitwt.start_worktree(tmp_path, branch="feat/1--x", base="main")
    assert out["resumed"] is True and out["branch"] == "feat/1--x"
    assert any("--resume" in c for c in calls)


def test_unrelated_error_still_raises(monkeypatch, tmp_path):
    def fake_run(args_list, repo):
        raise HarnessError("error: something entirely different broke")
    monkeypatch.setattr(gitwt, "_run_start", fake_run)
    try:
        gitwt.start_worktree(tmp_path, branch="feat/1--x", base="main")
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
    monkeypatch.setattr(cli.repos, "resolve_repo", lambda explicit, cwd, depth=7: repo_dir)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd: None)
    monkeypatch.setattr(cli.refs, "fetch_issue",
                        lambda parsed: {"title": "Add changelog", "body": ""})
    r = CliRunner().invoke(cli.app, ["start", "IPG-980", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["key"] == "jira:IPG-980"
    assert out["dry_run"] is True
