"""Tracker↔repo relation guards + cleanup ref resolution (TDD RED)."""

from __future__ import annotations

import json

from workagent import refs, store, trackers
from workagent.errors import HarnessError


def test_tracker_id_jira_prefix(isolated_config):
    p = refs.parse_ref("IPG-981")
    assert trackers.tracker_id(p) == "jira:IPG"


def test_tracker_id_github_repo(isolated_config):
    p = refs.parse_ref("https://github.com/OWNER/REPO/issues/22")
    assert trackers.tracker_id(p) == "github:OWNER/REPO"


def test_tracker_id_gitlab_mr(isolated_config):
    p = refs.parse_ref("https://git.jibit.cloud/server/projectx/-/merge_requests/1701")
    assert trackers.tracker_id(p) == "gitlab:git.jibit.cloud/server/projectx"


def test_default_tracker_github_remote(tmp_path, monkeypatch):
    """GitHub origin → github:O/R without spawning a host-CLI probe (issue #19)."""
    import subprocess
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                    "https://github.com/owner/repo.git"], check=True)
    monkeypatch.setattr(trackers.repos, "_detect_host_cli", lambda path: "gh")
    assert trackers.default_tracker_for_repo(d) == "github:owner/repo"


def test_default_tracker_gitlab_remote(tmp_path, monkeypatch):
    import subprocess
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                    "https://git.jibit.cloud/server/projectx.git"], check=True)
    monkeypatch.setattr(trackers.repos, "_detect_host_cli", lambda path: "glab")
    assert trackers.default_tracker_for_repo(d) == "gitlab:git.jibit.cloud/server/projectx"


def test_default_tracker_no_remote_empty(tmp_path, monkeypatch):
    import subprocess
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    monkeypatch.setattr(trackers.repos, "_detect_host_cli", lambda path: "gh")
    assert trackers.default_tracker_for_repo(d) == ""


def test_default_tracker_github_ssh_url(tmp_path, monkeypatch):
    """ssh:// GitHub remote derives like https/scp (review catch)."""
    import subprocess
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                    "ssh://git@github.com/owner/repo.git"], check=True)
    monkeypatch.setattr(trackers.repos, "_detect_host_cli", lambda path: "gh")
    assert trackers.default_tracker_for_repo(d) == "github:owner/repo"

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
    from workagent.errors import HarnessError

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


def test_host_scoped_tracker_rejects_foreign_remote_repo(isolated_config, tmp_path, monkeypatch):
    """A host-scoped tracker must refuse a repo whose origin belongs to a
    different host-scoped tracker (filing a GitLab checkout under
    ``github:o/r`` is always a wrong-repo write)."""
    import subprocess
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                    "https://git.example.com/group/proj.git"], check=True)
    monkeypatch.setattr(trackers.repos, "_detect_host_cli", lambda path: "glab")
    monkeypatch.setattr(trackers.repos, "_known_glab_hosts", lambda: {"git.example.com"})
    from workagent.errors import HarnessError
    with __import__("pytest").raises(HarnessError):
        trackers.check_or_record("github:owner/repo", str(d), yes=True)

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
    assert calls[1][2] == ('assignee = currentUser() AND '
                           'created >= -90d ORDER BY created DESC')

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
    """The initial (assigned/created, 90d) variant serves from its own key."""
    from workagent import trackers
    from workagent import store_sqlite as sq
    import workagent.store as store
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    rows = [{"key": "IPG-1", "title": "t", "url": "u", "status": "To Do",
             "created": "2026-09-01"}]
    sq.set_issue_cache(sq.db_path(), trackers._cache_key("jira", 90, True), rows)
    def _boom(warn, *a, **k):
        raise AssertionError("must use cache")
    monkeypatch.setattr(trackers, "_jira_my_issues", _boom)
    monkeypatch.setattr(trackers, "_gh_my_issues", lambda warn, *a, **k: [])
    got = trackers.list_my_issues([])
    assert [r["key"] for r in got] == ["IPG-1"]


def test_my_issues_force_skips_cache(monkeypatch, tmp_path):
    from workagent import trackers
    from workagent import store_sqlite as sq
    import workagent.store as store
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    sq.set_issue_cache(sq.db_path(), trackers._cache_key("jira", 90, True), [{"key": "STALE"}])
    monkeypatch.setattr(trackers, "_jira_my_issues", lambda warn, *a, **k: [])
    monkeypatch.setattr(trackers, "_gh_my_issues", lambda warn, *a, **k: [])
    got = trackers.list_my_issues([], force=True)
    assert got == []


def test_issue_variant_cache_keys_are_distinct(monkeypatch, tmp_path):
    """A broader selection ((window, scope) outside the initial superset)
    lands on its own key — one network round max per variant."""
    from workagent import trackers
    from workagent import store_sqlite as sq
    import workagent.store as store
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    rows = [{"key": "IPG-1", "title": "t", "url": "u", "status": "To Do",
             "created": "2025-12-01"}]
    sq.set_issue_cache(sq.db_path(), trackers._cache_key("jira", 365, True), rows)
    seen: list = []
    def _boom(warn, *a, **k):
        seen.append((a, k))
        raise AssertionError("must use variant cache")
    monkeypatch.setattr(trackers, "_jira_my_issues", _boom)
    monkeypatch.setattr(trackers, "_gh_my_issues", lambda warn, *a, **k: [])
    got = trackers.list_my_issues([], days=365, mine=True)
    assert [r["key"] for r in got] == ["IPG-1"]
    assert seen == []


def test_expired_issue_cache_serves_stale_without_fetch(monkeypatch, tmp_path):
    """Expired-but-present cache serves stale rows instantly with a
    background refresh — the API never blocks on the tracker CLI."""
    import time
    from datetime import datetime, timedelta, timezone
    from workagent import trackers
    from workagent import store_sqlite as sq
    monkeypatch.setattr(sq, "db_path", lambda: tmp_path / "state.db")
    stale_rows = [{"key": "jira:OLD-1", "title": "stale"}]
    sq.set_issue_cache(sq.db_path(), "jira:mine90", stale_rows)
    # Backdate past the 1h fresh TTL but inside the 1d SWR window.
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with sq.connect(sq.db_path()) as conn:
        conn.execute("UPDATE issue_cache SET fetched_at = ? WHERE source = ?",
                     (old, "jira:mine90"))
    live_calls: list = []
    def fake_fetch(warn):
        live_calls.append(1)
        time.sleep(0.2)
        return [{"key": "jira:NEW-1", "title": "fresh"}]
    t0 = time.monotonic()
    got = trackers._cached_source("jira", fake_fetch, [], False, "jira:mine90")
    elapsed = time.monotonic() - t0
    assert got == stale_rows  # instant, no wait for the 0.2s live fetch
    assert elapsed < 0.2
    time.sleep(0.5)  # background refresh lands
    rows, _ = sq.get_issue_cache(sq.db_path(), "jira:mine90")
    assert rows == [{"key": "jira:NEW-1", "title": "fresh"}]


def test_fresh_issue_cache_never_fetches(monkeypatch, tmp_path):
    """Fresh cache (< 1h) serves instantly with zero fetches."""
    from workagent import trackers
    from workagent import store_sqlite as sq
    monkeypatch.setattr(sq, "db_path", lambda: tmp_path / "state.db")
    sq.set_issue_cache(sq.db_path(), "jira:mine90", [{"key": "jira:A-1"}])
    def boom(warn):
        raise AssertionError("must not fetch on fresh cache")
    assert trackers._cached_source("jira", boom, [], False, "jira:mine90") == [{"key": "jira:A-1"}]
def test_initial_query_is_assigned_or_created_90d():
    """The default fetcher query covers assigned-to-me or created-by-me,
    all statuses, last 90 days."""
    from workagent import trackers
    jql = trackers._issues_jql()
    assert jql == ("(reporter = currentUser() OR assignee = currentUser()) "
                   "AND created >= -90d ORDER BY created DESC")
    assert trackers._issues_jql(365, False) == "created >= -365d ORDER BY created DESC"


def test_jql_split_tags_assignee_and_reporter():
    """Mine scope runs two origin-tagged JQL queries (assignee-me +
    reporter-me); their union still covers the old OR query's slice."""
    from workagent import trackers
    split = trackers._issues_jql_split()
    assert split == [("assigned_to_me", "assignee = currentUser() AND created >= -90d ORDER BY created DESC"),
                     ("created_by_me", "reporter = currentUser() AND created >= -90d ORDER BY created DESC")]


def test_jira_rows_carry_query_origin(monkeypatch):
    """A row from the assignee query tags assigned only; a row from both
    queries tags both (dedup keeps one row with OR-ed flags)."""
    import json
    from workagent import trackers
    def payload(key):
        return json.dumps([{"key": key, "fields": {
            "summary": "S", "status": {"name": "To Do"},
            "created": "2026-09-20T10:00:00.000+0000",
            "reporter": {"name": "someone"}, "assignee": {"name": "me"}}}])
    def fake(*a, **k):
        if a[:2] == ("jira-cli", "request"):
            jql = a[3]
            if jql.startswith("assignee"):
                return payload("IPG-1")
            return payload("IPG-1") if "reporter" in jql else "Unknown: request"
        raise HarnessError("command not found: gh")
    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = trackers._jira_my_issues([], days=90, mine=True)
    assert len(rows) == 1
    assert rows[0]["assigned_to_me"] is True and rows[0]["created_by_me"] is True


def test_gh_rows_carry_search_origin(monkeypatch):
    """Author-only match tags created; a match in both searches tags both."""
    import json
    from workagent import trackers
    mine_only = {"repository": {"nameWithOwner": "o/r"}, "number": 7,
                 "title": "T", "body": "", "author": {"login": "me"},
                 "state": "open", "url": "u", "createdAt": "2026-10-01T00:00:00Z"}
    shared = dict(mine_only, number=8)
    def fake(*a, **k):
        import json as _json
        if "--author=@me" in a:
            return _json.dumps([mine_only, shared])
        return _json.dumps([shared])
    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = {r["key"]: r for r in trackers._gh_my_issues([], days=90, mine=True)}
    assert rows["github:o/r#7"]["created_by_me"] is True
    assert rows["github:o/r#7"]["assigned_to_me"] is False
    assert rows["github:o/r#8"]["created_by_me"] is True
    assert rows["github:o/r#8"]["assigned_to_me"] is True

def test_gh_query_unions_author_and_assignee(monkeypatch):
    """The mine path unions author + assignee searches (deduped); the all
    path makes a single unqualified call."""
    from workagent import trackers
    calls = []
    payload = [{"repository": {"nameWithOwner": "o/r"}, "number": 7,
                "title": "T", "body": "", "author": {"login": "me"},
                "state": "closed", "url": "u", "createdAt": "2026-10-01T00:00:00Z"}]
    def fake(*a, **k):
        calls.append(a)
        import json as _json
        return _json.dumps(payload)
    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = trackers._gh_my_issues([], days=90, mine=True)
    assert len(calls) == 2  # author search + assignee search
    assert rows[0]["key"] == "github:o/r#7" and rows[0]["status"] == "closed"
    calls.clear()
    rows = trackers._gh_my_issues([], days=365, mine=False)
    assert len(calls) == 1  # unqualified search
    assert all("--author" not in a and "--assignee" not in a for a in calls[0])
    assert "created:>=2025-10-14" in calls[0]  # 365d back from today 2026-10-14


def test_my_issues_fetch_exception_never_raises(monkeypatch, tmp_path):
    from workagent import trackers
    import workagent.store as store
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    def _boom(warn):
        raise OSError("no such binary")
    monkeypatch.setattr(trackers, "_jira_my_issues", _boom)
    monkeypatch.setattr(trackers, "_gh_my_issues", lambda warn: [])
    warn: list = []
    assert trackers.list_my_issues(warn) == []
    assert any("jira" in w for w in warn)

def test_gh_my_issues_carries_author_and_body(monkeypatch):
    """gh rows carry author/reporter + description for client-side filters."""
    import json
    from workagent import trackers
    from workagent.errors import run_cmd as _real  # noqa: F841 (documents the seam)
    payload = json.dumps([{
        "repository": {"nameWithOwner": "o/r"}, "number": 7,
        "title": "Fix login", "body": "login breaks on retry",
        "author": {"login": "YounesRahimi"},
        "url": "https://github.com/o/r/issues/7",
        "createdAt": "2026-10-01T00:00:00Z",
        "updatedAt": "2026-10-02T00:00:00Z",
    }])
    seen = {}
    def fake(*a, **k):
        seen["fields"] = a[a.index("--json") + 1]
        return payload
    monkeypatch.setattr(trackers, "run_cmd", fake)
    rows = trackers._gh_my_issues([])
    assert "body" in seen["fields"] and "author" in seen["fields"]
    assert rows[0]["author"] == "YounesRahimi"
    assert rows[0]["reporter"] == "YounesRahimi"
    assert rows[0]["description"] == "login breaks on retry"


def test_jira_payload_carries_reporter_and_description(monkeypatch):
    """Jira payload rows carry reporter + description for client-side filters."""
    import json
    from workagent import trackers
    payload = json.dumps({"issues": [{
        "key": "IPG-9",
        "fields": {"summary": "Fix it", "status": {"name": "To Do"},
                   "created": "2026-10-01T00:00:00.000+0000",
                   "reporter": {"displayName": "Younes Rahimi"},
                   "description": "details here"},
    }]})
    rows = trackers._parse_jira_payload(payload)
    assert rows is not None and rows[0]["reporter"] == "Younes Rahimi"
    assert rows[0]["description"] == "details here"


def test_source_disabled_tracker_skips_fetch(isolated_config, monkeypatch):
    """A sole tracker with is_enabled=0 turns its source gate off (with a warning)."""
    from workagent import trackers
    monkeypatch.setattr(trackers.store, "load_trackers", lambda: {
        "jira:IPG": {"vendor": "jira", "remote_url": "https://jira/x",
                     "fetch_enabled": True, "is_enabled": False, "repos": []}})
    monkeypatch.setattr(trackers.store, "load_config", lambda: {"disabled_tracker_domains": []})
    assert trackers._source_enabled("jira") is False
    assert trackers._source_enabled("github") is True


def test_source_stays_on_while_any_tracker_usable(isolated_config, monkeypatch):
    """Disabling one tracker (jira:PAN) must not blank the source while a
    sibling (jira:IPG) is still enabled — the user's exact regression."""
    from workagent import trackers
    monkeypatch.setattr(trackers.store, "load_trackers", lambda: {
        "jira:IPG": {"vendor": "jira", "remote_url": "https://tribe.jibit.cloud/browse/IPG",
                     "fetch_enabled": True, "is_enabled": True, "repos": []},
        "jira:PAN": {"vendor": "jira", "remote_url": "https://tribe.jibit.cloud/browse/PAN",
                     "fetch_enabled": True, "is_enabled": False, "repos": []}})
    monkeypatch.setattr(trackers.store, "load_config", lambda: {"disabled_tracker_domains": []})
    warnings: list[str] = []
    assert trackers._enabled_sources(warnings) == {"jira", "github"}
    assert warnings == []


def test_source_all_disabled_warns(isolated_config, monkeypatch):
    """Every tracker of a vendor unusable → source off + one warning naming it."""
    from workagent import trackers
    monkeypatch.setattr(trackers.store, "load_trackers", lambda: {
        "jira:IPG": {"vendor": "jira", "remote_url": "https://jira/x",
                     "fetch_enabled": True, "is_enabled": False, "repos": []}})
    monkeypatch.setattr(trackers.store, "load_config", lambda: {"disabled_tracker_domains": []})
    warnings: list[str] = []
    assert trackers._enabled_sources(warnings) == {"github"}
    assert warnings == ["skipped disabled issue source(s): jira"]


def test_source_disabled_domain_skips_fetch(isolated_config, monkeypatch):
    """A tracker on a disabled domain turns its source gate off (subdomains match)."""
    from workagent import trackers
    monkeypatch.setattr(trackers.store, "load_trackers", lambda: {
        "jira:IPG": {"vendor": "jira", "remote_url": "https://tribe.jibit.cloud/browse/IPG",
                     "fetch_enabled": True, "is_enabled": True, "repos": []}})
    monkeypatch.setattr(trackers.store, "load_config", lambda: {"disabled_tracker_domains": ["jibit.cloud"]})
    assert trackers._source_enabled("jira") is False


def test_source_other_domain_stays_on(isolated_config, monkeypatch):
    """An unrelated domain leaves the gate on."""
    from workagent import trackers
    monkeypatch.setattr(trackers.store, "load_trackers", lambda: {
        "jira:IPG": {"vendor": "jira", "remote_url": "https://tribe.jibit.cloud/browse/IPG",
                     "fetch_enabled": True, "is_enabled": True, "repos": []}})
    monkeypatch.setattr(trackers.store, "load_config", lambda: {"disabled_tracker_domains": ["example.com"]})
    assert trackers._source_enabled("jira") is True

def test_disabled_domains_roundtrip(isolated_config):
    """Config accessors normalize (lowercase, dedupe, sorted), default empty."""
    from workagent import store
    assert store.load_disabled_tracker_domains() == []
    assert store.save_disabled_tracker_domains(["Example.COM ", "example.com", "sub.example.com"]) == [
        "example.com", "sub.example.com"]
    assert store.load_disabled_tracker_domains() == ["example.com", "sub.example.com"]


def test_default_repo_for_pr_url_resolves_head_worktree(isolated_config, tmp_path, monkeypatch):
    """Branch name without the PR number: only a head_ref fetch can find it."""
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("branch:feat/add-login",
                      {"branch": "feat/add-login", "worktree": str(wt),
                       "repo": "/repo/proj"})
    monkeypatch.setattr(refs, "fetch_pr_info",
                        lambda parsed, cwd=None: {"head_ref": "feat/add-login"})
    assert trackers.default_repo_for_ref("https://github.com/o/r/pull/33") == "/repo/proj"


def test_default_repo_for_pr_url_fetch_failure_falls_through(isolated_config, monkeypatch):
    def _boom(parsed, cwd=None):
        raise Exception("no auth")
    monkeypatch.setattr(refs, "fetch_pr_info", _boom)
    assert trackers.default_repo_for_ref("https://github.com/o/r/pull/33") == ""
