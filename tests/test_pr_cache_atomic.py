import threading

from workagent import store, store_sqlite as sq


def test_concurrent_pr_cache_writes_do_not_lose_updates(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    errors: list[Exception] = []

    def _worker(i: int) -> None:
        try:
            store.cache_pr_status(f"feat/branch-{i}", {"number": i, "state": "open"}, repo="/repo/a")
            store.cache_ci_status(f"feat/branch-{i}", "success", sha=f"sha{i}", repo="/repo/a")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for i in range(12):
        hit = store.lookup_pr_cache(f"feat/branch-{i}", "/repo/a")
        assert hit is not None
        assert hit["pr"]["number"] == i
        assert hit["ci"] == "success"


def test_single_row_update_emits_one_cdc_event_not_full_table(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    for i in range(5):
        store.cache_pr_status(f"b-{i}", {"number": i, "state": "open"}, repo="/repo/a")
    with sq.connect(db) as conn:
        before = conn.execute("SELECT COUNT(*) FROM change_log WHERE table_name = 'pr_cache'").fetchone()[0]
    store.cache_ci_status("b-0", "failure", sha="abc", repo="/repo/a")
    with sq.connect(db) as conn:
        after = conn.execute("SELECT COUNT(*) FROM change_log WHERE table_name = 'pr_cache'").fetchone()[0]
    assert after - before == 1


def test_ci_only_write_does_not_fabricate_pr_checked_at(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    store.cache_ci_status("brand-new", "running", sha="abc", repo="/repo/a")
    hit = store.lookup_pr_cache("brand-new", "/repo/a")
    assert hit is not None
    assert hit["ci"] == "running"
    assert hit.get("checked_at", "") == ""


def test_lookup_pr_cache_uses_single_row_read_not_full_table(isolated_config, monkeypatch):
    db = sq.db_path()
    sq.init_db(db)
    store.cache_pr_status("feat/one", {"number": 7, "state": "open"}, repo="/repo/a")
    monkeypatch.setattr(store, "load_pr_cache", lambda: (_ for _ in ()).throw(AssertionError("full table scan")))
    hit = store.lookup_pr_cache("feat/one", "/repo/a")
    assert hit is not None
    assert hit["pr"]["number"] == 7


def test_delete_pr_cache_entry_removes_repo_and_bare_keys(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    store.cache_pr_status("feat/del", {"number": 1}, repo="/repo/a")
    store.cache_pr_status("feat/del", {"number": 2}, repo="")
    deleted = store.delete_pr_cache_entry("feat/del", "/repo/a")
    assert deleted == 2
    assert store.lookup_pr_cache("feat/del", "/repo/a") is None
    assert store.lookup_pr_cache("feat/del", "") is None
