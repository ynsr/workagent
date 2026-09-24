"""Tracker-aware repo picker (TDD RED)."""

from __future__ import annotations

from pathlib import Path

from workagent import store, trackers


def _seed(tid, repos, monkeypatch, tmp_path):
    for r in repos:
        store.add_tracker_repo(tid, r)
    monkeypatch.setattr(trackers.repos, "repo_root", lambda cwd: None)


def test_cwd_linked_uses_cwd(isolated_config, tmp_path, monkeypatch):
    from workagent import repos as _repos

    cwd_repo = tmp_path / "proj"
    cwd_repo.mkdir()
    _seed("jira:IPG", [str(cwd_repo)], monkeypatch, tmp_path)
    monkeypatch.setattr(_repos, "repo_root", lambda cwd: cwd_repo)
    # Issue #26: CWD is never a default — the single linked repo wins
    # regardless of where the process runs from.
    r, outcome = trackers.resolve_for_tracker("jira:IPG", None, cwd_repo, yes=False)
    assert Path(r) == cwd_repo.resolve() and outcome == "ok"


def test_cwd_unlinked_no_falls_to_linked_list(isolated_config, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    linked = tmp_path / "proj"
    linked.mkdir()
    _seed("jira:IPG", [str(linked)], monkeypatch, tmp_path)
    from workagent import repos as _repos

    monkeypatch.setattr(_repos, "repo_root", lambda cwd: other)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # No prompt anymore: unlinked CWD is ignored, single linked repo wins.
    r, outcome = trackers.resolve_for_tracker("jira:IPG", None, other, yes=False)
    assert Path(r) == linked.resolve()


def test_no_repo_single_linked_wins(isolated_config, tmp_path, monkeypatch):
    linked = tmp_path / "proj"
    linked.mkdir()
    _seed("jira:IPG", [str(linked)], monkeypatch, tmp_path)
    r, outcome = trackers.resolve_for_tracker("jira:IPG", None, tmp_path / "plain", yes=False)
    assert Path(r) == linked.resolve()


def test_zero_links_aborts_without_repo(isolated_config, tmp_path, monkeypatch):
    """No linked repos + no --repo → exit 2 even on a TTY-less run (issue #26)."""
    from workagent.errors import HarnessError
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    try:
        trackers.resolve_for_tracker("jira:IPG", None, tmp_path, yes=False)
    except HarnessError as e:
        assert e.exit_code == 2 and "--repo" in str(e)
    else:
        raise AssertionError("expected HarnessError")


def test_default_repo_for_ref_prefers_linked_worktree(isolated_config, tmp_path):
    """Rule 1: an issue already linked to a worktree resolves to its repo."""
    repo = tmp_path / "proj"
    repo.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("jira:IPG-1", {"worktree": str(wt), "branch": "b",
                                     "repo": str(repo)})
    assert trackers.default_repo_for_ref("IPG-1") == str(repo)


def test_default_repo_for_ref_single_linked(isolated_config, tmp_path, monkeypatch):
    """Rule 2: tracker with exactly one linked repo → that repo."""
    linked = tmp_path / "proj"
    linked.mkdir()
    _seed("jira:IPG", [str(linked)], monkeypatch, tmp_path)
    assert trackers.default_repo_for_ref("IPG-99") == str(linked)


def test_default_repo_for_ref_multi_linked_empty(isolated_config, tmp_path, monkeypatch):
    """Ambiguous tracker (2+ repos) and no worktree link → no default."""
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    _seed("jira:IPG", [str(a), str(b)], monkeypatch, tmp_path)
    assert trackers.default_repo_for_ref("IPG-99") == ""


def test_no_repo_multi_linked_prompts(isolated_config, tmp_path, monkeypatch):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    _seed("jira:IPG", [str(a), str(b)], monkeypatch, tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    chosen = []
    monkeypatch.setattr(trackers.pick, "pick",
                        lambda label, options: chosen.append(options) or 1)
    r, outcome = trackers.resolve_for_tracker("jira:IPG", None, tmp_path / "plain", yes=False)
    assert Path(r) == b.resolve()
    assert chosen == [[str(a), str(b)]]



def test_explicit_repo_still_guarded(isolated_config, tmp_path, monkeypatch):
    linked = tmp_path / "proj"
    linked.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    _seed("jira:IPG", [str(linked)], monkeypatch, tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    from workagent.errors import HarnessError

    try:
        trackers.resolve_for_tracker("jira:IPG", str(other), other, yes=False)
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")

def test_yes_does_not_adopt_unlinked_cwd(isolated_config, tmp_path, monkeypatch):
    """--yes (web headless) with an unlinked cwd falls through to linked repos (issue #23)."""
    other = tmp_path / "other"
    other.mkdir()
    linked = tmp_path / "proj"
    linked.mkdir()
    _seed("jira:IPG", [str(linked)], monkeypatch, tmp_path)
    from workagent import repos as _repos
    monkeypatch.setattr(_repos, "repo_root", lambda cwd: other)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    r, outcome = trackers.resolve_for_tracker("jira:IPG", None, other, yes=True)
    assert Path(r) == linked.resolve()
    # The cwd repo must NOT be recorded under the tracker.
    assert str(other.resolve()) not in store.load_config()["trackers"]["jira:IPG"]["repos"]
