"""Tests for closed-loop PR & CI auto-watcher (issue #154 / T11)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from workagent import store, store_sqlite, store_timeline, watcher, webapp


def _seed_worktree(
    tmp_path: Path,
    *,
    repo_name: str = "demo",
    ref_key: str = "gh:demo#1",
    branch: str = "feat/demo",
    auto_fix_ci: bool = False,
    auto_fix_reviews: bool = False,
    wt_auto_fix_ci: bool | None = None,
    wt_auto_fix_reviews: bool | None = None,
    head_sha: str = "abc1234",
    ci_state: str = "",
) -> Path:
    db = store_sqlite.db_path()
    store_sqlite.init_db(db)
    repo_dir = tmp_path / repo_name
    wt_dir = tmp_path / f"{repo_name}-wt"
    repo_dir.mkdir(parents=True, exist_ok=True)
    wt_dir.mkdir(parents=True, exist_ok=True)
    store_sqlite.register_repo_row(
        db,
        repo_name,
        str(repo_dir),
        f"github:acme/{repo_name}",
        auto_fix_ci=auto_fix_ci,
        auto_fix_reviews=auto_fix_reviews,
    )
    entry: dict = {
        "worktree": str(wt_dir),
        "branch": branch,
        "repo": str(repo_dir),
        "pr_url": f"https://github.com/acme/{repo_name}/pull/1",
    }
    if wt_auto_fix_ci is not None:
        entry["auto_fix_ci"] = wt_auto_fix_ci
    if wt_auto_fix_reviews is not None:
        entry["auto_fix_reviews"] = wt_auto_fix_reviews
    store.record_link(ref_key, entry)
    store.cache_pr_status(
        branch,
        pr={"number": 1, "url": entry["pr_url"], "state": "open"},
        tool="gh",
        base_branch="main",
        branch_tip=head_sha,
        base_tip="0000000",
        behind=0,
        ahead=1,
    )
    return db


def test_compute_nudge_signature_is_deterministic_and_order_independent():
    s1 = watcher.compute_nudge_signature("deadbeef", "failure", ["t2", "t1"])
    s2 = watcher.compute_nudge_signature("deadbeef", "FAILURE", ["t1", "t2"])
    s3 = watcher.compute_nudge_signature("deadbeef", "success", ["t1", "t2"])
    assert s1 == s2
    assert len(s1) == 64
    assert s1 != s3


def test_compute_nudge_signature_distinguishes_failing_check_sets():
    base = watcher.compute_nudge_signature("deadbeef", "failure", [], ["lint"])
    changed = watcher.compute_nudge_signature("deadbeef", "failure", [], ["unit"])
    assert base != changed
    # Order of failing names does not matter; conclusions do.
    reordered = watcher.compute_nudge_signature(
        "deadbeef", "failure", [], ["unit", "lint"]
    )
    ab = watcher.compute_nudge_signature("deadbeef", "failure", [], ["lint", "unit"])
    assert reordered == ab
    concl = watcher.compute_nudge_signature(
        "deadbeef", "failure", [],
        [{"name": "lint", "conclusion": "failure"}],
    )
    assert concl == watcher.compute_nudge_signature("deadbeef", "failure", [], ["lint:failure"])
    assert concl != watcher.compute_nudge_signature("deadbeef", "failure", [], ["lint:success"])


def test_ci_failure_dispatch_dedup_and_nudge_event(tmp_path):
    db = _seed_worktree(tmp_path, auto_fix_ci=True, ci_state="failure")
    store_timeline.replace_pr_checks(
        db,
        "gh:demo#1",
        [
            {
                "name": "pytest",
                "commit_sha": "abc1234",
                "status": "completed",
                "conclusion": "failure",
                "url": "https://ci.example/1",
                "log_tail": "AssertionError: boom",
            }
        ],
    )
    dispatched: list[tuple[str, str, list[str]]] = []

    def _fake_dispatch(ref_key, kind, argv, _entry):
        dispatched.append((ref_key, kind, argv))

    res1 = watcher.run_watch_once(refresh=False, dispatch_fn=_fake_dispatch)
    assert res1["dispatched"] == 1
    assert len(dispatched) == 1
    ref_key, kind, argv = dispatched[0]
    assert ref_key == "gh:demo#1"
    assert kind == "ci_failure"
    assert "adhoc" in argv
    assert any("AssertionError: boom" in a for a in argv)

    # Event recorded in worktree_events
    events = store_timeline.list_worktree_events(db, "gh:demo#1")
    assert any(e["event_type"] == "nudge_routed" and e["details"]["kind"] == "ci_failure" for e in events)

    # Second pass with identical state is deduplicated via last_nudge_signature
    res2 = watcher.run_watch_once(refresh=False, dispatch_fn=_fake_dispatch)
    assert res2["dispatched"] == 0
    assert res2["results"][0]["reason"] == "already_nudged"
    assert len(dispatched) == 1


def test_review_comment_dispatch_and_worktree_override(tmp_path):
    # Repo has auto_fix_reviews=False, worktree overrides to True
    db = _seed_worktree(
        tmp_path,
        auto_fix_ci=False,
        auto_fix_reviews=False,
        wt_auto_fix_reviews=True,
        ci_state="success",
    )
    store_timeline.replace_pr_review_threads(
        db,
        "gh:demo#1",
        [
            {
                "thread_id": "thr-42",
                "author": "alice",
                "file": "app.py",
                "line": 10,
                "body": "Please rename this variable.",
                "resolved": False,
                "is_bot": False,
            }
        ],
    )
    dispatched: list[tuple[str, str, list[str]]] = []

    res = watcher.run_watch_once(
        refresh=False,
        dispatch_fn=lambda k, kind, argv, _e: dispatched.append((k, kind, argv))
    )
    assert res["dispatched"] == 1
    assert dispatched[0][1] == "review_comments"
    assert "review" in dispatched[0][2]
    assert "--fix-comments" in dispatched[0][2]


def test_lock_skip_and_paused_flow_skip(tmp_path, monkeypatch):
    db = _seed_worktree(tmp_path, auto_fix_ci=True, ci_state="failure")
    store_timeline.replace_pr_checks(
        db,
        "gh:demo#1",
        [{"name": "lint", "status": "completed", "conclusion": "failure"}],
    )
    dispatched: list = []

    # Active harness lock skips
    monkeypatch.setattr(store, "active_harness", lambda _k: {"harness": "omp", "pid": 99999})
    res_lock = watcher.run_watch_once(refresh=False, dispatch_fn=lambda *a: dispatched.append(a))
    assert res_lock["dispatched"] == 0
    assert res_lock["results"][0]["reason"] == "active_harness"

    # Paused flow skips
    monkeypatch.setattr(store, "active_harness", lambda _k: None)
    monkeypatch.setattr(store, "list_flows", lambda worktree="": [{"id": 1, "status": "paused"}])
    res_flow = watcher.run_watch_once(refresh=False, dispatch_fn=lambda *a: dispatched.append(a))
    assert res_flow["dispatched"] == 0
    assert res_flow["results"][0]["reason"] == "paused_flow"
    assert dispatched == []


def test_dry_run_does_not_dispatch_or_persist_signature(tmp_path):
    db = _seed_worktree(tmp_path, auto_fix_ci=True, ci_state="failure")
    store_timeline.replace_pr_checks(
        db,
        "gh:demo#1",
        [{"name": "unit", "status": "completed", "conclusion": "failure"}],
    )
    dispatched: list = []
    res = watcher.run_watch_once(refresh=False, dry_run=True, dispatch_fn=lambda *a: dispatched.append(a))
    assert res["dispatched"] == 0
    assert res["results"][0]["action"] == "dry_run"
    assert res["results"][0]["kind"] == "ci_failure"
    assert dispatched == []
    # Signature not persisted on dry-run
    links = store.load_links()
    assert not links["gh:demo#1"].get("last_nudge_signature")

def test_refresh_updates_snapshots_before_deciding(tmp_path):
    _seed_worktree(tmp_path, auto_fix_ci=True, ci_state="")

    def _fake_refresh(ref_key, entry, branch, cached, head_sha):
        store.cache_ci_status(branch, "failure", sha=head_sha or "abc1234",
                              ci_url="https://ci.example/9",
                              repo=str(entry.get("repo") or ""))
        store.replace_pr_checks(ref_key, [
            {"name": "lint", "status": "completed", "conclusion": "failure"},
        ])
        store.cache_review_stats(branch, {"reviews": 0, "unresolved": 0, "resolved": 0},
                                 sha=head_sha or "abc1234",
                                 repo=str(entry.get("repo") or ""))
        store.replace_pr_review_threads(ref_key, [])
        stats = {"reviews": 0, "unresolved": 0, "resolved": 0}
        return ("failure", "https://ci.example/9",
                [{"name": "lint", "status": "completed", "conclusion": "failure"}],
                stats, [])

    dispatched: list = []
    res = watcher.run_watch_once(
        refresh_fn=_fake_refresh,
        dispatch_fn=lambda *a: dispatched.append(a),
    )
    assert res["dispatched"] == 1
    assert res["results"][0]["kind"] == "ci_failure"
    assert res["results"][0]["stale_inputs"] is False


def test_refresh_failure_falls_back_to_last_known_snapshots(tmp_path):
    _seed_worktree(tmp_path, auto_fix_ci=True, ci_state="failure")

    def _dead_refresh(*_a):
        return (None, "", [], None, [])

    dispatched: list = []
    res = watcher.run_watch_once(
        refresh_fn=_dead_refresh, dispatch_fn=lambda *a: dispatched.append(a),
    )
    assert res["dispatched"] == 0
    assert res["results"][0]["reason"] == "clean"
    assert res["results"][0]["stale_inputs"] is True


def test_changed_failing_set_redispatches_on_same_sha(tmp_path):
    db = _seed_worktree(tmp_path, auto_fix_ci=True, ci_state="failure")
    store_timeline.replace_pr_checks(
        db, "gh:demo#1",
        [{"name": "lint", "status": "completed", "conclusion": "failure"}],
    )
    dispatched: list = []
    res1 = watcher.run_watch_once(refresh=False, dispatch_fn=lambda *a: dispatched.append(a))
    assert res1["dispatched"] == 1
    # Same head SHA, different failing check -> new signature -> dispatch again.
    store_timeline.replace_pr_checks(
        db, "gh:demo#1",
        [{"name": "unit", "status": "completed", "conclusion": "failure"}],
    )
    res2 = watcher.run_watch_once(refresh=False, dispatch_fn=lambda *a: dispatched.append(a))
    assert res2["dispatched"] == 1
    assert len(dispatched) == 2


def test_dispatch_failure_records_error_and_keeps_signature_clear(tmp_path):
    db = _seed_worktree(tmp_path, auto_fix_ci=True, ci_state="failure")
    store_timeline.replace_pr_checks(
        db, "gh:demo#1",
        [{"name": "lint", "status": "completed", "conclusion": "failure"}],
    )

    def _boom(*_a):
        raise RuntimeError("spawn exploded")

    res = watcher.run_watch_once(refresh=False, dispatch_fn=_boom)
    assert res["dispatched"] == 0
    assert res["errors"] == 1
    assert res["results"][0]["action"] == "error"
    links = store.load_links()
    assert not links["gh:demo#1"].get("last_nudge_signature")
    events = store_timeline.list_worktree_events(db, "gh:demo#1")
    assert any(e["event_type"] == "nudge_failed" for e in events)



def test_webapp_lifespan_watcher_starts_and_cancels_cleanly(tmp_path, monkeypatch):
    calls: list[int] = []

    def _fake_pass(**_kwargs):
        calls.append(1)
        return {"checked": 0, "dispatched": 0, "skipped": 0, "results": []}

    monkeypatch.setattr(watcher, "run_watch_once", _fake_pass)
    app = webapp.create_app(
        static_dir=tmp_path,
        host="127.0.0.1",
        port=8765,
        allowed_hosts=[],
        enable_watcher=True,
        watch_interval=60.0,
    )
    with TestClient(app) as client:
        r = client.get("/api/repos", headers={"host": "127.0.0.1:8765"})
        assert r.status_code == 200
        assert hasattr(app.state, "watcher_task")
    assert app.state.watcher_task.done()


def test_webapp_worktree_watcher_override_endpoints(tmp_path):
    _seed_worktree(tmp_path, auto_fix_ci=True, auto_fix_reviews=False)
    app = webapp.create_app(
        static_dir=tmp_path,
        host="127.0.0.1",
        port=8765,
        allowed_hosts=[],
        enable_watcher=False,
    )
    headers = {"host": "127.0.0.1:8765", "content-type": "application/json"}
    encoded_ref = "gh%3Ademo%231"
    with TestClient(app) as client:
        g1 = client.get(f"/api/worktrees/{encoded_ref}/watcher", headers={"host": "127.0.0.1:8765"})
        assert g1.status_code == 200
        d1 = g1.json()
        assert d1["auto_fix_ci"] is None
        assert d1["effective_auto_fix_ci"] is True
        assert d1["effective_auto_fix_reviews"] is False

        p1 = client.post(
            f"/api/worktrees/{encoded_ref}/watcher",
            json={"auto_fix_ci": False, "auto_fix_reviews": True},
            headers=headers,
        )
        assert p1.status_code == 200
        d2 = p1.json()
        assert d2["auto_fix_ci"] is False
        assert d2["auto_fix_reviews"] is True
        assert d2["effective_auto_fix_ci"] is False
        assert d2["effective_auto_fix_reviews"] is True

        # Reset back to inherit
        p2 = client.post(
            f"/api/worktrees/{encoded_ref}/watcher",
            json={"auto_fix_ci": None},
            headers=headers,
        )
        assert p2.status_code == 200
        assert p2.json()["auto_fix_ci"] is None
        assert p2.json()["effective_auto_fix_ci"] is True


def test_cli_repo_update_persists_and_clears_auto_fix_flags(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from workagent import cli

    from tests.cli_helpers import _invoke

    _seed_worktree(tmp_path, repo_name="watchrepo", ref_key="gh:watchrepo#1",
                   branch="feat/watch", auto_fix_ci=False, auto_fix_reviews=False)
    monkeypatch.setattr(cli.repos, "repo_names", lambda: ["watchrepo"])

    r = _invoke("repo", "update", "watchrepo", "--auto-fix-ci", "--auto-fix-reviews", "--json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["auto_fix_ci"] is True
    assert data["auto_fix_reviews"] is True

    r2 = _invoke("repo", "update", "watchrepo", "--no-auto-fix-ci", "--json")
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.stdout)["auto_fix_ci"] is False

    runner = CliRunner()
    r3 = runner.invoke(cli.app, ["repo", "update", "nope", "--auto-fix-ci"])
    assert r3.exit_code != 0


def test_cli_watch_once_json_and_repo_filter(tmp_path, monkeypatch):
    import json

    from workagent import cli

    from tests.cli_helpers import _invoke

    _seed_worktree(tmp_path, repo_name="watchrepo", ref_key="gh:watchrepo#1",
                   branch="feat/watch", auto_fix_ci=True)
    monkeypatch.setattr(cli.repos, "repo_names", lambda: ["watchrepo"])
    monkeypatch.setattr("workagent.cli_watch.watcher.run_watch_once",
                        lambda **kw: {"checked": 1, "dispatched": 0, "skipped": 1,
                                      "errors": 0, "results": [],
                                      "echo": {"dry_run": kw.get("dry_run"),
                                               "repo_filter": kw.get("repo_filter")}})

    r = _invoke("watch", "--once", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    payload = json.loads(r.stdout)
    assert payload["echo"]["dry_run"] is True
    assert payload["echo"]["repo_filter"] is None

    r2 = _invoke("watch", "--once", "--repo", "watchrepo", "--json")
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.stdout)["echo"]["repo_filter"] == "watchrepo"


def test_cli_watch_dry_run_implies_once_without_loop(tmp_path, monkeypatch):
    from workagent import cli

    from tests.cli_helpers import _invoke

    _seed_worktree(tmp_path, auto_fix_ci=True)
    monkeypatch.setattr(cli.repos, "repo_names", lambda: ["demo"])
    calls: list = []

    def _fake_pass(**kw):
        calls.append(kw)
        return {"checked": 0, "dispatched": 0, "skipped": 0, "errors": 0, "results": []}

    monkeypatch.setattr("workagent.cli_watch.watcher.run_watch_once", _fake_pass)
    r = _invoke("watch", "--dry-run")
    assert r.exit_code == 0, r.output
    assert len(calls) == 1
    assert calls[0].get("dry_run") is True


def test_build_ci_fix_prompt_includes_url_and_last_50_lines():
    long_lines = [f"step line {i:02d} " + ("x" * 60) for i in range(1, 65)]
    prompt = watcher._build_ci_fix_prompt([
        {
            "name": "test",
            "conclusion": "failure",
            "url": "https://git.jibit.cloud/server/projectx/-/jobs/203729",
            "log_tail": "\n".join(long_lines),
        }
    ])
    assert "URL: https://git.jibit.cloud/server/projectx/-/jobs/203729" in prompt
    assert "step line 14 " not in prompt
    assert "step line 15 " in prompt
    assert "step line 64 " in prompt

