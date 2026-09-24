"""Tracker-aware repo picker (TDD RED)."""

from __future__ import annotations

from pathlib import Path

from workagent import store, trackers


def _seed(tid, repos, monkeypatch, tmp_path):
    cfg = store.load_config()
    cfg["trackers"] = {tid: {"repos": repos}}
    store.save_config(cfg)
    monkeypatch.setattr(trackers.repos, "repo_root", lambda cwd: None)


def test_cwd_linked_uses_cwd(isolated_config, tmp_path, monkeypatch):
    from workagent import repos as _repos

    cwd_repo = tmp_path / "proj"
    cwd_repo.mkdir()
    _seed("jira:IPG", [str(cwd_repo)], monkeypatch, tmp_path)
    monkeypatch.setattr(_repos, "repo_root", lambda cwd: cwd_repo)
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
    # "n" to the y/N prompt -> falls through to linked repos; single linked repo wins.
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    r, outcome = trackers.resolve_for_tracker("jira:IPG", None, other, yes=False)
    assert Path(r) == linked.resolve()


def test_no_repo_single_linked_wins(isolated_config, tmp_path, monkeypatch):
    linked = tmp_path / "proj"
    linked.mkdir()
    _seed("jira:IPG", [str(linked)], monkeypatch, tmp_path)
    r, outcome = trackers.resolve_for_tracker("jira:IPG", None, tmp_path / "plain", yes=False)
    assert Path(r) == linked.resolve()


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
