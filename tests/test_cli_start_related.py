"""`start --related-repo` fans out one child worktree per related repo."""
from __future__ import annotations

from workagent import cli, store

from tests.cli_helpers import runner, _start_mocks


def _start(isolated_config, tmp_path, monkeypatch, args):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir(exist_ok=True)
    other = tmp_path / "other"
    other.mkdir(exist_ok=True)
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.repos, "resolve_repo",
                        lambda explicit, cwd, depth=7: other
                        if str(explicit) == "other" else repo_dir)
    monkeypatch.setattr(cli.repos, "repo_name",
                        lambda p: __import__("pathlib").Path(str(p)).name)
    monkeypatch.setattr(cli.repos, "register_repo", lambda *a, **k: None)
    created = []

    def fake_start(repo, **kw):
        wt = tmp_path / f"wt-{__import__('pathlib').Path(str(repo)).name}"
        wt.mkdir(exist_ok=True)
        created.append((str(repo), kw.get("issue"), kw.get("slug")))
        return {"worktree_path": str(wt), "branch": "feat/IPG-1011--x"}

    monkeypatch.setattr(cli.gitwt, "start_worktree", fake_start)
    prompts = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    orig = cli.backend.prompt_for_issue

    def spy(title, body, ref, **kw):
        prompts.append(kw)
        return orig(title, body, ref, **kw)

    monkeypatch.setattr(cli.backend, "prompt_for_issue", spy)
    r = runner.invoke(cli.app, ["start", "IPG-1011", *args])
    assert r.exit_code == 0, r.output
    return created, prompts


def test_start_creates_child_per_related_repo(isolated_config, tmp_path, monkeypatch):
    created, prompts = _start(isolated_config, tmp_path, monkeypatch,
                              ["--launch", "--json", "--repo", str(tmp_path / "proj"),
                               "--related-repo", "other", "--related-repo", "other"])
    assert len(created) == 2  # parent + one deduped child
    links = store.load_links()
    parent = links["jira:IPG-1011@proj"]
    child = links["jira:IPG-1011@other"]
    assert child["parent_key_ref"] == "jira:IPG-1011@proj"
    assert child["branch"] == parent["branch"] == "feat/IPG-1011--x"
    assert parent["related_repos"] == [str(tmp_path / "other")]
    assert prompts and prompts[0].get("related_paths")


def test_fanout_skips_branch_ensure_for_new_branch(isolated_config, tmp_path, monkeypatch):
    """Fan-out must not require the fresh branch in the related repo.

    Regression: `start --related-repo` failed with
    "branch '...' not found locally or as origin/... (repo: <related>)"
    because the just-created main-repo branch was pre-checked against the
    sibling, where it cannot exist yet.
    """
    from tests.cli_helpers import _start_mocks
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir(exist_ok=True)
    other = tmp_path / "other"
    other.mkdir(exist_ok=True)
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.repos, "resolve_repo",
                        lambda explicit, cwd, depth=7: other
                        if str(explicit) == "other" else repo_dir)
    monkeypatch.setattr(cli.repos, "repo_name",
                        lambda p: __import__("pathlib").Path(str(p)).name)
    monkeypatch.setattr(cli.repos, "register_repo", lambda *a, **k: None)
    seen = []

    def fake_start(repo, **kw):
        seen.append((str(repo), dict(kw)))
        wt = tmp_path / f"wt-{__import__('pathlib').Path(str(repo)).name}"
        wt.mkdir(exist_ok=True)
        return {"worktree_path": str(wt), "branch": kw.get("branch", "")}

    monkeypatch.setattr(cli.gitwt, "start_worktree", fake_start)
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    r = runner.invoke(cli.app, ["start", "IPG-1011", "--launch", "--json",
                                "--repo", str(repo_dir), "--related-repo", "other"])
    assert r.exit_code == 0, r.output
    child_calls = [kw for repo, kw in seen if repo == str(other)]
    assert child_calls, "expected a child worktree call for the related repo"
    assert child_calls[0].get("ensure_branch") is False
    assert "branch" in child_calls[0]  # fan-out adopts the fresh parent branch


def test_start_rerun_adopts_children(isolated_config, tmp_path, monkeypatch):
    args = ["--launch", "--json", "--repo", str(tmp_path / "proj"),
            "--related-repo", "other"]
    _start(isolated_config, tmp_path, monkeypatch, args)
    before = store.load_links(include_inactive=True, include_deleted=True)
    _start(isolated_config, tmp_path, monkeypatch, args)
    after = store.load_links(include_inactive=True, include_deleted=True)
    assert set(after) == set(before)


def test_start_related_same_as_main_is_usage_error(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir(exist_ok=True)
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(tmp_path / "wt"),
                                            "branch": "feat/IPG-1011--x"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    r = runner.invoke(cli.app, ["start", "IPG-1011", "--launch", "--json",
                                "--repo", str(repo_dir),
                                "--related-repo", str(repo_dir)])
    assert r.exit_code == 2


def test_start_pr_mode_rejects_related_repo(isolated_config, tmp_path, monkeypatch):
    r = runner.invoke(cli.app, ["start", "https://github.com/o/r/pull/88",
                                "--no-tty", "--related-repo", "other"])
    assert r.exit_code == 2


def test_start_dry_run_plans_children_without_creating(isolated_config, tmp_path, monkeypatch):
    import json
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir(exist_ok=True)
    other = tmp_path / "other"
    other.mkdir(exist_ok=True)
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: (_ for _ in ()).throw(
                            AssertionError("dry-run must not create")))
    r = runner.invoke(cli.app, ["start", "IPG-1011", "--dry-run", "--json",
                                "--related-repo", str(other)])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["related_repos"] == [str(other)]
    assert store.load_links() == {}


def test_start_headless_suffix_conditional_on_changes(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir(exist_ok=True)
    worktree = tmp_path / "wt"
    worktree.mkdir(exist_ok=True)
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/IPG-1011--x"})
    prompts = []
    monkeypatch.setattr(cli.cli_start, "_run_harness",
                        lambda *a, **k: prompts.append(a[1]) or None)
    r = runner.invoke(cli.app, ["start", "IPG-1011", "--no-tty", "--json"])
    assert r.exit_code == 0, r.output
    assert prompts, "expected _run_harness to capture the prompt"
    assert "if there are code changes" in prompts[0]
    assert "open a PR/MR to the default branch" in prompts[0]
    assert "do not commit" in prompts[0]
    assert "create an MR/PR to the default branch" not in prompts[0]
