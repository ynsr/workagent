"""Shared helpers for split webapp tests (NOT collected: no test_ defs here)."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from workagent.webapp import (
    Registry,
    Run,
    create_app,
)


def _app(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    dist.joinpath("index.html").write_text("<html>index</html>")
    dist.joinpath("app.js").write_text("console.log(1)")
    return create_app(dist, "127.0.0.1", 3344, ["devbox.local"]), dist


@pytest.fixture
def client(tmp_path, isolated_config):
    app, _dist = _app(tmp_path)
    with TestClient(app, base_url="http://localhost:3344") as c:
        yield c


def _stub_spawn(monkeypatch):
    """Replace the real child process with a scripted fake."""

    def fake_spawn(run: Run, registry: Registry) -> None:
        def reader() -> None:
            script = FAKE_SCRIPTS.get(run.command, {})
            lines, code = script.get(tuple(run.args) or (), ([], 0))
            for ln in lines:
                run.append(ln + "\n")
            with run.lock:
                run.exit_code = code
                run.state = {0: "succeeded", 2: "needs_input"}.get(
                    code, "failed")
            registry.release_target(run)

        run.proc = object()  # non-None so cancel paths take the fake branch
        threading.Thread(target=reader, daemon=True).start()

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)


FAKE_SCRIPTS: dict[str, dict[tuple, tuple[list, int]]] = {
    "cleanup": {("IPG-1", "--dry-run"): (["cleanup: would remove"], 0)},
    "register": {("/tmp/nope-wt",): (["err"], 2)},
    "start": {},
}


def _wait_state(client, rid, states, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/runs/{rid}").json()["state"]
        if s in states:
            return s
        time.sleep(0.02)
    raise AssertionError(f"run {rid} never reached {states}: {s}")


def _register_repo(tmp_path, name="proj"):
    from workagent import store
    repo = tmp_path / name
    repo.mkdir()
    cfg = store.load_config()
    cfg["repos"] = {name: {"path": str(repo)}}
    # Same leak guard as test_cli._candidates_env: keep the real
    # ~/dev/worktrees out of candidates endpoint tests.
    empty = tmp_path / "empty-scan"
    empty.mkdir(exist_ok=True)
    cfg["scan_root"] = str(empty)
    store.save_config(cfg)
    return str(repo)


def _issue_row(days_ago, key="jira:IPG-1"):
    from datetime import datetime, timedelta, timezone

    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"key": key, "title": "T", "url": "u", "status": "To Do",
            "created": created.isoformat()}
