"""Tests for SQLite CDC poller (src/workagent/cdc.py) and GET /api/events SSE."""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from workagent import store_sqlite as sq
from workagent.cdc import CdcHub
from tests.webapp_helpers import _app


def test_cdc_hub_poll_broadcast_and_clean_shutdown(tmp_path):
    db = tmp_path / "state.db"
    sq.init_db(db)

    hub = CdcHub(lambda: db, poll_interval=0.02, prune_interval=60.0)
    loop = asyncio.new_event_loop()
    try:
        q = hub.subscribe(loop)
        hub.start()
        assert hub.running
        # Wait for initial cursor sync
        time.sleep(0.05)

        with sq.connect(db) as conn:
            conn.execute(
                "INSERT INTO prodtest_items (issue_key, updated_at) VALUES (?, ?)",
                ("IPG-42", "2026-01-01T00:00:00Z"),
            )

        async def _recv():
            return await asyncio.wait_for(q.get(), timeout=2.0)

        ev = loop.run_until_complete(_recv())
        assert ev is not None
        assert ev["table"] == "prodtest_items"
        assert ev["row_key"] == "IPG-42"
        assert ev["op"] == "insert"
        assert ev["seq"] >= 1

        # Stop pushes None sentinel and stops thread cleanly
        hub.stop(timeout=2.0)
        assert not hub.running
        sentinel = loop.run_until_complete(_recv())
        assert sentinel is None
    finally:
        hub.stop()
        loop.close()


def test_api_events_replay_and_shutdown(tmp_path, isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO messages (message_id, added_at) VALUES (?, ?)",
            ("m-100", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO messages (message_id, added_at) VALUES (?, ?)",
            ("m-101", "2026-01-01T00:00:00Z"),
        )
    events = sq.read_change_log(db, after_seq=0)
    assert len(events) == 2
    first_seq = events[0]["seq"]

    app, _ = _app(tmp_path)
    app.state.cdc_hub.heartbeat_interval = 0.05

    with TestClient(app) as c:
        # Stop hub shortly after stream starts so TestClient exits cleanly
        def _stop_soon():
            time.sleep(0.15)
            app.state.cdc_hub.stop()

        t = threading.Thread(target=_stop_soon, daemon=True)
        t.start()

        with c.stream(
            "GET",
            "/api/events",
            headers={"Host": "localhost", "Last-Event-ID": str(first_seq)},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            body = "".join(resp.iter_text())
        t.join(timeout=2.0)

    assert f"id: {events[1]['seq']}" in body
    assert "event: change" in body
    assert '"row_key": "m-101"' in body
    assert ": heartbeat" in body
    assert f'"row_key": "m-100"' not in body
