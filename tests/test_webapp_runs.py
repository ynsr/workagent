"""Webapp run-args tests: argv building, session-file injection, arg validation (split from test_webapp.py)."""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from workagent import cli, refs, store, trackers
from workagent.errors import HarnessError
from workagent.webapp import (
    VAL_FLAGS,
    Registry,
    Run,
    _build_argv,
    _target_for,
    _validate_args,
    create_app,
)
from tests.webapp_helpers import (
    FAKE_SCRIPTS,
    _stub_spawn,
    _wait_state,
    client,
)

def test_start_accepts_and_forwards_launch(client, monkeypatch):
    """`start --launch` passes validation and reaches the child argv."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "start", "args": ["IPG-1", "--no-tty", "--launch"],
        "confirm": True,
    })
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    run = client.app.state.registry.get(rid)
    assert "--launch" in run.argv and "--yes" in run.argv  # server appends --yes
    assert "--session-file" in run.argv and run.session_file.endswith(".jsonl")
    _wait_state(client, rid, {"succeeded"})


def test_review_accepts_launch_shorthand_L(client, monkeypatch):
    """-L (CLI shorthand) passes web validation like --launch."""
    _stub_spawn(monkeypatch)
    r = client.post("/api/runs", json={
        "command": "review", "args": ["o/r#33", "-L", "--dry-run"],
    })
    assert r.status_code == 202, r.text


def test_start_rejects_stale_no_harness_alias(client):
    """Old `--no-harness`/`-N` are rejected with a bad_arg hint (renamed to --launch)."""
    r = client.post("/api/runs", json={
        "command": "start", "args": ["IPG-1", "--no-tty", "--no-harness"],
        "confirm": True,
    })
    assert r.status_code == 400, r.text
    body = r.json()["error"]
    assert body["code"] == "bad_arg" and "--launch" in body["message"]


def test_validate_args_allows_verbose_global():
    _validate_args("sync", ["-v", "IPG-1"])


def test_webapp_review_all_flags_and_target():
    _validate_args("review", ["--all"])
    _validate_args("review", ["--all", "--sequential", "--fix"])
    _validate_args("review", ["--all", "--fix-comments"])
    _validate_args("review", ["o/r#33", "--fix-comments"])
    assert _target_for("review", ["--all"]) == "review:all"
    assert _target_for("review", ["o/r#33"]) == "review:o/r#33"


def test_webapp_open_allowed_and_target():
    _validate_args("open", ["jira:X-1"])
    assert _target_for("open", ["jira:X-1"]) == "open:jira:X-1"


def test_webapp_cleanup_merged_target():
    assert _target_for("cleanup", ["--merged"]) == "cleanup:all"
    assert _target_for("cleanup", ["IPG-1"]) == "cleanup:IPG-1"


def test_api_default_repo_prefers_linked_worktree(client, tmp_path, monkeypatch):
    """Issue #26: /api/default-repo returns the linked-worktree repo, else single-linked."""
    from workagent import repos, trackers, store
    linked = tmp_path / "proj"
    linked.mkdir()
    trackers.check_or_record("jira:IPG", str(linked), persist=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("jira:IPG-1", {"worktree": str(wt), "branch": "b",
                                     "repo": str(linked)})
    r = client.get("/api/default-repo", params={"ref": "IPG-1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ref": "IPG-1", "repo": str(linked), "repos": [str(linked)]}
    assert client.get("/api/default-repo", params={"ref": "IPG-99"}).json() == {"ref": "IPG-99", "repo": str(linked), "repos": [str(linked)]}


def test_mirror_run_stamps_fix_metadata(client):
    """Exit-0 run reusing a review session stamps fixed_at + run id."""
    from workagent import store_sqlite as sq
    from workagent import web_runs as _wr
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k', '/tmp/w-fix1', 'b-fix1', NULL, '2026-01-01', '{}')")
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                            prompt="p", file_path="/s/rev-1.jsonl", session_id="rev-1",
                            session_type="review")
    sq.finish_session(db, "rev-1", "finished")
    run = client.app.state.registry.create("review", ["k", "--fix-comments"], "review:k")
    run.session_file = "/s/rev-1.jsonl"
    run.argv = ["python", "-m", "workagent", "review", "k", "--fix-comments",
                "--launch", "--session-file", "/s/rev-1.jsonl"]
    run.exit_code = 0
    _wr._mirror_run(run)
    md = sq.get_session(db, sid)["metadata"]
    assert md["review_comments_fixed_at"]
    assert md["fixed_by_run_id"] == run.id


def test_mirror_run_failure_leaves_fix_metadata(client):
    """Non-zero continue run leaves prior fix metadata untouched."""
    from workagent import store_sqlite as sq
    from workagent import web_runs as _wr
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k2', '/tmp/w-fix2', 'b-fix2', NULL, '2026-01-01', '{}')")
    sid = sq.insert_session(db, worktree_ref="k2", harness_name="omp", initiator_command="review",
                            prompt="p", file_path="/s/rev-9.jsonl", session_id="rev-9",
                            session_type="review",
                            metadata={"review_comments_fixed_at": "old", "fixed_by_run_id": "old-run"})
    sq.finish_session(db, "rev-9", "finished")
    run = client.app.state.registry.create("review", ["k2", "--fix-comments"], "review:k2")
    run.session_file = "/s/rev-9.jsonl"
    run.argv = ["python", "-m", "workagent", "review", "k2", "--fix-comments",
                "--launch", "--session-file", "/s/rev-9.jsonl"]
    run.exit_code = 1
    _wr._mirror_run(run)
    md = sq.get_session(db, sid)["metadata"]
    assert md == {"review_comments_fixed_at": "old", "fixed_by_run_id": "old-run"}


def test_api_review_session_returns_latest(client):
    from workagent import store_sqlite as sq
    from workagent import store as _store
    db = sq.db_path()
    sq.init_db(db)
    wt = "/tmp/wrs2"
    _store.record_link("rk2", {"worktree": wt, "branch": "branch-rk2", "repo": "/tmp/r"})
    sq.insert_session(db, worktree_ref="rk2", harness_name="omp", initiator_command="review",
                      prompt="p", file_path="/s/rev-2.jsonl", session_id="rev-2",
                      session_type="review")
    sq.finish_session(db, "rev-2", "finished")
    r = client.get("/api/review-session", params={"ref": "rk2"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ref": "rk2", "session_id": "rev-2", "file_path": "/s/rev-2.jsonl"}


def test_review_fix_comments_reuses_review_transcript(client, monkeypatch):
    """Fix-continue runs reuse the latest review transcript, not a fresh id.

    Regression: the server minted a fresh --session-file for preview
    fix-comments runs; explicit --session-file always wins in the CLI, so
    the minted file shadowed the finished review transcript and omp
    resumed an empty/new session instead of the review one.
    """
    from workagent import store_sqlite as sq
    from workagent import store as _store
    db = sq.db_path()
    sq.init_db(db)
    _store.record_link("rk-fix", {"worktree": "/tmp/w-fix", "branch": "b-fix",
                                  "repo": "/tmp/r"})
    sq.insert_session(db, worktree_ref="rk-fix", harness_name="omp",
                      initiator_command="review", prompt="p",
                      file_path="/s/review-old.jsonl", session_id="rev-old",
                      session_type="review")
    sq.finish_session(db, "rev-old", "finished")
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        seen["session_file"] = run.session_file
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    r = client.post("/api/runs", json={"command": "review",
                                       "args": ["rk-fix", "--no-tty", "--fix-comments"],
                                       "confirm": True})
    assert r.status_code == 202, r.text
    assert seen["session_file"] == "/s/review-old.jsonl"
    assert "--session-file" in seen["argv"]
    idx = seen["argv"].index("--session-file")
    assert seen["argv"][idx + 1] == "/s/review-old.jsonl"


def test_review_fix_comments_fresh_session_mints_new_file(client, monkeypatch):
    """--new-fix-session keeps a minted path (links back via metadata)."""
    from workagent import store_sqlite as sq
    from workagent import store as _store
    db = sq.db_path()
    sq.init_db(db)
    _store.record_link("rk-fresh", {"worktree": "/tmp/w-fresh", "branch": "b-fresh",
                                    "repo": "/tmp/r"})
    sq.insert_session(db, worktree_ref="rk-fresh", harness_name="omp",
                      initiator_command="review", prompt="p",
                      file_path="/s/review-prev.jsonl", session_id="rev-prev",
                      session_type="review")
    sq.finish_session(db, "rev-prev", "finished")
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        seen["session_file"] = run.session_file
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    r = client.post("/api/runs", json={"command": "review",
                                       "args": ["rk-fresh", "--no-tty", "--fix-comments",
                                                "--new-fix-session"],
                                       "confirm": True})
    assert r.status_code == 202, r.text
    assert seen["session_file"] != "/s/review-prev.jsonl"
    assert seen["session_file"].endswith(".jsonl")

def test_register_run_key_unique_per_path(client):
    r1 = client.post("/api/runs", json={"command": "register",
                                        "args": ["/tmp/wt-a"]})
    r2 = client.post("/api/runs", json={"command": "register",
                                        "args": ["/tmp/wt-b"]})
    assert r1.status_code == 202, r1.text
    assert r2.status_code == 202, r2.text
    assert r1.json()["run_id"] != r2.json()["run_id"]


def test_start_run_injects_session_file(client, monkeypatch):
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        seen["session_file"] = run.session_file
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    r = client.post("/api/runs", json={"command": "start",
                                       "args": ["IPG-1"],
                                       "confirm": True})
    assert r.status_code == 202, r.text
    assert seen["session_file"].endswith(".jsonl")
    assert "--session-file" in seen["argv"]
    detail = client.get(f"/api/runs/{r.json()['run_id']}").json()
    assert detail["session_file"] == seen["session_file"]


def test_sync_explicit_session_file_recorded(client, monkeypatch):
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    body = {"command": "sync", "args": ["k", "--session-file", "/tmp/s.jsonl"],
            "confirm": True}
    r = client.post("/api/runs", json=body)
    assert r.status_code == 202, r.text
    assert seen["argv"].count("--session-file") == 1
    detail = client.get(f"/api/runs/{r.json()['run_id']}").json()
    assert detail["session_file"] == "/tmp/s.jsonl"


def test_all_runs_skip_session_file_injection(client, monkeypatch):
    """Review/sync --all: no --session-file injected (CLI rejects it with --all)."""
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        seen["session_file"] = run.session_file
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    for command in ("review", "sync"):
        seen.clear()
        r = client.post("/api/runs", json={"command": command,
                                           "args": ["--all"],
                                           "confirm": True})
        assert r.status_code == 202, r.text
        assert "--session-file" not in seen["argv"]
        assert seen["session_file"] == ""


def test_all_with_session_file_rejected(client):
    for command in ("review", "sync"):
        r = client.post("/api/runs", json={
            "command": command,
            "args": ["--all", "--session-file", "/tmp/s.jsonl"],
            "confirm": True})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "bad_arg"


def test_review_force_all_passthrough_accepted():
    """Issue #28: --force-all passes web arg validation for review --all."""
    from workagent import webapp as _w2
    from workagent.webapp import _validate_args
    _validate_args("review", ["--all", "--force-all"])
    for cmd in ("start", "review", "cleanup", "sync", "open", "register", "repo", "link", "tracker"):
        assert cmd in _w2.SPECS, f"SPECS missing {cmd}"


def test_start_review_multi_repo_requires_explicit_repo(client, tmp_path, monkeypatch):
    """Issue #29: ambiguous tracker + no --repo → 400 repo_ambiguous (not a run)."""
    from workagent import store
    from workagent import store_sqlite as _sq
    _stub_spawn(monkeypatch)
    _sq.init_db(_sq.db_path())
    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "b").mkdir(exist_ok=True)
    wt = tmp_path / "wt-pinned"
    wt.mkdir(exist_ok=True)
    store.add_tracker_repo("jira:IPG", str(tmp_path / "a"))
    store.add_tracker_repo("jira:IPG", str(tmp_path / "b"))
    from workagent import trackers as _t
    assert len(_t.linked_repos("jira:IPG")) == 2, _t.linked_repos("jira:IPG")
    store.add_tracker_repo("github:o/r", str(tmp_path / "a"))
    store.add_tracker_repo("github:o/r", str(tmp_path / "b"))
    for command, ref in (("start", "IPG-987"), ("review", "https://github.com/o/r/pull/1")):
        r = client.post("/api/runs", json={"command": command,
                                           "args": [ref, "--dry-run"],
                                           "confirm": True})
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["error"]["code"] == "repo_ambiguous"
        assert "--repo" in body["error"]["message"]
    # Rule-1 pinned: exact key already linked to a worktree repo skips the guard.
    store.record_link("jira:IPG-987", {"branch": "feat/x", "worktree": str(wt),
                                      "repo": str(tmp_path / "a"),
                                      "added_at": "2026-01-01T00:00:00+00:00"})
    r = client.post("/api/runs", json={"command": "start",
                                       "args": ["IPG-987", "--dry-run"],
                                       "confirm": True})
    assert r.status_code == 202, r.text
    r = client.post("/api/runs", json={"command": "start",
                                       "args": ["IPG-987", "--dry-run", "--repo", str(tmp_path / "a")],
                                       "confirm": True})
    assert r.status_code == 202, r.text


def test_repo_remove_worktrees_require_force(client, tmp_path, monkeypatch):
    """repo remove over a repo with linked worktrees 400s unless force."""
    from workagent import store_sqlite as sq
    _stub_spawn(monkeypatch)
    db = sq.db_path()
    sq.register_repo_row(db, "proj", str(tmp_path / "proj"), "jira:IPG")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'proj', '2026-01-01T00:00:00+00:00')")
    r = client.post("/api/runs", json={"command": "repo",
                                       "args": ["remove", "proj"],
                                       "confirm": True})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "worktrees_exist"
    r = client.post("/api/runs", json={"command": "repo",
                                       "args": ["remove", "proj"],
                                       "confirm": True, "force": True})
    assert r.status_code == 202, r.text


def test_fetch_origins_dedupes_per_repo(tmp_path, monkeypatch):
    """_fetch_origins fetches once per distinct repo; only with refresh."""
    links = {
        "jira:A": {"worktree": str(tmp_path / "wt1"), "repo": str(tmp_path / "repo1")},
        "jira:B": {"worktree": str(tmp_path / "wt2"), "repo": str(tmp_path / "repo1")},
        "jira:C": {"worktree": str(tmp_path / "wt3"), "repo": str(tmp_path / "repo2")},
    }
    for e in links.values():
        open(e["worktree"], "w").close()
    calls = []
    monkeypatch.setattr(cli, "run_cmd",
                        lambda *a, **k: calls.append(a) or "")
    cli._fetch_origins(links, True)
    assert len([a for a in calls if "fetch" in a]) == 2  # deduped per repo
    cli._fetch_origins(links, False)
    assert len([a for a in calls if "fetch" in a]) == 2  # no fetch without refresh


def test_status_endpoint_refresh_fetches(client, isolated_config, tmp_path, monkeypatch):
    """/api/status?refresh=true triggers an origin fetch (ref path fetches
    only that repo); plain GET does not."""
    wt = tmp_path / "wt"; wt.mkdir()
    store.record_link("jira:IPG-929", {"issue": "IPG-929", "worktree": str(wt),
                                       "branch": "feat/IPG-929--x",
                                       "repo": str(tmp_path / "proj")})
    git_calls = []
    monkeypatch.setattr(cli, "run_cmd",
                        lambda *a, **k: git_calls.append(a) or "")
    r = client.get("/api/status", params={"refresh": True})
    assert r.status_code == 200, r.text
    assert [a for a in git_calls if "fetch" in a]  # fetched for the ref's repo
    git_calls.clear()
    r = client.get("/api/status")
    assert r.status_code == 200, r.text
    assert not [a for a in git_calls if "fetch" in a]  # no fetch without refresh


def test_resume_run_unknown_is_404_not_500(client):
    """POST /api/runs/<unknown>/resume -> 404 JSON (never bare 500)."""
    r = client.post("/api/runs/does-not-exist-123/resume")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found"


def test_api_default_repo_multi_linked_lists_repos(client, tmp_path):
    """Ambiguous tracker → repo "" with every linked repo in `repos`."""
    from workagent import trackers
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    trackers.check_or_record("jira:IPG", str(a), persist=True)
    trackers.check_or_record("jira:IPG", str(b), yes=True, persist=True)
    r = client.get("/api/default-repo", params={"ref": "IPG-99"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo"] == ""
    assert body["repos"] == [str(a), str(b)]


def test_terminal_stop_endpoint_kills_and_clears(client, isolated_config):
    """POST /api/terminal/stop SIGTERMs the terminal-origin lock holder."""
    import subprocess as sp
    p = sp.Popen(["sleep", "30"])
    store.record_harness_run("jira:T-9", "omp", "", origin="terminal", pid=p.pid)
    try:
        r = client.post("/api/terminal/stop", json={"worktree_ref": "jira:T-9"})
        assert r.status_code == 200, r.text
        assert r.json() == {"worktree_ref": "jira:T-9", "stopped": True}
        p.wait(timeout=10)
        assert store.active_harness("jira:T-9") is None
    finally:
        try:
            p.kill()
        except OSError:
            pass


def test_terminal_stop_endpoint_unknown_ref_is_404(client):
    r = client.post("/api/terminal/stop", json={"worktree_ref": "jira:NOPE"})
    assert r.json()["error"]["code"] == "not_found"


def test_terminal_stop_endpoint_headless_lock_is_404(client, isolated_config):
    """A headless (non-terminal) lock is never stoppable via this endpoint."""
    import subprocess as sp
    p = sp.Popen(["sleep", "30"])
    store.record_harness_run("jira:T-8", "omp", "", pid=p.pid)
    try:
        r = client.post("/api/terminal/stop", json={"worktree_ref": "jira:T-8"})
        assert r.status_code == 404, r.text
        assert p.poll() is None  # untouched
    finally:
        try:
            p.kill()
        except OSError:
            pass


def test_record_harness_run_stamps_start_ticks(isolated_config):
    """Claims record /proc start-ticks so pid reuse can't resurrect a dead agent."""
    import os
    store.record_harness_run("jira:T-7", "omp")
    rec = store.active_harness("jira:T-7")
    assert rec is not None and rec["pid"] == os.getpid()
    ticks = rec.get("start_ticks")
    assert isinstance(ticks, int) and ticks > 0


def test_live_rec_rejects_start_ticks_mismatch(isolated_config):
    """A recorded pid now owned by another process is dead for the lock."""
    import os
    store.save_harnesses_raw({"jira:T-6": {"harness": "omp",
                                           "pid": os.getpid(),
                                           "started_at": 1.0,
                                           "start_ticks": 1,
                                           "worktree": "/wt",
                                           "origin": "terminal"}})
    assert store.active_harness("jira:T-6") is None


def test_stop_terminal_run_refuses_pid_reuse(isolated_config):
    """stop_terminal_run refuses to kill a recycled pid (start-ticks mismatch)."""
    import os
    import subprocess as sp
    p = sp.Popen(["sleep", "30"])
    try:
        store.save_harnesses_raw({"jira:T-5": {"harness": "omp",
                                               "pid": p.pid,
                                               "started_at": 1.0,
                                               "start_ticks": 1,
                                               "worktree": "/wt",
                                               "origin": "terminal"}})
        assert store.stop_terminal_run("jira:T-5") is False
        assert p.poll() is None  # untouched
    finally:
        try:
            p.kill()
        except OSError:
            pass


def test_boot_reaps_dead_terminal_lock(isolated_config):
    store.save_harnesses_raw({"jira:OLD": {"harness": "omp", "pid": 999999999,
                                           "started_at": 0.0, "worktree": "/wt",
                                           "origin": "terminal"}})
    assert store.active_harness("jira:OLD") is None
def test_preview_run_pins_session_file_and_id(client, monkeypatch):
    """Preview child pins the web-minted --session-file: same id + full path.

    Regression: the first run (preview) minted a fresh session row with an
    empty file_path, so /runs/db-<id> showed no session link and future
    runs of the same session could not reuse the path.
    """
    from workagent import cli_harness as _h
    from workagent import store_sqlite as sq
    from workagent import store as _store
    db = sq.db_path()
    sq.init_db(db)
    _store.record_link("rk-pin", {"worktree": "/tmp/w-pin", "branch": "b-pin",
                                  "repo": "/tmp/r"})
    sid = "2026-10-09T09-01-45-289Z-9100"
    pinned = str(sq.session_file_path(sid, "omp"))
    got = _h.record_preview_session("rk-pin", "start", "PROMPT", "omp",
                                    "harness command: cd /tmp/w-pin && omp 'x'",
                                    session_file=pinned)
    assert got == sid
    row = sq.get_session(db, sid)
    assert row["file_path"] == pinned


def test_preview_launch_collision_adopts_existing(isolated_config, tmp_path, monkeypatch):
    """Headless rerun with a pinned --session-file adopts the preview row.

    Same PK (transcript stem) must not warn/fail — the launch continues on
    the existing session id.
    """
    from workagent import cli_harness as _h
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    sid = "2026-10-09T09-01-45-289Z-9100"
    pinned = str(sq.session_file_path(sid, "omp"))
    first = _h.record_preview_session("k", "start", "P", "omp",
                                      "harness command: cd /wt && omp 'x'",
                                      session_file=pinned)
    assert first == sid
    warned = []
    monkeypatch.setattr(_h, "eprint", lambda *a: warned.append(a))
    monkeypatch.setattr(_h.backend, "launch", lambda *a, **k: 0)
    result = {"key": "k", "command": "start", "branch": "b"}
    _h._run_harness("omp", "P", "/wt", "/r", True, True, result, True,
                    run_key="k", session_file=pinned)
    # branch "b" rescopes the pinned path under sessions/omp/b/<action>/ (same sid)
    rescoped = str(sq.session_file_path(sid, "omp", branch="b", session_type="start"))
    assert result.get("session_file") == rescoped
    assert warned == []
    assert sq.get_session(db, sid) is not None


def test_mirror_skips_preview_self_recorded_runs(client):
    """Preview/terminal children self-record their runs row: no mirror dup."""
    from workagent import store_sqlite as sq
    from workagent import web_runs as _wr
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k3', '/tmp/w3', 'b3', NULL, '2026-01-01', '{}')")
    sid = sq.insert_session(db, worktree_ref="k3", harness_name="omp",
                            initiator_command="start", prompt="p",
                            file_path="/s/prev.jsonl", session_id="prev-3",
                            session_type="start")
    sq.finish_session(db, "prev-3", "preview")
    before = len(sq.list_runs(db))
    run = client.app.state.registry.create("start", ["k3"], "start:k3")
    run.session_file = "/s/prev.jsonl"
    run.argv = ["python", "-m", "workagent", "start", "k3", "--yes",
                "--session-file", "/s/prev.jsonl"]
    run.exit_code = 0
    _wr._mirror_run(run)
    assert len(sq.list_runs(db)) == before


def test_resolve_session_file_adopts_minted(client, tmp_path, monkeypatch):
    """Pinned-but-missing path adopts the omp-minted .jsonl and persists it."""
    from workagent import store_sqlite as sq
    from workagent import web_runs_routes as _rr
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
                     " VALUES ('k4', '/tmp/w4', 'b4', NULL, '2026-01-01', '{}')")
    sid = "2026-10-09T09-01-45-289Z-9100"
    pinned = str(tmp_path / f"{sid}.jsonl")
    sq.insert_session(db, worktree_ref="k4", harness_name="omp",
                      initiator_command="start", prompt="p", file_path=pinned,
                      session_id=sid, session_type="start")
    sq.finish_session(db, sid, "preview")
    minted = tmp_path / "2026-10-09T09-01-47-227Z_abc.jsonl"
    minted.write_text('{"type":"session"}\n')
    got = _rr._resolve_session_file(pinned, sid)
    assert got == str(minted)
    assert sq.get_session(db, sid)["file_path"] == str(minted)
def test_launch_rescopes_branchless_pinned_path(isolated_config, tmp_path, monkeypatch):
    """Review (branch known only after MR fetch) rescopes the web-minted path.

    The web server mints --session-file without the branch slug; the CLI
    child knows the branch at launch. The scope must move under
    sessions/omp/<branch-slug>/ while the session id (transcript stem) stays
    the same — so the transcript layout matches the start flow.
    """
    from workagent import cli_harness as _h
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO trackers (key_ref, vendor, remote_url) VALUES ('t', 'unknown', 't')")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/r', 'r')")
        conn.execute("INSERT INTO tracker_repos (tracker_key, repo_key) VALUES ('t', 'r')")
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k5', '/wt5', 'feat/my-branch', 'r', '2026-01-01T00:00:00+00:00')")
    sid = "2026-10-09T11-24-01-825Z-4557"
    pinned = str(sq.session_file_path(sid, "omp", session_type="review"))  # branch-less, as the web mints it
    assert "/feat/my-branch/" not in pinned
    monkeypatch.setattr(_h.backend, "launch", lambda *a, **k: 0)
    result = {"key": "k5", "command": "review", "branch": "feat/my-branch"}
    _h._run_harness("omp", "P", "/wt5", "/r", True, True, result, True,
                    run_key="k5", session_file=pinned)
    rescoped = str(sq.session_file_path(sid, "omp", branch="feat/my-branch", session_type="review"))
    assert result.get("session_file") == rescoped
    assert "/feat/my-branch/" in rescoped
    row = sq.get_session(db, sid)
    assert row is not None and row["file_path"] == rescoped
def test_preview_command_carries_branch_scoped_session_dir(isolated_config, monkeypatch):
    """Printed/copied Review command is branch-scoped from the very first run.

    The user runs the printed command manually (or via Run-in-terminal), so
    the --session-dir in the preview text itself must carry the branch slug
    — not just the launch path the web child never reaches.
    """
    from workagent import cli_harness as _h
    from workagent import store_sqlite as sq
    from workagent import store as _store
    db = sq.db_path()
    sq.init_db(db)
    _store.record_link("rk-prev", {"worktree": "/tmp/w-prev", "branch": "feat/my-branch",
                                   "repo": "/tmp/r"})
    sid = "2026-10-09T11-24-01-825Z-4557"
    pinned = str(sq.session_file_path(sid, "omp", session_type="review"))  # branch-less, as the web mints it
    result = {"key": "rk-prev", "command": "review", "branch": "feat/my-branch"}
    _h._run_harness("omp", "PROMPT", "/tmp/w-prev", "/tmp/r", False, False,
                    result, True, run_key="rk-prev", session_file=pinned)
    rescoped = str(sq.session_file_path(sid, "omp", branch="feat/my-branch", session_type="review"))
    assert result.get("harness_command", "").find(f"--session-dir {rescoped.rsplit('/', 1)[0]}") >= 0         or f"--session-dir '{rescoped.rsplit('/', 1)[0]}'" in result.get("harness_command", "")
    row = sq.get_session(db, sid)
    assert row is not None and row["file_path"] == rescoped
