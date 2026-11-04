"""CLI prodtest run tests (Task 5)."""
from __future__ import annotations

import json

from workagent import cli

from tests.cli_helpers import runner, _invoke


def test_run_refuses_without_release(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--dry-run", "--json"])
    assert r.exit_code != 0, r.output
    assert "release" in (r.stdout + r.stderr).lower()


def test_run_dry_run_with_release_ok(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-9", plan_path="p.yaml", plan_revision=1,
                       release_sha="abc1234", status="pending_prodtest")
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert out["release_sha"] == "abc1234"


def test_run_refuses_bad_fix_mode(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-9", release_sha="abc1234")
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--fix-mode", "auto"])
    assert r.exit_code != 0, r.output
    assert "fix-mode" in (r.stdout + r.stderr).lower()


def test_run_refuses_held_lock(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-9", release_sha="abc1234")
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    from workagent import prodtest_lock as _lock

    base = tmp_path / "locks"
    base.mkdir()
    monkeypatch.setattr(cli.store, "config_dir", lambda: base)
    with _lock.held_issue_lock(str(base), "IPG-9"):
        r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--json"])
    assert r.exit_code != 0, r.output
    assert "already in progress" in (r.stdout + r.stderr).lower()


def test_run_timeout_kills_harness_and_marks_blocked(isolated_config, tmp_path, monkeypatch):
    """Tiny timeout forces the BLOCKED path: group killed, status persisted."""
    import subprocess
    import time
    from workagent import cli, cli_prodtest
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-9", release_sha="abc1234")
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    saved_upsert = sq.upsert_prodtest
    monkeypatch.setattr(cli.store, "upsert_prodtest",
                        lambda key, **kw: saved_upsert(db, key, **kw))
    monkeypatch.setattr(cli.store, "load_links", lambda **k: {})
    monkeypatch.setattr("workagent.worktrees.resolve_any",
                        lambda ref, links: ("jira:IPG-9",
                                            {"issue": "IPG-9", "branch": "feat/x",
                                             "repo": "", "worktree": str(tmp_path),
                                             "issue_url": ""},
                                            {"kind": "issue", "number": "IPG-9"}))

    # Stub the harness layer: a real sleeping child in its own process group
    # (mirrors backend kill semantics: SIGTERM then SIGKILL on overrun).
    import os as _os
    import signal as _signal
    from workagent.errors import HarnessTimeout as _HarnessTimeout

    killed = []

    def _sleepy(*a, **k):
        timeout_s = k.get("timeout_s")
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _os.killpg(proc.pid, _signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _os.killpg(proc.pid, _signal.SIGKILL)
                proc.wait()
            killed.append(proc.pid)
            raise _HarnessTimeout("command timed out: sleep 30")
        return None

    monkeypatch.setattr(cli_prodtest, "_run_harness", _sleepy)
    # Checkout/validate/render/comment: keep hermetic, avoid FS/network.
    monkeypatch.setattr("workagent.release_worktree.prepare",
                        lambda repo, dest, sha: tmp_path)
    monkeypatch.setattr("workagent.release_worktree.finish",
                        lambda dest, ok: None)
    monkeypatch.setattr("workagent.prodtest_scripts.run_validate",
                        lambda plan, manifest: {"valid": True})
    monkeypatch.setattr("workagent.prodtest_scripts.render",
                        lambda results_yaml: "# partial\n")
    monkeypatch.setattr("workagent.issue_comments.post_issue_comment",
                        lambda url, body: None)

    from workagent import cli_prodtest as _cp
    monkeypatch.setattr(_cp, "_run_timeout_s", lambda plan_file, skip: 1)
    t0 = time.monotonic()
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9",
                                "--launch", "--no-tty", "--yes", "--json",
                                "--run-dir", str(tmp_path / "run-out")])
    dt = time.monotonic() - t0
    assert r.exit_code != 0, r.output
    assert "marked BLOCKED" in (r.stdout + r.stderr), r.output
    assert killed, "expected the harness process group to be killed"
    # No orphan survives: the sleep child is reaped.
    assert subprocess.run(["ps", "-p", str(killed[0])],
                          capture_output=True).returncode != 0
    # Status persisted despite the overrun.
    row = sq.get_prodtest(db, "IPG-9")
    assert row and row.get("status") == "blocked", row
    assert row.get("last_run_id"), row
    # Lock released and ephemeral checkout finished after the kill.
    from workagent import prodtest_lock as _lock
    base = cli.store.config_dir()
    assert not _lock.is_locked(str(base), "IPG-9")
    # Fast: tiny-plan default timeout would be ~20 min; stub the budget down.
    assert dt < 60
    # Partial artifact exists and is not empty.
    report = tmp_path / "run-out" / "report.md"
    assert report.exists() and report.stat().st_size > 0

def test_stored_divergence_warns_on_revision_mismatch(tmp_path):
    from workagent import cli_prodtest as _cp
    plan = tmp_path / "plan.yaml"
    plan.write_text("revision: 2\ncases: []\n")
    msg = _cp._plan_stored_divergence({"plan_path": str(plan), "plan_revision": 3}, plan)
    assert msg and "plan_revision" in msg
    assert _cp._plan_stored_divergence({"plan_path": str(plan), "plan_revision": 2}, plan) is None


def test_cap_report_truncates_with_note():
    from workagent import cli_prodtest as _cp
    long_text = "x" * (_cp._REPORT_POST_CAP + 100)
    out = _cp._cap_report(long_text)
    assert len(out) < len(long_text)
    assert "truncated" in out
    assert _cp._cap_report("short") == "short"


def test_validate_results_flags_unknown_cases(tmp_path, monkeypatch):
    import yaml
    from workagent import cli_prodtest as _cp
    plan = tmp_path / "plan.yaml"
    plan.write_text(yaml.safe_dump({"cases": [{"id": "C1"}]}))
    results = tmp_path / "results.yaml"
    results.write_text(yaml.safe_dump({"cases": [{"id": "C1"}, {"id": "EVIL"}]}))
    monkeypatch.setattr(_cp, "eprint", lambda *a, **k: None)
    out = _cp._validate_results_for_post(str(results), plan, type("P", (), {
        "render": staticmethod(lambda f: "# report")})())
    assert "EVIL" in out


def _run_all_links(monkeypatch, links, plan_path="p.yaml"):
    from workagent import cli
    monkeypatch.setattr(cli.store, "load_links", lambda **k: links)
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: {
        "A": {"release_sha": "sha1", "plan_path": plan_path},
        "B": {"release_sha": "", "plan_path": plan_path},
        "C": {"plan_path": plan_path},
        "D": {"release_sha": "sha2", "plan_path": ""},
    }.get(key))


def test_run_all_dry_run_lists_eligible_and_skips(isolated_config, tmp_path, monkeypatch):
    import json
    from workagent import cli
    plan = tmp_path / "p.yaml"
    plan.write_text("plan_version: 1\n")
    (tmp_path / ".workagent" / "prodtest" / "plans").mkdir(parents=True)
    (tmp_path / ".workagent" / "prodtest" / "plans" / "D.yaml").write_text("plan_version: 1\n")
    _run_all_links(monkeypatch, {
        "jira:A": {"worktree": str(tmp_path), "issue": "A"},
        "jira:B": {"worktree": str(tmp_path), "issue": "B"},
        "jira:C": {"worktree": str(tmp_path), "issue": "C"},
        "jira:D": {"worktree": str(tmp_path), "issue": "D"},
    }, plan_path=str(plan))
    r = runner.invoke(cli.app, ["prodtest", "run", "--all", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)["results"]
    by_key = {row["key"]: row for row in rows}
    assert by_key["jira:A"]["status"] == "dry-run"
    assert by_key["jira:B"]["status"] == "skipped:no-release"
    assert by_key["jira:C"]["status"] == "skipped:no-release"
    # Empty stored plan_path but a valid canonical on-disk plan → eligible.
    assert by_key["jira:D"]["status"] == "dry-run"


def test_run_all_rejects_ref_and_run_dir(isolated_config, tmp_path, monkeypatch):
    from workagent import cli
    _run_all_links(monkeypatch, {})
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--all", "--json"])
    assert r.exit_code == 2, r.output
    r = runner.invoke(cli.app, ["prodtest", "run", "--all", "--run-dir", "x"])
    assert r.exit_code == 2, r.output
    r = runner.invoke(cli.app, ["prodtest", "run", "--all", "--session-file", "x"])
    assert r.exit_code == 2, r.output
