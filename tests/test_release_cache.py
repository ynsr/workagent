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
