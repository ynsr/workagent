"""Tracker↔repo relation guards + cleanup ref resolution (TDD RED)."""

from __future__ import annotations

import json

from harness import refs, store, trackers
from harness.errors import HarnessError


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


# ── list_my_issues ────────────────────────────────────────────────────


def test_list_my_issues_jira_request(isolated_config, tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"url": "https://jira.example.com"}))
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (cfg,))
    calls = []

    def fake(*a, **k):
        calls.append(a)
        return json.dumps({"issues": [
            {"key": "IPG-981", "fields": {"summary": "T",
                                          "status": {"name": "In Progress"},
                                          "created": "2026-09-20T10:00:00.000+0000"}}]})

    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = trackers.list_my_issues()
    assert rows[0]["key"] == "jira:IPG-981" and rows[0]["status"] == "In Progress"
    assert rows[0]["title"] == "T" and rows[0]["created"].endswith("+0000")
    assert rows[0]["url"] == "https://jira.example.com/browse/IPG-981"
    assert calls[0][:3] == ("jira-cli", "request", "GET")


def test_list_my_issues_jira_search_fallback(isolated_config, monkeypatch):
    """This machine's jira-cli shim has no `request` subcommand; the search
    seam returns a bare list of raw-API issue objects."""
    calls = []

    def fake(*a, **k):
        calls.append(a)
        if a[:2] == ("jira-cli", "request"):
            return "Unknown: request"  # shim: rc 0, not JSON
        if a[:2] == ("jira-cli", "search"):
            return json.dumps([{"key": "IPG-7", "fields": {
                "summary": "S", "status": {"name": "To Do"},
                "created": "2026-09-20T10:00:00.000+0000"}}])
        raise HarnessError("command not found: gh")

    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = trackers.list_my_issues()
    assert rows[0]["key"] == "jira:IPG-7" and rows[0]["status"] == "To Do"
    assert calls[1][1] == "search"
    assert calls[1][2] == ('reporter = currentUser() AND status in '
                           '("To Do", "In Progress") AND created >= -60d '
                           'ORDER BY created DESC')


def test_list_my_issues_jira_plain_fallback(isolated_config, monkeypatch):
    """Stock jira-cli without request/search: plain issue list, 2+ space
    column padding, right-anchored columns (…|status|created)."""

    def fake(*a, **k):
        if a[:2] == ("jira-cli", "request") or a[:2] == ("jira-cli", "search"):
            raise HarnessError("unknown command")
        if a[0] == "jira-cli":
            assert a[1:3] == ("issue", "list")
            return "\n".join([
                "IPG-5    Fix login flow    In Progress    2026-09-20T10:00:00.000+0000",
                "IPG-6    Other    To Do    2026-09-18T08:00:00.000+0000",
                "",
            ])
        raise HarnessError("command not found: gh")
    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = trackers.list_my_issues()
    assert [r["key"] for r in rows] == ["jira:IPG-5", "jira:IPG-6"]
    assert rows[0]["title"] == "Fix login flow" and rows[0]["status"] == "In Progress"


def test_list_my_issues_jira_plain_unparseable_warns(isolated_config,
                                                     monkeypatch):
    """A plain seam that answers rc 0 with non-empty garbage must fall
    through to the all-tiers warning, not report zero issues."""

    def fake(*a, **k):
        if a[:2] == ("jira-cli", "request"):
            return "Unknown: request"  # rc 0, not JSON
        if a[:2] == ("jira-cli", "search"):
            return "Unknown: search"  # rc 0, not JSON
        if a[:2] == ("jira-cli", "issue"):
            return "Unknown: issue list"  # rc 0, no parseable rows
        raise HarnessError("command not found: gh")

    monkeypatch.setattr(trackers, "run_cmd", fake)
    warnings = []
    assert trackers.list_my_issues(warnings) == []
    assert any("jira" in w and "issue list" in w for w in warnings)


def test_list_my_issues_gh_search(isolated_config, monkeypatch):
    def fake(*a, **k):
        if a[0] == "gh":
            return json.dumps([
                {"repository": {"nameWithOwner": "o/r"}, "number": 7,
                 "title": "Crash on save", "updatedAt": "2026-09-21T08:47:57Z",
                 "url": "https://github.com/o/r/issues/7",
                 "createdAt": "2026-09-19T01:00:00Z"}])
        raise HarnessError("command not found: jira-cli")

    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = trackers.list_my_issues()
    assert rows[0]["key"] == "github:o/r#7" and rows[0]["status"] == "open"
    assert rows[0]["title"] == "Crash on save" and rows[0]["created"].endswith("Z")


def test_list_my_issues_cli_missing(isolated_config, monkeypatch):
    def boom(*a, **k):
        raise HarnessError("command not found")

    monkeypatch.setattr(trackers, "run_cmd", boom)
    assert trackers.list_my_issues() == []


def test_list_my_issues_warnings_collected(isolated_config, monkeypatch):
    def boom(*a, **k):
        raise HarnessError("command not found")

    monkeypatch.setattr(trackers, "run_cmd", boom)
    warnings: list[str] = []
    assert trackers.list_my_issues(warnings) == []
    assert any("jira" in w for w in warnings)
    assert any("github" in w for w in warnings)


def test_parse_created_offsets():
    from datetime import datetime, timezone

    dt = trackers.parse_created("2026-09-20T10:00:00.000+0000")
    assert dt is not None and dt.tzinfo is not None
    assert dt == datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    assert trackers.parse_created("2026-09-20T10:00:00Z") == dt
    assert trackers.parse_created("garbage") is None
    assert trackers.parse_created("") is None


def test_my_issues_uses_cache_within_ttl(monkeypatch, tmp_path):
    from harness import trackers
    from harness import store_sqlite as sq
    import harness.store as store
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    rows = [{"key": "IPG-1", "title": "t", "url": "u", "status": "To Do",
             "created": "2026-09-01"}]
    sq.set_issue_cache(sq.db_path(), "jira", rows)
    def _boom(warn):
        raise AssertionError("must use cache")
    monkeypatch.setattr(trackers, "_jira_my_issues", _boom)
    monkeypatch.setattr(trackers, "_gh_my_issues", lambda warn: [])
    got = trackers.list_my_issues([])
    assert [r["key"] for r in got] == ["IPG-1"]


def test_my_issues_force_skips_cache(monkeypatch, tmp_path):
    from harness import trackers
    from harness import store_sqlite as sq
    import harness.store as store
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    sq.set_issue_cache(sq.db_path(), "jira", [{"key": "STALE"}])
    monkeypatch.setattr(trackers, "_jira_my_issues", lambda warn: [])
    monkeypatch.setattr(trackers, "_gh_my_issues", lambda warn: [])
    got = trackers.list_my_issues([], force=True)
    assert got == []
