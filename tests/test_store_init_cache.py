import sqlite3
import pytest

from workagent import store_sqlite as sq


def test_init_db_skips_schema_current_on_warm_cache(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    sq.init_db(db)
    calls = 0
    orig = sq._schema_current

    def _spy(p):
        nonlocal calls
        calls += 1
        return orig(p)

    monkeypatch.setattr(sq, "_schema_current", _spy)
    sq.init_db(db)
    sq.init_db(db)
    assert calls == 0


def test_init_db_rechecks_when_db_file_replaced(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)
    db.unlink()
    sqlite3.connect(str(db)).close()  # new empty file (new inode)
    sq.init_db(db)
    with sq.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pr_cache" in tables


def test_init_db_raises_operational_error_not_typeerror_on_lock(tmp_path, monkeypatch):
    db = tmp_path / "locked.db"

    def _locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sq, "connect", _locked)
    monkeypatch.setattr(sq, "_schema_current", lambda _p: False)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        sq.init_db(db)
