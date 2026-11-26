import logging
import threading

from workagent import swr


def test_is_fresh_returns_stale_without_calling_refresh():
    called = False

    def _refresh():
        nonlocal called
        called = True
        return "fresh"

    val, served_stale = swr.serve_stale_or_refresh("cached", True, _refresh, "k-fresh")
    assert val == "cached"
    assert served_stale is False
    assert called is False


def test_cli_default_does_not_spawn_background_thread():
    swr.set_background_enabled(False)
    ran = threading.Event()
    val, served_stale = swr.serve_stale_or_refresh("cached", False, lambda: ran.set(), "k-cli")
    assert val == "cached"
    assert served_stale is True
    assert not ran.wait(0.1)


def test_background_exception_is_logged(caplog):
    done = threading.Event()

    def _boom():
        try:
            raise RuntimeError("boom")
        finally:
            done.set()

    with caplog.at_level(logging.WARNING, logger="workagent.swr"):
        val, served_stale = swr.serve_stale_or_refresh(
            "cached", False, _boom, "k-boom", allow_background=True
        )
        assert done.wait(2.0)
    assert val == "cached"
    assert served_stale is True
    assert any("k-boom" in r.message and "boom" in r.message for r in caplog.records)
