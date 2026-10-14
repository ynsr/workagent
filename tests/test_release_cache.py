# tests/test_release_cache.py
from workagent import store_sqlite as sq

def test_release_columns_and_cache_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "rx", "/r/rx", "jira:RX", remote="https://x/rx.git", tool="gh")
    assert sq.get_release_diff_cache(db, "rx") == (None, "")
    sq.set_release_diff_cache(db, "rx", 7)
    count, fetched = sq.get_release_diff_cache(db, "rx")
    assert count == 7 and fetched != ""
    cols = {r[1] for r in sq.connect(db).execute("PRAGMA table_info(repos)")}
    assert {"default_branch", "release_branch"} <= cols
def test_detect_release_branch_prefers_master_on_gitlab(tmp_path, monkeypatch):
    from workagent import repos as _repos
    monkeypatch.setattr(_repos, "_detect_host_cli", lambda p: "glab")
    def _fake(*a, **k):
        if "--verify" in a and "master" in " ".join(a):
            return "sha"
        raise Exception("no ref")
    monkeypatch.setattr(_repos, "run_cmd", _fake)
    assert _repos.detect_release_branch(tmp_path) == "master"


def test_register_repo_seeds_branches(tmp_path, monkeypatch):
    from workagent import repos as _repos
    from workagent import store_sqlite as _sq
    db = tmp_path / "state.db"
    _sq.init_db(db)
    monkeypatch.setattr(_sq, "db_path", lambda: db)
    monkeypatch.setattr(_repos, "default_branch", lambda p: "main")
    monkeypatch.setattr(_repos, "detect_release_branch", lambda p: "master")
    monkeypatch.setattr(_repos, "remote_url", lambda p: "https://h/r.git")
    monkeypatch.setattr(_repos, "_detect_host_cli", lambda p: "glab")
    _repos.register_repo("rx", tmp_path, tracker_key="jira:RX")
    rows = {r[1] for r in _sq.connect(db).execute("PRAGMA table_info(repos)")}
    assert {"default_branch", "release_branch"} <= rows
def test_release_status_serves_stale_without_fetch(tmp_path, monkeypatch):
    from workagent import repos as _repos
    from workagent import store_sqlite as _sq
    from workagent import store as _store
    db = tmp_path / "state.db"
    _sq.init_db(db)
    monkeypatch.setattr(_sq, "db_path", lambda: db)
    _sq.register_repo_row(db, "rx", "/r/rx", "jira:RX")
    with _sq.connect(db) as _c:
        _c.execute("UPDATE repos SET default_branch='main', release_branch='master' WHERE key_ref='rx'")
        _c.execute("INSERT INTO release_diff_cache VALUES ('rx', 4, '2020-01-01T00:00:00+00:00')")
    def _boom(*a, **k):
        raise AssertionError("must not fetch")
    monkeypatch.setattr(_repos, "release_diff_count", _boom)
    monkeypatch.setattr(_store, "load_repos", lambda: {"rx": {"path": "/r/rx", "default_branch": "main", "release_branch": "master"}})
    st = _repos.release_status("rx")
    assert st["diff_count"] == 4 and st["stale"] is True and st["releasable"] is True
