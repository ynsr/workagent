from workagent import store, store_sqlite as sq, watcher


def test_watcher_reads_repo_scoped_pr_cache_and_nested_pr_url(isolated_config):
    sq.init_db(sq.db_path())
    store.cache_pr_status(
        "feat/x",
        {"number": 42, "state": "open", "url": "https://github.com/o/r/pull/42"},
        tool="gh",
        repo="/repo/a",
    )
    entry = store.lookup_pr_cache("feat/x", "/repo/a")
    assert watcher._cached_pr_url(entry) == "https://github.com/o/r/pull/42"


def test_prune_orphan_pr_cache_removes_unlinked_entries(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    store.cache_pr_status("orphan-br", {"number": 1}, repo="/repo/gone")
    deleted = sq.prune_orphan_pr_cache(db)
    assert deleted == 1
    assert store.lookup_pr_cache("orphan-br", "/repo/gone") is None
