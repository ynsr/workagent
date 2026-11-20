"""Restart ordering (issue #81): serve binds before slow startup work runs."""
from __future__ import annotations


def test_run_server_defers_gc_and_backfill(isolated_config, tmp_path, monkeypatch):
    from workagent import webapp as _web
    from workagent.store_sqlite import _INIT_CACHE

    calls: list[str] = []
    monkeypatch.setattr(_web, "_port_free", lambda host, port: True)
    monkeypatch.setattr(_web, "create_app", lambda *a, **k: "app")
    monkeypatch.setattr(_web, "start_background_maintenance",
                        lambda: calls.append("bg") or None)
    # No lingering backfill thread: it would race later tests' _INIT_CACHE.
    monkeypatch.setattr("workagent.store_sqlite.backfill_async", lambda: None)

    import uvicorn

    seen: dict = {}

    class _Cfg:
        def __init__(self, app, host=None, port=None, log_level=None):
            seen.update(app=app, host=host, port=port)

    class _Server:
        def __init__(self, config):
            seen["config"] = config
            self.handle_exit = None

        def run(self):
            seen["ran"] = True

    monkeypatch.setattr(uvicorn, "Config", _Cfg)
    monkeypatch.setattr(uvicorn, "Server", _Server)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("x")

    _INIT_CACHE.pop("backfill", None)
    _web.run_server("127.0.0.1", 3344, dist, [])
    assert calls == ["bg"]  # slow GC/prune/backfill runs after bind-capable return path
    assert seen.get("ran") is True


def test_startup_maintenance_runs_gc_and_prune(isolated_config, monkeypatch):
    from workagent import webapp as _web

    order: list[str] = []
    seen: dict = {}
    def _init_db(path, **kwargs):
        seen.update(kwargs)
        order.append("migrate")
        return path
    monkeypatch.setattr("workagent.store_sqlite.init_db", _init_db)
    monkeypatch.setattr("workagent.cli_cleanup.gc_soft_deleted",
                        lambda *a, **k: order.append("gc") or [{"key": "k"}])
    monkeypatch.setattr("workagent.cli_cleanup.purge_orphans",
                        lambda *a, **k: order.append("orphans") or
                        {"runs": 1, "transcripts": ["/t.jsonl"]})
    monkeypatch.setattr("workagent.run_store.prune_finished",
                        lambda *a, **k: order.append("prune") or 2)

    out = _web.run_startup_maintenance()
    assert order == ["migrate", "gc", "orphans", "prune"]
    assert "defer_backfill" not in seen  # migrations stay synchronous
    assert out == {"purged": [{"key": "k"}],
                   "orphans": {"runs": 1, "transcripts": ["/t.jsonl"]},
                   "pruned": 2}

def test_startup_maintenance_never_raises_on_migration_failure(
    isolated_config, monkeypatch, capsys
):
    from workagent import webapp as _web

    def _boom(path, **kwargs):
        raise RuntimeError("migration exploded")
    monkeypatch.setattr("workagent.store_sqlite.init_db", _boom)
    monkeypatch.setattr("workagent.cli_cleanup.gc_soft_deleted",
                        lambda *a, **k: [])
    monkeypatch.setattr("workagent.run_store.prune_finished",
                        lambda *a, **k: 0)

    out = _web.run_startup_maintenance()
    assert out == {"purged": [],
                   "orphans": {"runs": 0, "flows": 0, "messages": 0,
                               "parent_links": 0, "transcripts": []},
                   "pruned": 0}
    assert "migration failed" in capsys.readouterr().err

def test_init_db_defer_backfill_skips_inline_git(isolated_config, monkeypatch):
    from workagent import store_sqlite as _sq
    _db_path = _sq.db_path

    _sq._INIT_CACHE.pop("backfill", None)
    _sq._INIT_CACHE.pop("backfill:started", None)
    _sq._INIT_CACHE.pop(str(_db_path()), None)
    calls: list[str] = []
    monkeypatch.setattr(_sq, "_run_backfill",
                        lambda: calls.append("sync") or None)
    monkeypatch.setattr(_sq, "backfill_async",
                        lambda: calls.append("async") or None)
    _sq.init_db(_db_path(), defer_backfill=True)
    assert calls == ["async"]


def _stub_run_server(monkeypatch, tmp_path):
    from workagent import webapp as _web
    from workagent.store_sqlite import _INIT_CACHE
    import uvicorn

    monkeypatch.setattr(_web, "_port_free", lambda host, port: True)
    monkeypatch.setattr(_web, "create_app", lambda *a, **k: "app")
    monkeypatch.setattr(_web, "start_background_maintenance", lambda: None)
    monkeypatch.setattr("workagent.store_sqlite.backfill_async", lambda: None)

    class _Cfg:
        def __init__(self, *a, **k):
            pass

    class _Server:
        def __init__(self, config):
            self.handle_exit = None

        def run(self):
            pass

    monkeypatch.setattr(uvicorn, "Config", _Cfg)
    monkeypatch.setattr(uvicorn, "Server", _Server)
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("x")
    _INIT_CACHE.pop("backfill", None)
    return _web, dist


def test_run_server_marks_service_under_systemd_without_marker(
    isolated_config, tmp_path, monkeypatch
):
    """Legacy workagent.service units (pre-#121) set NO_COLOR=1 without
    WORKAGENT_SERVICE=1; run_server must still mark WORKAGENT_SERVICE=1 when
    launched under systemd (INVOCATION_ID present) so spawned terminals drop
    NO_COLOR."""
    import os
    _web, dist = _stub_run_server(monkeypatch, tmp_path)
    monkeypatch.setenv("INVOCATION_ID", "deadbeef")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("WORKAGENT_SERVICE", raising=False)

    _web.run_server("127.0.0.1", 3344, dist, [])
    assert os.environ.get("WORKAGENT_SERVICE") == "1"


def test_run_server_skips_service_marker_outside_systemd(
    isolated_config, tmp_path, monkeypatch
):
    import os
    _web, dist = _stub_run_server(monkeypatch, tmp_path)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("WORKAGENT_SERVICE", raising=False)

    _web.run_server("127.0.0.1", 3344, dist, [])
    assert "WORKAGENT_SERVICE" not in os.environ



