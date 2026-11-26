from datetime import datetime, timedelta, timezone

from workagent import store_sqlite as sq, trackers


def test_empty_issue_list_is_cached(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira:mine90", [])
    calls = 0

    def _fetch(w):
        nonlocal calls
        calls += 1
        return []

    warn: list[str] = []
    rows = trackers._cached_source("jira", _fetch, warn, force=False, cache_key="jira:mine90")
    assert rows == [] and calls == 0


def test_upstream_failure_does_not_overwrite_issue_cache(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira:mine90", [{"key": "IPG-1"}])

    def _fail(w):
        w.append("jira timeout")
        return []

    warn: list[str] = []
    rows = trackers._cached_source("jira", _fail, warn, force=True, cache_key="jira:mine90")
    assert rows == [{"key": "IPG-1"}]
    assert "jira timeout" in warn
    cached, _ = sq.get_issue_cache(db, "jira:mine90")
    assert cached == [{"key": "IPG-1"}]


def test_set_issue_cache_prunes_rows_older_than_max_age(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    sq.set_issue_cache(db, "jira:old365", [{"key": "OLD-1"}])
    with sq.connect(db) as conn:
        conn.execute("UPDATE issue_cache SET fetched_at = ? WHERE source = ?", (old, "jira:old365"))
    sq.set_issue_cache(db, "jira:mine90", [{"key": "NEW-1"}], max_age_seconds=86400)
    old_rows, old_ts = sq.get_issue_cache(db, "jira:old365")
    assert old_rows == [] and old_ts is None


def test_tracker_upsert_and_delete_clear_issue_cache(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira:mine90", [{"key": "IPG-1"}])
    sq.upsert_tracker(db, "jira:IPG", vendor="jira", remote_url="https://jira.example.com", is_enabled=False)
    rows, ts = sq.get_issue_cache(db, "jira:mine90")
    assert rows == [] and ts is None

    sq.set_issue_cache(db, "jira:mine90", [{"key": "IPG-2"}])
    assert sq.delete_tracker(db, "jira:IPG") is True
    rows2, ts2 = sq.get_issue_cache(db, "jira:mine90")
    assert rows2 == [] and ts2 is None
