"""Local + CI gate tests (Task 5 RED)."""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest



def _git_repo(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for a in (["init", "-q", str(d)], ["-C", str(d), "config", "user.email", "t@t"],
              ["-C", str(d), "config", "user.name", "t"]):
        subprocess.run(["git", *a], check=True)
    (d / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(d), "add", "."], check=True)
    subprocess.run(["git", "-C", str(d), "commit", "-qm", "init"], check=True)
    return d


def _head(d: Path) -> str:
    return subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _fake_clock(monkeypatch):
    from workagent import release_gates as g
    now = [1000.0]
    monkeypatch.setattr(g, "_now", lambda: now[0])
    monkeypatch.setattr(g, "_sleep", lambda s: now.__setitem__(0, now[0] + s))
    return now


def test_local_gate_missing_command_aborts(tmp_path, capsys):
    from workagent import release_gates as g
    src = _git_repo(tmp_path / "src")
    with pytest.raises(Exception) as e:
        g.run_local_gate(str(src), "proj", _head(src), "", 60)
    assert getattr(e.value, "exit_code", 2) == 2
    assert "pre-release command missing" in capsys.readouterr().err


def test_local_gate_nonzero_aborts_and_keeps_worktree(tmp_path, monkeypatch):
    from workagent import release_gates as g
    from workagent.errors import HarnessError
    monkeypatch.setenv("WORKTREES_DIR", str(tmp_path / "wt"))
    src = _git_repo(tmp_path / "src")
    kept = []
    monkeypatch.setattr(g._wt, "finish", lambda dest, ok: kept.append((dest, ok)) or str(dest))
    with pytest.raises(HarnessError) as e:
        g.run_local_gate(str(src), "proj", _head(src), "exit 3", 60)
    assert e.value.exit_code == 1 and kept and kept[0][1] is False


def test_local_gate_timeout_kills_group(tmp_path, monkeypatch):
    import time as _t
    from workagent import release_gates as g
    from workagent.errors import HarnessError
    src = _git_repo(tmp_path / "src")
    monkeypatch.setenv("WORKTREES_DIR", str(tmp_path / "wt"))
    t0 = _t.monotonic()
    with pytest.raises(HarnessError, match="timed out"):
        g.run_local_gate(str(src), "proj", _head(src), "sleep 30", 1)
    assert _t.monotonic() - t0 < 25  # whole process group died, no 30s wait


def test_local_gate_logs_command_first(tmp_path, capsys):
    from workagent import release_gates as g
    src = _git_repo(tmp_path / "src")
    import os
    os.environ["WORKTREES_DIR"] = str(tmp_path / "wt")
    g.run_local_gate(str(src), "proj", _head(src), "true", 60)
    assert "running pre-release for proj: true" in capsys.readouterr().err


def test_ci_gate_success_proceeds(monkeypatch):
    from workagent import release_gates as g
    monkeypatch.setattr(g, "_ci_sha_status", lambda *a: ("success", "http://ci/1"))
    g.run_ci_gate("glab", "/r", "abc", 60)


def test_ci_gate_failure_aborts(monkeypatch):
    from workagent import release_gates as g
    monkeypatch.setattr(g, "_ci_sha_status", lambda *a: ("failure", "http://ci/1"))
    with pytest.raises(Exception) as e:
        g.run_ci_gate("glab", "/r", "abc", 60)
    assert getattr(e.value, "exit_code", 2) == 1


def test_ci_gate_pending_polls_then_times_out(monkeypatch, capsys):
    from workagent import release_gates as g
    _fake_clock(monkeypatch)
    monkeypatch.setattr(g, "_ci_sha_status", lambda *a: ("running", ""))
    with pytest.raises(Exception):
        g.run_ci_gate("glab", "/r", "abc", 60)
    assert "CI timed out" in capsys.readouterr().err



def test_ci_gate_no_ci_aborts(monkeypatch, capsys):
    from workagent import release_gates as g
    monkeypatch.setattr(g, "_ci_sha_status", lambda *a: ("not_started", ""))
    with pytest.raises(Exception):
        g.run_ci_gate("glab", "/r", "abc", 60)

def test_ci_sha_status_glab_prefers_failed_url(monkeypatch, tmp_path):
    """Issue #116: rows[0] may be a later success; link the failed status."""
    import json
    import workagent.refs  # noqa: F401 -- prime refs before refs_ci (circular)
    from workagent import release_gates as g
    statuses = json.dumps([
        {"status": "success", "target_url": "https://ci/ok"},
        {"status": "failed", "target_url": "https://ci/bad"},
    ])
    real_run = g.subprocess.run

    def fake_run(*a, **k):
        argv = a[0] if a else k.get("args", [])
        if "glab" in argv[:2] and "statuses" in " ".join(argv):
            return type("R", (), {"stdout": statuses})()
        return real_run(*a, **k)

    (tmp_path / "repo").mkdir()
    assert real_run(["git", "-C", str(tmp_path / "repo"), "init", "-q"]).returncode == 0
    monkeypatch.setattr(g.subprocess, "run", fake_run)
    st, url = g._ci_sha_status("glab", str(tmp_path / "repo"), "abc")
    assert (st, url) == ("failure", "https://ci/bad")


def test_ci_sha_status_gh_prefers_failed_url(monkeypatch, tmp_path):
    """Issue #116 (gh): runs[0] may be a later success; link the failed check."""
    import json
    import workagent.refs  # noqa: F401 -- prime refs before refs_ci (circular)
    from workagent import release_gates as g
    checks = json.dumps({"check_runs": [
        {"conclusion": "success", "status": "completed", "html_url": "https://ci/ok"},
        {"conclusion": "failure", "status": "completed", "html_url": "https://ci/bad"},
    ]})
    real_run = g.subprocess.run

    def fake_run(*a, **k):
        argv = a[0] if a else k.get("args", [])
        if argv[:2] == ["gh", "api"]:
            return type("R", (), {"stdout": checks})()
        return real_run(*a, **k)

    (tmp_path / "repo").mkdir()
    assert real_run(["git", "-C", str(tmp_path / "repo"), "init", "-q"]).returncode == 0
    monkeypatch.setattr(g.subprocess, "run", fake_run)
    st, url = g._ci_sha_status("gh", str(tmp_path / "repo"), "abc")
    assert (st, url) == ("failure", "https://ci/bad")


def test_ci_sha_status_glab_ignores_ci_ignore_patterns(monkeypatch, tmp_path):
    import json
    import workagent.refs  # noqa: F401
    from workagent import release_gates as g, refs_ci
    monkeypatch.setattr(refs_ci, "_repo_ci_ignore", lambda cwd: ["trivy:*"])
    statuses = json.dumps([
        {"name": "trivy:source", "status": "failed", "target_url": "https://ci/trivy"},
        {"name": "test", "status": "success", "target_url": "https://ci/test"},
    ])
    real_run = g.subprocess.run

    def fake_run(*a, **k):
        argv = a[0] if a else k.get("args", [])
        if "glab" in argv[:2] and "statuses" in " ".join(argv):
            return type("R", (), {"stdout": statuses})()
        return real_run(*a, **k)

    (tmp_path / "repo").mkdir()
    assert real_run(["git", "-C", str(tmp_path / "repo"), "init", "-q"]).returncode == 0
    assert real_run(["git", "-C", str(tmp_path / "repo"), "remote", "add", "origin",
                     "https://git.jibit.cloud/server/projectx.git"]).returncode == 0
    monkeypatch.setattr(g.subprocess, "run", fake_run)
    st, url = g._ci_sha_status("glab", str(tmp_path / "repo"), "abc123")
    assert st == "success"
    assert url == "https://ci/test"


def test_ci_sha_status_gh_ignores_ci_ignore_patterns(monkeypatch, tmp_path):
    import json
    import workagent.refs  # noqa: F401
    from workagent import release_gates as g, refs_ci
    monkeypatch.setattr(refs_ci, "_repo_ci_ignore", lambda cwd: ["trivy-*"])
    payload = {"checks": json.dumps({"check_runs": [
        {"name": "trivy-scan", "conclusion": "failure", "status": "completed",
         "html_url": "https://ci/trivy"},
        {"name": "pytest", "conclusion": "success", "status": "completed",
         "html_url": "https://ci/pytest"},
    ]})}
    real_run = g.subprocess.run

    def fake_run(*a, **k):
        argv = a[0] if a else k.get("args", [])
        if argv[:2] == ["gh", "api"]:
            return type("R", (), {"stdout": payload["checks"]})()
        return real_run(*a, **k)

    (tmp_path / "repo").mkdir()
    assert real_run(["git", "-C", str(tmp_path / "repo"), "init", "-q"]).returncode == 0
    assert real_run(["git", "-C", str(tmp_path / "repo"), "remote", "add", "origin",
                     "https://github.com/o/r.git"]).returncode == 0
    monkeypatch.setattr(g.subprocess, "run", fake_run)
    st, url = g._ci_sha_status("gh", str(tmp_path / "repo"), "abc123")
    assert (st, url) == ("success", "https://ci/pytest")

    # All check-runs match ci_ignore -> fall back to unfiltered runs instead of not_started.
    payload["checks"] = json.dumps({"check_runs": [
        {"name": "trivy-scan", "conclusion": "failure", "status": "completed",
         "html_url": "https://ci/trivy"},
    ]})
    st, url = g._ci_sha_status("gh", str(tmp_path / "repo"), "abc123")
    assert (st, url) == ("failure", "https://ci/trivy")
