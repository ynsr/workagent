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
    assert "no CI configured" in capsys.readouterr().err
