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


def test_default_repo_for_ref_worktree_key(isolated_config, tmp_path):
    """Rule 0: a linked worktree key (Launch Review/Sync case) resolves to
    that worktree's repo, even for a second repo on the same tracker."""
    repo = tmp_path / "proj"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("branch:feat-login", {"worktree": str(wt),
                                            "branch": "feat/login",
                                            "repo": str(other)})
    assert trackers.default_repo_for_ref("branch:feat-login") == str(other)
    assert trackers.default_repo_for_ref("feat/login") == str(other)


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


def test_no_repo_multi_linked_errors(isolated_config, tmp_path, monkeypatch):
    """Issue #29: several linked repos + no --repo → exit 2 naming candidates."""
    from workagent.errors import HarnessError
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    _seed("jira:IPG", [str(a), str(b)], monkeypatch, tmp_path)
    for yes, tty in ((False, True), (True, True), (False, False)):
        monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
        try:
            trackers.resolve_for_tracker("jira:IPG", None, tmp_path / "plain", yes=yes)
        except HarnessError as e:
            assert e.exit_code == 2
            assert "--repo" in str(e) and str(a) in str(e) and str(b) in str(e)
        else:
            raise AssertionError(f"expected HarnessError (yes={yes})")



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


def test_resolve_repo_for_ref_explicit_wins(isolated_config, tmp_path, monkeypatch):
    """B2: explicit --repo wins over a worktree-pinned repo; base branch returned."""
    from workagent import refs, repos as _repos
    repo = tmp_path / "proj"
    repo.mkdir()
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    monkeypatch.setattr(_repos, "resolve_repo", lambda explicit, cwd, depth=7: repo)
    monkeypatch.setattr(_repos, "default_branch", lambda r: "main")
    parsed = refs.parse_ref("o/r#22")
    r, base, tid, outcome = trackers.resolve_repo_for_ref(
        parsed, str(repo), tmp_path, yes=True, persist=False, pinned=str(pinned))
    assert r == repo and base == "main" and tid == "github:o/r"


def test_resolve_repo_for_ref_uses_pinned(isolated_config, tmp_path, monkeypatch):
    """B2: without --repo the worktree-pinned repo is used (|single linked)."""
    from workagent import refs
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    _seed("github:o/r", [str(pinned)], monkeypatch, tmp_path)
    monkeypatch.setattr(trackers.repos, "default_branch", lambda r: "main")
    parsed = refs.parse_ref("o/r#22")
    r, base, tid, outcome = trackers.resolve_repo_for_ref(
        parsed, None, tmp_path, yes=True, persist=False, pinned=str(pinned))
    assert Path(r).resolve() == pinned.resolve() and base == "main"


def test_resolve_repo_for_ref_multi_linked_errors(isolated_config, tmp_path, monkeypatch):
    """B2: several linked repos without --repo stay a usage error (never first-pick)."""
    from workagent import refs
    from workagent.errors import HarnessError
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    _seed("github:o/r", [str(a), str(b)], monkeypatch, tmp_path)
    parsed = refs.parse_ref("o/r#22")
    try:
        trackers.resolve_repo_for_ref(parsed, None, tmp_path, yes=True, persist=False)
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")
