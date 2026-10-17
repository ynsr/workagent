"""Tests for env-file sourcing (env.py + launch wiring)."""
from __future__ import annotations

from workagent import env


def test_default_content_has_required_exports():
    assert 'VISUAL' in env.DEFAULT_ENV_CONTENT
    assert 'EDITOR' in env.DEFAULT_ENV_CONTENT
    assert 'NO_PROXY' in env.DEFAULT_ENV_CONTENT
    assert 'chat.jibit.cloud' in env.DEFAULT_ENV_CONTENT
    assert 'PI_EDIT_VARIANT=hashline' in env.DEFAULT_ENV_CONTENT


def test_ensure_creates_default_once(isolated_config, monkeypatch):
    from workagent import store
    target = store.config_dir() / "env.sh"
    assert not target.exists()
    got = env.ensure_env_file()
    assert got == target
    assert target.read_text() == env.DEFAULT_ENV_CONTENT
    # Second call preserves user edits (never overwrites).
    target.write_text("export FOO=bar\n")
    assert env.ensure_env_file() == target
    assert target.read_text() == "export FOO=bar\n"


def test_ensure_explicit_path(isolated_config, tmp_path):
    custom = tmp_path / "custom.sh"
    assert env.ensure_env_file(str(custom)) == custom
    assert custom.read_text() == env.DEFAULT_ENV_CONTENT


def test_parse_expands_defaults_against_environ(tmp_path, monkeypatch):
    f = tmp_path / "e.sh"
    f.write_text('export VISUAL="${VISUAL:-code --wait}"\n'
                 'export PI_EDIT_VARIANT=hashline\n'
                 '# comment\n\nexport NO_PROXY="a,b"\n')
    monkeypatch.delenv("VISUAL", raising=False)
    out = env.parse_env_file(f)
    assert out["VISUAL"] == "code --wait"
    assert out["PI_EDIT_VARIANT"] == "hashline"
    assert out["NO_PROXY"] == "a,b"
    monkeypatch.setenv("VISUAL", "vim")
    assert env.parse_env_file(f)["VISUAL"] == "vim"


def test_wrap_command_prefixes_source(tmp_path):
    f = tmp_path / "e.sh"
    f.write_text("export A=1\n")
    wrapped = env.wrap_command("cd /wt && omp foo", str(f))
    assert wrapped == f"source {f} && cd /wt && omp foo"
    assert env.wrap_command("cd /wt && omp foo", None) == "cd /wt && omp foo"


def test_resume_shell_command_sources_env(tmp_path, isolated_config, monkeypatch):
    from workagent import store
    from workagent.web_runs import _resume_shell_command
    env_file = store.config_dir() / "env.sh"
    env_file.write_text("export A=1\n")
    real = tmp_path / "r.jsonl"
    real.write_text("{}\n")
    cmd = _resume_shell_command("/wt", str(real))
    assert cmd.startswith(f"source {env_file} && cd /wt && omp --resume")
    fresh = _resume_shell_command("/wt", str(tmp_path / "missing.jsonl"))
    assert fresh.startswith(f"source {env_file} && cd /wt && omp --session-dir")


def test_headless_launch_gets_parsed_env(tmp_path, isolated_config, monkeypatch):
    from workagent import backend, store
    env_file = store.config_dir() / "env.sh"
    env_file.write_text("export TEST_WA_ENV=hello123\n")
    monkeypatch.setattr(backend.shutil, "which", lambda n: f"/usr/bin/{n}")
    seen = {}

    def fake_run(argv, cwd, **kw):
        seen.update(kw)
        class Proc:
            returncode = 0
        return Proc()

    monkeypatch.setattr(backend.subprocess, "run", fake_run)
    backend.launch("omp", "p", str(tmp_path), no_tty=True,
                   env_file=str(env_file))
    assert seen["env"]["TEST_WA_ENV"] == "hello123"

def test_tty_launch_applies_overlay_before_exec(tmp_path, isolated_config, monkeypatch):
    from workagent import backend, store
    env_file = store.config_dir() / "env.sh"
    env_file.write_text("export TEST_WA_ENV=tty123\n")
    monkeypatch.delenv("TEST_WA_ENV", raising=False)
    monkeypatch.setattr(backend.shutil, "which", lambda n: f"/usr/bin/{n}")
    seen = {}

    def fake_execvp(path, argv):
        seen["environ"] = dict(backend.os.environ)
        raise RuntimeError("stop after exec")

    monkeypatch.setattr(backend.os, "execvp", fake_execvp)
    monkeypatch.setattr(backend, "cd_worktree", lambda d: None)
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="stop after exec"):
        backend.get_harness("omp").launch("p", str(tmp_path), False,
                                          env_file=str(env_file))
    assert seen["environ"].get("TEST_WA_ENV") == "tty123"


def test_merged_env_overlay(tmp_path, isolated_config, monkeypatch):
    from workagent import store
    env_file = store.config_dir() / "env.sh"
    env_file.write_text("export TEST_WA_MERGED=yes456\n")
    monkeypatch.delenv("TEST_WA_MERGED", raising=False)
    assert env.merged_env(str(env_file))["TEST_WA_MERGED"] == "yes456"


def test_pre_release_gate_sources_env(tmp_path, isolated_config):
    import subprocess
    from workagent import release_gates as g
    from workagent import store
    src = tmp_path / "src"
    src.mkdir()
    for a in (["init", "-q", str(src)],
              ["-C", str(src), "config", "user.email", "t@t"],
              ["-C", str(src), "config", "user.name", "t"]):
        subprocess.run(["git", *a], check=True)
    (src / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-qm", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    env_file = store.config_dir() / "env.sh"
    env_file.write_text("export TEST_WA_GATE=gate789\n")
    g.run_local_gate(str(src), "proj", sha,
                     "test \"$TEST_WA_GATE\" = gate789", 60)
