"""CLI cleanup tests (split from test_cli.py)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _sync_mocks

def test_cleanup_missing_link_errors(isolated_config):
    r = _invoke("cleanup", "o/r#99", "--json")
    assert r.exit_code == 2
    assert "no linked state" in r.output


def test_cleanup_dry_run(isolated_config):
    store.record_link("github:o/r#22", {"branch": "feat/22-x", "repo": "/tmp/proj",
                                        "worktree": "/tmp/wt"})
    r = _invoke("cleanup", "o/r#22", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["dry_run"] is True and out["branch"] == "feat/22-x"


def test_cleanup_resolves_by_branch(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-929", {"branch": "feat/IPG-929--x", "repo": "/tmp/proj",
                                       "worktree": "/tmp/wt"})
    r = runner.invoke(cli.app, ["cleanup", "feat/IPG-929--x", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["branch"] == "feat/IPG-929--x"
    # Wording gate: resolve/pick errors say "worktree", never "session".
    store.record_link("pr:https://git.example.com/x/-/merge_requests/929",
                      {"branch": "feat/IPG-929--pr", "repo": "/tmp/proj",
                       "worktree": "/tmp/wt-pr"})
    r2 = runner.invoke(cli.app, ["cleanup", "IPG-929", "--dry-run", "--json"])
    assert r2.exit_code == 2
    assert "exact worktree key" in r2.output
    assert "session" not in r2.output
    r3 = _invoke("cleanup", "NOPE-404")
    assert r3.exit_code == 2
    assert "linked worktrees" in r3.output


def test_cleanup_fuzzy_branch_match(isolated_config, monkeypatch):
    store.record_link("jira:IPG-981", {"issue": "IPG-981", "worktree": "/tmp/wt",
                                       "branch": "feat/IPG-981--x", "repo": "/tmp/proj"})
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    r = _invoke("cleanup", "IPG-981", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["key"] == "jira:IPG-981"


def test_cleanup_merged_loop(isolated_config, monkeypatch):
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": "/tmp/wt-1",
                                     "branch": "feat/1", "repo": "/tmp/proj"})
    store.record_link("jira:IPG-2", {"issue": "IPG-2", "worktree": "/tmp/wt-2",
                                     "branch": "feat/2", "repo": "/tmp/proj"})
    monkeypatch.setattr(
        cli, "_status_cells",
        lambda entry, refresh_pr=False: {"pr_data": {"state":
            "MERGED" if entry.get("branch") == "feat/1" else "OPEN"}})
    cleaned = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda repo, branch, **k: cleaned.append(branch) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)
    r = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r.exit_code == 0, r.output
    status = {row["key"]: row["status"] for row in json.loads(r.stdout)["results"]}
    assert status == {"jira:IPG-1": "cleaned", "jira:IPG-2": "skipped:open"}
    assert cleaned == ["feat/1"]
    links = store.load_links()
    assert "jira:IPG-1" not in links and "jira:IPG-2" in links


def test_cleanup_merged_skips_live_harness_and_invalid(isolated_config,
                                                       tmp_path, monkeypatch):
    wt_ok = tmp_path / "wt-ok"; wt_ok.mkdir()
    store.record_link("jira:IPG-3", {"issue": "IPG-3", "worktree": str(wt_ok),
                                     "branch": "feat/3", "repo": "/tmp/proj"})
    store.record_link("jira:IPG-4", {"issue": "IPG-4", "worktree": str(tmp_path / "gone"),
                                     "branch": "feat/4", "repo": "/tmp/proj"})
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "MERGED"}})
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 1}
                        if key == "jira:IPG-3" else None)
    cleaned = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda repo, branch, **k: cleaned.append(branch) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    r = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r.exit_code == 0, r.output
    status = {row["key"]: row["status"] for row in json.loads(r.stdout)["results"]}
    assert status == {"jira:IPG-3": "skipped:live-harness",
                      "jira:IPG-4": "skipped:invalid"}
    assert cleaned == []
    assert set(store.load_links()) == {"jira:IPG-3", "jira:IPG-4"}


def test_cleanup_merged_requires_yes_noninteractive(isolated_config, monkeypatch):
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": "/tmp/wt-1",
                                     "branch": "feat/1", "repo": "/tmp/proj"})
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "MERGED"}})
    r = _invoke("cleanup", "--merged", "--json")
    assert r.exit_code == 2
    assert "--yes" in r.output
    assert "jira:IPG-1" in store.load_links()
    # --dry-run previews without --yes and without acting.
    acted = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: acted.append(1) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)
    r2 = _invoke("cleanup", "--merged", "--dry-run", "--json")
    assert r2.exit_code == 0, r2.output
    assert acted == []
    status = {row["key"]: row["status"] for row in json.loads(r2.stdout)["results"]}
    assert status == {"jira:IPG-1": "dry-run"}
    assert "jira:IPG-1" in store.load_links()


def test_cleanup_merged_usage_errors(isolated_config):
    r = _invoke("cleanup", "IPG-1", "--merged", "--yes")
    assert r.exit_code == 2
    assert "--merged takes no ref" in r.output
    r2 = _invoke("cleanup")
    assert r2.exit_code == 2
    assert "ref required" in r2.output


def test_cleanup_merges_open_pr_first(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "worktree": "/tmp/wt",
                                     "branch": "feat/9", "repo": "/tmp/proj",
                                     "pr_url": "https://github.com/o/r/pull/9"})
    calls = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: calls.append("cleanup") or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: calls.append("close"))
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "OPEN"}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda branch, url, cwd, tool=None: (
                            url, {"state": "OPEN", "mergeable": "MERGEABLE",
                                  "merge_state": ""}, False))
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/9"})
    from workagent.errors import run_cmd as _real  # noqa: F841 (documents the seam)
    def fake(*a, **k):
        calls.append(a)
        return ""
    monkeypatch.setattr(cli, "_merge_pr",
                        lambda url, squash=True, cwd=None: calls.append("merge"))
    monkeypatch.setattr("workagent.cli.run_cmd", fake)
    entry = dict(store.load_links()["jira:IPG-9"])
    out = cli._cleanup_one("jira:IPG-9", entry, force=True, yes=True,
                           dry_run=False, json_output=True)
    assert out["status"] == "cleaned"
    assert "merge" in calls
    assert "close" not in calls  # merged, not closed
    assert out["remote_deleted"] is True


def test_cleanup_merge_failure_keeps_worktree(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-9", {"issue": "IPG-9", "worktree": "/tmp/wt",
                                     "branch": "feat/9", "repo": "/tmp/proj",
                                     "pr_url": "https://github.com/o/r/pull/9"})
    from workagent.errors import HarnessError
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "OPEN"}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda branch, url, cwd, tool=None: (
                            url, {"state": "OPEN", "mergeable": "CONFLICTING",
                                  "merge_state": "DIRTY"}, False))
    def boom(*a, **k):
        raise HarnessError("merge conflict")
    monkeypatch.setattr("workagent.cli.run_cmd", boom)
    import pytest, typer
    # Conflicted without --force fails closed BEFORE the merge call:
    # PR stays open, worktree/link/remote branch all kept.
    with pytest.raises(typer.exceptions.Exit):
        cli._cleanup_one("jira:IPG-9", dict(store.load_links()["jira:IPG-9"]),
                         force=False, yes=True, dry_run=False,
                         json_output=True)
    assert "jira:IPG-9" in store.load_links()  # link kept


def test_cleanup_picks_latest_open_pr(isolated_config, monkeypatch, capsys):
    """Newer closed PR must not win over the live open one."""
    store.record_link("jira:IPG-12", {"issue": "IPG-12", "worktree": "/tmp/wt",
                                      "branch": "feat/12", "repo": "/tmp/proj",
                                      "pr_url": "https://github.com/o/r/pull/1"})
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "OPEN"}})
    monkeypatch.setattr(cli.refs, "fetch_pr_list_for_branch",
                        lambda tool, branch, cwd=None: [
                            {"number": 1, "state": "merged",
                             "created_at": "2026-09-01",
                             "url": "https://github.com/o/r/pull/1"},
                            {"number": 2, "state": "open",
                             "created_at": "2026-09-15",
                             "url": "https://github.com/o/r/pull/2"},
                            {"number": 3, "state": "closed",
                             "created_at": "2026-09-20",
                             "url": "https://github.com/o/r/pull/3"}])
    seen = {}
    def fake_fresh(url, cwd):
        seen["url"] = url
        return {"state": "MERGED", "mergeable": "", "merge_state": ""}
    monkeypatch.setattr(cli, "_fresh_pr_state", fake_fresh)
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/12"})
    monkeypatch.setattr("workagent.cli.run_cmd", lambda *a, **k: "")
    out = cli._cleanup_one("jira:IPG-12", dict(store.load_links()["jira:IPG-12"]),
                           force=False, yes=True, dry_run=False,
                           json_output=True, merge=False)
    assert seen["url"] == "https://github.com/o/r/pull/2"
    assert "latest for branch" in capsys.readouterr().err
    assert out["remote_deleted"] is True


def test_cleanup_force_conflict_leaves_pr_open_keeps_remote(isolated_config, monkeypatch, capsys):
    """Rule 3: --force merge-fail → PR left open, remote kept, cleanup continues."""
    from workagent.errors import HarnessError
    store.record_link("jira:IPG-13", {"issue": "IPG-13", "worktree": "/tmp/wt",
                                      "branch": "feat/13", "repo": "/tmp/proj",
                                      "pr_url": "https://github.com/o/r/pull/13"})
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    closed = []
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: closed.append(True))
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "OPEN"}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda branch, url, cwd, tool=None: (
                            url, {"state": "OPEN", "mergeable": "CONFLICTING",
                                  "merge_state": "DIRTY"}, False))
    def boom(*a, **k):
        if "--delete" in str(a):
            return ""
        raise HarnessError("merge conflict")
    monkeypatch.setattr("workagent.cli.run_cmd", boom)
    out = cli._cleanup_one("jira:IPG-13", dict(store.load_links()["jira:IPG-13"]),
                           force=True, yes=True, dry_run=False,
                           json_output=True)
    assert not closed  # PR left open on unmergeable --force
    assert "left open" in capsys.readouterr().err
    assert out["status"] == "cleaned"
    assert out["remote_deleted"] is False


def test_cleanup_404_close_keeps_remote_and_link(isolated_config, monkeypatch):
    """Rule 2: close on a 404 PR keeps the remote branch and the link."""
    from workagent.errors import HarnessError
    import pytest, typer
    store.record_link("jira:IPG-14", {"issue": "IPG-14", "worktree": "/tmp/wt",
                                      "branch": "feat/14", "repo": "/tmp/proj",
                                      "pr_url": "https://github.com/o/r/pull/404"})
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "OPEN"}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda branch, url, cwd, tool=None: (url, None, False))
    def boom(parsed, url, force, cwd=None):
        raise HarnessError("gh pr close failed: 404 Not Found")
    monkeypatch.setattr(cli, "_close_pr", boom)
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    with pytest.raises(typer.exceptions.Exit):
        cli._cleanup_one("jira:IPG-14", dict(store.load_links()["jira:IPG-14"]),
                         force=False, yes=True, dry_run=False,
                         json_output=True, merge=False)
    assert "jira:IPG-14" in store.load_links()


def test_cleanup_unknown_pr_state_closes_without_merge(isolated_config, monkeypatch):
    store.record_link("jira:IPG-10", {"issue": "IPG-10", "worktree": "/tmp/wt",
                                      "branch": "feat/10", "repo": "/tmp/proj",
                                      "pr_url": "https://github.com/o/r/pull/10"})
    calls = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: calls.append("close"))
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": ""}})
    monkeypatch.setattr(cli, "_resolve_branch_pr", lambda branch, url, cwd, tool=None: (url, None, False))
    monkeypatch.setattr("workagent.cli.run_cmd",
                        lambda *a, **k: calls.append("merge") or "")
    out = cli._cleanup_one("jira:IPG-10", dict(store.load_links()["jira:IPG-10"]),
                           force=False, yes=True, dry_run=False, json_output=True)
    assert "close" in calls
    assert out["remote_deleted"] is False  # unknown state keeps remote branch


def test_close_pr_treats_merged_mr_as_closed(isolated_config, monkeypatch, capsys):
    """`glab mr close` on a merged MR must not abort cleanup (run eb420c1bb14d)."""
    from workagent.errors import HarnessError
    import workagent.errors as errors
    def fake_run(*a, **k):
        raise HarnessError("glab mr close https://git.jibit.cloud/server/projectx/-/merge_requests/1700 "
                           "failed: ERROR\n\n  This merge request has already been merged.")
    monkeypatch.setattr(errors, "run_cmd", fake_run)
    cli._close_pr({}, "https://git.jibit.cloud/server/projectx/-/merge_requests/1700",
                  force=False, cwd="/tmp")
    assert "already closed" in capsys.readouterr().err


def test_merge_pr_treats_merged_mr_as_success(isolated_config, monkeypatch, capsys):
    """`glab mr merge` on an already-merged MR must not abort cleanup (3d TTL staleness)."""
    from workagent.errors import HarnessError
    import workagent.errors as errors
    def fake_run(*a, **k):
        raise HarnessError("glab mr merge https://git.jibit.cloud/x/-/merge_requests/1 "
                           "failed: ERROR\n\n  This merge request has already been merged.")
    monkeypatch.setattr(cli, "run_cmd", fake_run)
    cli._merge_pr("https://git.jibit.cloud/x/-/merge_requests/1", squash=True, cwd="/tmp")
    assert "already merged/closed" in capsys.readouterr().err


def test_cleanup_merged_state_skips_remote_close(isolated_config, monkeypatch, capsys):
    """Known-merged PR state skips the host close call entirely (no-op)."""
    store.record_link("jira:IPG-11", {"issue": "IPG-11", "worktree": "/tmp/wt",
                                      "branch": "feat/11", "repo": "/tmp/proj",
                                      "pr_url": "https://github.com/o/r/pull/11"})
    calls = []
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: calls.append("close"))
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": "merged"}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda branch, url, cwd, tool=None: (
                            url, {"state": "MERGED", "mergeable": "",
                                  "merge_state": ""}, False))
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/11"})
    pushes = []
    def fake_run(*a, **k):
        pushes.append(a)
        return ""
    monkeypatch.setattr("workagent.cli.run_cmd", fake_run)
    out = cli._cleanup_one("jira:IPG-11", dict(store.load_links()["jira:IPG-11"]),
                           force=False, yes=True, dry_run=False, json_output=True)
    assert "close" not in calls
    assert "skipping remote close" in capsys.readouterr().err
    assert out["remote_deleted"] is True  # merged source branch is deleted
    assert any("--delete" in str(c) for c in pushes)


def test_close_issue_treats_closed_as_success(isolated_config, monkeypatch, capsys):
    """`gh issue close` on a closed issue continues cleanup instead of raising."""
    from workagent.errors import HarnessError
    import workagent.errors as errors
    def fake_run(*a, **k):
        raise HarnessError("gh issue close 22 --repo o/r failed: GraphQL: Could not resolve to an Issue with the number of 22")
    monkeypatch.setattr(errors, "run_cmd", fake_run)
    cli._close_issue({"tool": "gh", "repo": "o/r", "kind": "issue_or_pr",
                      "number": "22", "url": "https://github.com/o/r/issues/22"},
                     force=False)
    assert "already closed" in capsys.readouterr().err


def test_cleanup_repairs_stale_repo_from_live_worktree(isolated_config, tmp_path, monkeypatch):
    """Stale recorded repo (wrong checkout) is repaired from the live
    worktree, and the MR close runs with cwd inside the real repo."""
    import subprocess
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "-C", str(main), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.name", "t"], check=True)
    (main / "f").write_text("x")
    subprocess.run(["git", "-C", str(main), "add", "."], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(main), "checkout", "-qb", "feat/x"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", str(wt)], check=True)
    store.record_link("jira:X-1", {"issue": "X-1", "worktree": str(wt),
                                   "branch": "feat/x", "repo": "/stale/checkout",
                                   "pr_url": "https://git.example.com/g/r/-/merge_requests/1"})
    seen = {}
    monkeypatch.setattr(cli.gitwt, "cleanup_worktree",
                        lambda repo, branch, **k: seen.setdefault("repo", str(repo)) or {"ok": True})
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    def close(parsed, url, force, cwd=None):
        seen["cwd"] = cwd
        # Worktree must still exist at close time: the close runs BEFORE
        # git-wt teardown (IPG-953: close-after-teardown ran glab from a
        # deleted cwd and failed with "no git remote points to a known host").
        seen["wt_alive_at_close"] = Path(cwd).is_dir()
    monkeypatch.setattr(cli, "_close_pr", close)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda entry, refresh_pr=False: {"pr_data": {"state": ""}})
    cli._cleanup_one("jira:X-1", dict(store.load_links()["jira:X-1"]),
                     force=False, yes=True, dry_run=False, json_output=True)
    assert seen["repo"] == str(main)
    assert seen["cwd"] == str(wt)
    assert seen["wt_alive_at_close"] is True
    assert "jira:X-1" not in store.load_links()


def test_merge_pr_github_squash_default(isolated_config, monkeypatch):
    """GitHub merges pass --squash (gh requires an explicit strategy non-interactively)."""
    seen = []
    monkeypatch.setattr(cli, "run_cmd", lambda *a, **k: seen.append(a) or "")
    cli._merge_pr("https://github.com/o/r/pull/9", squash=True, cwd="/tmp")
    assert seen[0][:4] == ("gh", "pr", "merge", "https://github.com/o/r/pull/9")
    assert "--squash" in seen[0]


def test_merge_pr_github_no_squash_uses_merge(isolated_config, monkeypatch):
    """--no-squash maps to gh --merge (gh has no --no-squash flag)."""
    seen = []
    monkeypatch.setattr(cli, "run_cmd", lambda *a, **k: seen.append(a) or "")
    cli._merge_pr("https://github.com/o/r/pull/9", squash=False, cwd="/tmp")
    assert "--merge" in seen[0]
    assert "--no-squash" not in seen[0]
