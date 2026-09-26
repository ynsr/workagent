"""CLI review --all tests (split from test_cli.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _init_repo, _add_wt, _start_mocks

def test_review_all_spawns_parallel_children(isolated_config, tmp_path, monkeypatch):
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1"},
        "jira:B-2": {"branch": "feat/b", "worktree": str(tmp_path / "b"),
                     "pr_url": "https://github.com/o/r/pull/2"},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    for d in ("a", "b"):
        (tmp_path / d / ".git").mkdir()  # cheap validity stand-in
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    real_load_links = store.load_links
    monkeypatch.setattr(cli.store, "load_links", lambda: real_load_links())
    spawns = []

    class FakePopen:
        def __init__(self, argv, **kw):
            self.argv = argv
            self.pid = 4242
            spawns.append(argv)
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert len(spawns) == 2
    for argv in spawns:
        assert argv[:3] == [cli.sys.executable, "-m", "workagent"]
        assert "--no-tty" in argv and "--post-comments" in argv
    out = json.loads(r.stdout)
    assert sorted(e["key"] for e in out) == ["jira:A-1", "jira:B-2"]
    assert all(e["exit_code"] == 0 for e in out)


def test_review_all_sequential_waits_one_by_one(isolated_config, tmp_path, monkeypatch):
    """--sequential spawns and waits per worktree; --fix reaches the child argv."""
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1"},
        "jira:B-2": {"branch": "feat/b", "worktree": str(tmp_path / "b"),
                     "pr_url": "https://github.com/o/r/pull/2"},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    events = []

    class FakePopen:
        def __init__(self, argv, **kw):
            self.argv = argv
            events.append(("spawn", argv))
        def wait(self, timeout=None):
            events.append(("wait", self.argv))
            return 3

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--sequential", "--fix", "--json"])
    assert r.exit_code == 0, r.output
    assert [e[0] for e in events] == ["spawn", "wait", "spawn", "wait"]
    for kind, argv in events:
        if kind == "spawn":
            assert "--fix" in argv and "--post-comments" in argv and "--no-tty" in argv
    out = json.loads(r.stdout)
    assert all(e["exit_code"] == 3 for e in out)  # child rc surfaced in summary


def test_review_all_skips_live_harness_and_empty(isolated_config, tmp_path, monkeypatch):
    """A live-harness worktree is skipped with a note; zero reviewable → exit 0."""
    wt = tmp_path / "a"
    wt.mkdir()
    store.record_link("jira:A-1", {"branch": "feat/a", "worktree": str(wt),
                                   "pr_url": "https://github.com/o/r/pull/1"})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 99999, "started_at": 1.0})
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []  # live harness worktree skipped, nothing else to do
    assert "harness already live" in r.stderr
    assert "nothing to review" in r.stderr
    assert r.stdout.strip() == ""


def test_review_sequential_without_all_fails():
    r = runner.invoke(cli.app, ["review", "--sequential"])
    assert r.exit_code == 2


def test_review_all_notes_no_pr_skips(isolated_config, tmp_path, monkeypatch):
    """--all must note worktrees with no resolvable PR/MR, not skip silently."""
    wt = tmp_path / "a"
    wt.mkdir()
    store.record_link("jira:A-1", {"branch": "feat/a", "worktree": str(wt)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.worktrees, "worktree_pr_url", lambda k, v: None)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []
    assert "jira:A-1: no PR/MR — skipping" in r.stderr
    assert "nothing to review" in r.stderr


def test_review_all_dry_run_prints_plan_without_spawning(isolated_config,
                                                         tmp_path,
                                                         monkeypatch):
    """--all --dry-run prints the per-child plan and spawns nothing."""
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1",
                     "repo": str(tmp_path / "proj")},
        "jira:B-2": {"branch": "feat/b", "worktree": str(tmp_path / "b"),
                     "pr_url": "https://github.com/o/r/pull/2",
                     "repo": str(tmp_path / "proj")},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []  # dry run must not launch children
    out = json.loads(r.stdout)
    assert sorted(e["key"] for e in out) == ["jira:A-1", "jira:B-2"]
    for row in out:
        assert row["pr_url"].startswith("https://github.com/o/r/pull/")
        assert "-m workagent review" in row["command"]
        assert row["pr_url"] in row["command"]
        assert "--no-tty" in row["command"] and "--post-comments" in row["command"]


def test_review_all_no_runtime_prints_commands_without_spawning(isolated_config,
                                                                tmp_path,
                                                                monkeypatch):
    """--all -N prints each child command as summary rows without spawning."""
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1",
                     "repo": str(tmp_path / "proj")},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--no-runtime", "--json"])
    assert r.exit_code == 0, r.output
    assert spawned == []
    out = json.loads(r.stdout)
    assert [e["key"] for e in out] == ["jira:A-1"]
    assert out[0]["exit_code"] == ""
    assert "-m workagent review https://github.com/o/r/pull/1" in out[0]["command"]
    assert "--no-tty" in out[0]["command"] and "--post-comments" in out[0]["command"]


def test_review_launch_failure_clears_reviewed(isolated_config, tmp_path,
                                               monkeypatch):
    """A failed non-exec launch reverts the reviewed fields (spec bookkeeping)."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False,
                        persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.repos, "branch_tip", lambda wt: "abc123")
    monkeypatch.setattr(cli.refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})

    from workagent.errors import HarnessError

    def boom(*a, **k):
        raise HarnessError("no such harness", 1)

    monkeypatch.setattr(cli.backend, "launch", boom)
    url = "https://github.com/o/r/pull/33"
    key = "feat/33"
    r = runner.invoke(cli.app, ["review", url, "--no-tty", "--json"])
    assert r.exit_code == 1
    entry = store.load_links()[key]
    assert "reviewed" not in entry and "reviewed_at" not in entry


def test_review_all_jira_pr_alias_dedupes(isolated_config, tmp_path,
                                          monkeypatch):
    """jira: and pr: links to the same PR → one reviewable child; a reviewed
    pr: entry makes the jira: alias unreviewable too."""
    wt = tmp_path / "a"
    wt.mkdir()
    (wt / ".git").mkdir()
    pr = "https://github.com/o/r/pull/1"
    store.record_link("jira:A-1", {"branch": "feat/a", "worktree": str(wt),
                                   "pr_url": pr})
    store.record_link(f"pr:{pr}", {"pr_url": pr, "branch": "feat/a",
                                   "worktree": str(wt)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    monkeypatch.setattr(cli.repos, "branch_tip", lambda p: "tip1")

    spawned = []
    class FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert len(spawned) == 1  # alias deduped: one child per worktree/PR
    assert spawned[0][4] == pr  # child spawned with the pr: entry's URL

    # Reviewed pr: entry → jira: alias skipped too.
    links = store.load_links()
    links[f"pr:{pr}"]["reviewed"] = True
    links[f"pr:{pr}"]["reviewed_at"] = "tip1"
    store.save_links(links)
    spawned.clear()
    r2 = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r2.exit_code == 0, r2.output
    assert spawned == []
    assert "nothing to review" in r2.stderr


def test_review_fix_without_all_is_usage_error():
    r = runner.invoke(cli.app, ["review", "https://github.com/o/r/pull/1", "--fix"])
    assert r.exit_code == 2
    assert "--fix requires --all" in r.stderr


def test_review_fix_comments_rejects_fix_and_post_comments():
    """Issue #32: --fix-comments is exclusive with --fix/--post-comments."""
    r = runner.invoke(cli.app, ["review", "--all", "--fix", "--fix-comments"])
    assert r.exit_code == 2
    assert "--fix cannot be used with --fix-comments" in r.stderr


def test_review_all_fix_comments_reaches_child_argv(isolated_config, tmp_path, monkeypatch):
    """Issue #32: review --all --fix-comments spawns fix-children (no post-comments)."""
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1"},
    }
    (tmp_path / "a").mkdir()
    store.save_links(links)
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.worktrees, "resolve_worktree", lambda ref, _l: "jira:A-1")
    monkeypatch.setattr(cli.worktrees, "worktree_pr_url",
                        lambda k, e: links[k]["pr_url"])
    seen: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv, **kw):
            seen.append(argv)
        def wait(self, timeout=None):
            return 0


    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = runner.invoke(cli.app, ["review", "--all", "--fix-comments", "--json"])
    assert r.exit_code == 0, r.output
    assert seen and "--fix-comments" in seen[0]
    assert "--post-comments" not in seen[0] and "--fix" not in seen[0]
