from datetime import datetime, timedelta, timezone

from workagent import cli_candidates, store_sqlite as sq


def test_empty_pr_list_is_served_from_cache(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_pr_candidates_cache(db, [], source="prs")
    calls = 0

    def _live():
        nonlocal calls
        calls += 1
        return ([], [], True)

    monkeypatch.setattr(cli_candidates, "_fetch_raw_open_prs", _live)
    prs, warnings, stale = cli_candidates._candidate_prs_cached()
    assert prs == [] and stale is False and calls == 0


def test_linked_pr_filtered_on_read(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_pr_candidates_cache(
        db,
        [{"url": "https://github.com/o/r/pull/1", "title": "t", "branch": "feat/a"}],
        source="prs",
    )
    monkeypatch.setattr(
        cli_candidates,
        "_linked_sets",
        lambda: ({"https://github.com/o/r/pull/1"}, set(), set(), set(), {}),
    )
    prs, _, _ = cli_candidates._candidate_prs_cached()
    assert prs == []


def test_total_failure_preserves_existing_cache(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    sq.set_pr_candidates_cache(
        db,
        [{"url": "https://github.com/o/r/pull/7", "title": "keep"}],
        source="prs",
    )
    with sq.connect(db) as conn:
        conn.execute("UPDATE pr_candidates_cache SET fetched_at = ?", (old,))
    monkeypatch.setattr(cli_candidates, "_fetch_raw_open_prs", lambda: ([], ["gh down"], False))
    prs, warnings, stale = cli_candidates._candidate_prs_cached(force=True)
    assert len(prs) == 1
    assert "gh down" in warnings[0]
    cached, _ = sq.get_pr_candidates_cache(db)
    assert len(cached) == 1


def test_expired_beyond_swr_fetches_inline(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    sq.set_pr_candidates_cache(db, [{"url": "https://github.com/o/r/pull/1"}], source="prs")
    with sq.connect(db) as conn:
        conn.execute("UPDATE pr_candidates_cache SET fetched_at = ?", (old,))
    calls = 0

    def _live():
        nonlocal calls
        calls += 1
        return ([{"url": "https://github.com/o/r/pull/2"}], [], True)

    monkeypatch.setattr(cli_candidates, "_fetch_raw_open_prs", _live)
    prs, _, stale = cli_candidates._candidate_prs_cached(force=False)
    assert calls == 1
    assert stale is False
    assert [p["url"] for p in prs] == ["https://github.com/o/r/pull/2"]
