"""Tracker↔repo relation guards + cleanup ref resolution (TDD RED)."""

from __future__ import annotations

from harness import refs, store, trackers


def test_tracker_id_jira_prefix(isolated_config):
    p = refs.parse_ref("IPG-981")
    assert trackers.tracker_id(p) == "jira:IPG"


def test_tracker_id_github_repo(isolated_config):
    p = refs.parse_ref("https://github.com/OWNER/REPO/issues/22")
    assert trackers.tracker_id(p) == "github:OWNER/REPO"


def test_tracker_id_gitlab_mr(isolated_config):
    p = refs.parse_ref("https://git.jibit.cloud/server/projectx/-/merge_requests/1701")
    assert trackers.tracker_id(p) == "gitlab:git.jibit.cloud/server/projectx"


def test_first_use_records_mapping(isolated_config, tmp_path):
    repo = tmp_path / "projectx"
    repo.mkdir()
    assert trackers.check_or_record("jira:IPG", str(repo), yes=True) == "recorded"
    assert store.load_config()["trackers"]["jira:IPG"] == {"repos": [str(repo)]}


def test_same_repo_passes_silently(isolated_config, tmp_path):
    repo = tmp_path / "projectx"
    repo.mkdir()
    trackers.check_or_record("jira:IPG", str(repo), yes=True)
    assert trackers.check_or_record("jira:IPG", str(repo), yes=True) == "ok"


def test_mismatch_non_tty_without_yes_aborts(isolated_config, tmp_path, monkeypatch):
    from harness.errors import HarnessError

    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    trackers.check_or_record("jira:IPG", str(a), yes=True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    try:
        trackers.check_or_record("jira:IPG", str(b), yes=False)
    except HarnessError as e:
        assert e.exit_code == 2
        assert "--yes" in str(e)
    else:
        raise AssertionError("expected HarnessError")


def test_mismatch_yes_records_new_repo(isolated_config, tmp_path, monkeypatch):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    trackers.check_or_record("jira:IPG", str(a), yes=True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert trackers.check_or_record("jira:IPG", str(b), yes=True) == "recorded"
    assert str(b) in store.load_config()["trackers"]["jira:IPG"]["repos"]
    assert str(a) in store.load_config()["trackers"]["jira:IPG"]["repos"]
