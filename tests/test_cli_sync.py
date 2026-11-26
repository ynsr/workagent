"""CLI sync tests (split from test_cli.py; engine tests live in test_sync.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _link_session, _sync_mocks, _init_repo, _add_wt

def test_sync_unknown_ref(isolated_config):
    r = _invoke("sync", "nope-1")
    assert r.exit_code == 2


def test_sync_rebase_strategy_with_open_pr(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                              repo=str(repo_dir))
    rebase, local, pulled = [], [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase,
                pulled=pulled)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out["strategy"] == "remote-rebase" and out["result"] == "rebased"
    assert out["pull"] == "reset"
    assert rebase == [("glab", 9, str(wt_dir))] and local == []
    assert pulled == [(str(wt_dir), "feat/IPG-929--x")]


def test_sync_explicit_rebase_flag_and_mutual_exclusion(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                              repo=str(repo_dir))
    rebase, local, pulled = [], [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase,
                pulled=pulled)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    r = _invoke("sync", "IPG-929", "--rebase", "--yes", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["strategy"] == "remote-rebase"
    r_bad = _invoke("sync", "IPG-929", "--rebase", "--merge", "--yes")
    assert r_bad.exit_code == 2


def test_sync_merge_flag_uses_local_merge(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                              repo=str(repo_dir))
    rebase, local = [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase)
    r = _invoke("sync", "IPG-929", "--merge", "--yes", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-merge" and out["result"] == "merged"
    assert local == [(str(wt_dir), "main")] and rebase == []


def test_sync_no_pr_falls_back_to_local_rebase_harness(isolated_config, tmp_path, monkeypatch):
    """Issue #223: without a PR, Rebase mode skips local_merge and invokes the AI harness."""
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    local = []
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda *a, **k: local.append(a))
    prompts = []
    monkeypatch.setattr(cli, "_run_harness",
                        lambda name, prompt, *a, **k: prompts.append((name, prompt)))
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-rebase"
    assert out["result"] == "rebase-harness"
    assert local == []
    assert len(prompts) == 1
    assert "Rebase branch feat/IPG-929--x onto origin/main" in prompts[0][1]


def test_sync_recorded_pr_seed_uses_local_rebase_harness(isolated_config, tmp_path,
                                                         monkeypatch):
    """A recorded pr_url seed (state unknown) is display-only: Rebase sync must
    skip remote-rebase and local_merge, invoking the AI rebase harness instead."""
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    links = store.load_links()
    links["jira:IPG-929"]["pr_url"] = \
        "https://git.jibit.cloud/server/projectx/-/merge_requests/1699"
    store.save_links(links)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    rebase, local, prompts = [], [], []
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda *a, **k: local.append(a))
    monkeypatch.setattr(cli.sync_mod, "rebase_remote",
                        lambda *a, **k: rebase.append(a))
    monkeypatch.setattr(cli, "_run_harness",
                        lambda name, prompt, *a, **k: prompts.append(prompt))
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-rebase"
    assert out["result"] == "rebase-harness"
    assert local == [] and rebase == [] and len(prompts) == 1


def test_sync_dry_run_touches_nothing(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                              repo=str(repo_dir))
    rebase, local = [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase)
    r = _invoke("sync", "IPG-929", "--dry-run", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out["strategy"] == "remote-rebase" and "would" in out["result"]
    assert rebase == [] and local == []


def test_sync_dirty_worktree_aborts(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    (wt_dir / "dirty.txt").write_text("x\n")
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.sync_mod, "dirty_files", lambda p: ["dirty.txt"])
    r = _invoke("sync", "IPG-929", "--yes")
    assert r.exit_code == 1
    assert "dirty.txt" in r.output

def test_sync_dirty_abort_names_merge_in_progress(
        isolated_config, tmp_path, monkeypatch):
    """A leftover conflicted merge must say so: the dirty abort names the
    in-progress merge and the commit+push step (was: bare file list)."""
    from workagent import sync as sync_mod
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    (wt_dir / "dirty.txt").write_text("x\n")
    _link_session(repo_dir, wt_dir, monkeypatch)
    monkeypatch.setattr(cli.sync_mod, "dirty_files", lambda p: ["dirty.txt"])
    monkeypatch.setattr(sync_mod, "merge_in_progress", lambda p: True)
    r = _invoke("sync", "IPG-929", "--yes")
    assert r.exit_code == 1
    assert "merge is in progress" in r.output



def test_sync_all_missing_worktree_skipped(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    _link_session(repo_dir, tmp_path / "missing", monkeypatch)
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: None)
    r = _invoke("sync", "--all", "--yes", "--json")
    assert r.exit_code == 0
    out = json.loads(r.stdout)
    assert out[0]["result"] == "missing-worktree"


def test_sync_rebase_failure_falls_back_to_local_rebase_harness(
        isolated_config, tmp_path, monkeypatch):
    """Issue #223: when remote rebase fails, Rebase mode skips local_merge
    and invokes the AI harness to rebase."""
    from workagent.errors import HarnessError
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                              repo=str(repo_dir))
    rebase, local, prompts = [], [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase)
    monkeypatch.setattr(cli.sync_mod, "rebase_remote",
                        lambda tool, pr, wt: (_ for _ in ()).throw(
                            HarnessError("glab mr rebase failed: conflict")))
    monkeypatch.setattr(cli, "_run_harness",
                        lambda name, prompt, *a, **k: prompts.append(prompt))
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-rebase" and out["fallback"] is True
    assert out["result"] == "rebase-harness"
    assert local == [] and rebase == [] and len(prompts) == 1
    assert "Rebase branch feat/IPG-929--x onto origin/main" in prompts[0]


# ── register: register an existing worktree by path ────────────────────


def test_sync_creates_worktree_for_new_pr_ref(isolated_config, tmp_path, monkeypatch):
    """sync <PR-url> with no linked state creates the branch worktree then syncs."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/99"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/99"})
    synced = []
    monkeypatch.setattr(cli, "_sync_one",
                        lambda k, entry, **kw: synced.append(k) or {"key": k, "result": "ok"})
    url = "https://github.com/o/r/pull/99"
    r = runner.invoke(cli.app, ["sync", url, "--json"])
    assert r.exit_code == 0, r.output
    assert synced == ["feat/99@proj"]
    links = store.load_links()
    assert links["feat/99@proj"]["pr_url"] == url


def test_sync_pr_ref_dry_run_creates_nothing(isolated_config, tmp_path, monkeypatch):
    """sync <PR-url> --dry-run prints the plan without creating a worktree."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/100"})

    def _no_start(repo, **kw):
        raise AssertionError("dry-run must not create a worktree")
    monkeypatch.setattr(cli.gitwt, "start_worktree", _no_start)
    url = "https://github.com/o/r/pull/100"
    r = runner.invoke(cli.app, ["sync", url, "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert store.load_links() == {}


def test_sync_missing_link_errors(isolated_config):
    r = _invoke("sync", "o/r#99", "--json")
    assert r.exit_code == 2
    assert "no linked state" in r.output

def test_sync_merged_pr_deleted_branch_points_at_cleanup(
        isolated_config, tmp_path, monkeypatch):
    """Merged MR + gone remote branch: sync fails with a cleanup hint."""
    from workagent import cli
    from workagent.errors import run_cmd as _real_run_cmd

    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "merged",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                          repo=str(repo_dir))
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)

    def _no_ref(*args, **kwargs):
        if "ls-remote" in args:
            return None  # empty output: no remote ref
        return _real_run_cmd(*args, **kwargs)
    monkeypatch.setattr("workagent.errors.run_cmd", _no_ref)
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 1
    assert "no longer exists" in r.output
    assert "workagent cleanup" in r.output


def test_sync_open_pr_missing_ref_still_syncs(
        isolated_config, tmp_path, monkeypatch):
    """Open PR: the gate stays silent even when ls-remote is empty."""
    import subprocess

    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                              repo=str(repo_dir))
    rebase, local, pulled = [], [], []
    _sync_mocks(monkeypatch, local_calls=local, rebase_calls=rebase,
                pulled=pulled)
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    r = _invoke("sync", "IPG-929", "--yes", "--json")
    assert r.exit_code == 0


def test_sync_harness_option_selects_harness(isolated_config, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    _link_session(tmp_path / "proj", wt, monkeypatch)
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda *a: "up-to-date")
    monkeypatch.setattr(cli.sync_mod, "local_merge", lambda *a, **k: {"status": "conflict", "conflicts": ["a.txt"]})
    monkeypatch.setattr(cli.sync_mod, "auto_resolve_changelog", lambda *a: False)
    seen = []
    monkeypatch.setattr(cli, "_run_harness", lambda name, *a, **k: seen.append(name))
    r = _invoke("sync", "IPG-929", "--merge", "--harness", "omp")
    assert r.exit_code == 0, r.output
    assert seen == ["omp"]


def test_sync_unknown_harness(isolated_config, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    _link_session(tmp_path / "proj", wt, monkeypatch)
    r = _invoke("sync", "IPG-929", "--harness", "unknown-harness")
    assert r.exit_code == 2


def test_sync_rebase_fallback_conflict_prompt_requests_rebase(
        isolated_config, tmp_path, monkeypatch):
    """When Sync method is Rebase (default) and remote rebase fails,
    it skips local_merge and asks the harness to rebase."""
    from workagent.errors import HarnessError
    repo_dir = tmp_path / "proj"
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    _link_session(repo_dir, wt, monkeypatch)
    store.cache_pr_status("feat/IPG-929--x", {"number": 9, "state": "open",
                                              "title": "T", "author": "a",
                                              "created_at": "2026-09-15",
                                              "url": "https://x/mr/9",
                                              "target_branch": "main"},
                              repo=str(repo_dir))
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "ahead_behind", lambda wt, db, branch="": None)
    monkeypatch.setattr(cli.sync_mod, "rebase_remote",
                        lambda *a, **k: (_ for _ in ()).throw(
                            HarnessError("glab mr rebase failed: conflict")))
    local = []
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda *a, **k: local.append(a))
    prompts = []
    monkeypatch.setattr(cli, "_run_harness",
                        lambda name, prompt, *a, **k: prompts.append(prompt))
    r = _invoke("sync", "IPG-929", "--harness", "omp")
    assert r.exit_code == 0, r.output
    assert local == []
    assert len(prompts) == 1
    assert "Rebase branch feat/IPG-929--x onto origin/main" in prompts[0]
    assert "git merge --abort" not in prompts[0]
    assert "git push --force-with-lease origin feat/IPG-929--x" in prompts[0]
    assert "complete the merge" not in prompts[0]


@pytest.mark.parametrize("extra_args", [[], ["--rebase"]], ids=["default", "explicit-rebase"])
def test_sync_no_pr_default_rebase_conflict_prompt_requests_rebase(
        isolated_config, tmp_path, monkeypatch, extra_args):
    """Without a PR, default Sync method (Rebase) skips local_merge and asks the harness to rebase."""
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    _link_session(tmp_path / "proj", wt, monkeypatch)
    local = []
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda *a, **k: local.append(a))
    prompts = []
    monkeypatch.setattr(cli, "_run_harness",
                        lambda name, prompt, *a, **k: prompts.append(prompt))
    r = _invoke("sync", "IPG-929", *extra_args, "--harness", "omp")
    assert r.exit_code == 0, r.output
    assert local == []
    assert len(prompts) == 1
    assert "Rebase branch feat/IPG-929--x onto origin/main" in prompts[0]
    assert "complete the merge" not in prompts[0]


def test_sync_no_pr_rebase_dry_run(isolated_config, tmp_path, monkeypatch):
    """Rebase mode --dry-run without a PR reports would-rebase without calling local_merge."""
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    _link_session(tmp_path / "proj", wt, monkeypatch)
    local = []
    monkeypatch.setattr(cli.sync_mod, "local_merge", lambda *a, **k: local.append(a))
    r = _invoke("sync", "IPG-929", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-rebase" and out["result"] == "would-rebase"
    assert local == []


def test_sync_no_pr_rebase_non_tty_needs_harness(isolated_config, tmp_path, monkeypatch):
    """Rebase mode in non-TTY without --yes/--harness reports needs-harness and fails."""
    from workagent.cli_sync import result_failed
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    _link_session(tmp_path / "proj", wt, monkeypatch)
    r = _invoke("sync", "IPG-929", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["strategy"] == "local-rebase"
    assert out["result"] == "needs-harness"
    assert result_failed(out) is True


def test_sync_merge_flag_conflict_prompt_requests_merge(
        isolated_config, tmp_path, monkeypatch):
    """When --merge is explicitly selected, conflict prompt asks to complete the merge."""
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    _link_session(tmp_path / "proj", wt, monkeypatch)
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda *a: "up-to-date")
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda *a, **k: {"status": "conflict", "conflicts": ["a.txt"]})
    monkeypatch.setattr(cli.sync_mod, "auto_resolve_changelog", lambda *a: False)
    prompts = []
    monkeypatch.setattr(cli, "_run_harness",
                        lambda name, prompt, *a, **k: prompts.append(prompt))
    r = _invoke("sync", "IPG-929", "--merge", "--harness", "omp")
    assert r.exit_code == 0, r.output
    assert len(prompts) == 1
    assert "complete the merge" in prompts[0]
    assert "Perform a rebase instead of a merge" not in prompts[0]


