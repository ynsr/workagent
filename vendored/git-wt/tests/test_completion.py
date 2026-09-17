"""Tests for git_wt.completions — scripts, rc install, idempotency."""

from __future__ import annotations

import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from git_wt import completions
from git_wt.cli import main

SUPPORTED = ("bash", "zsh", "fish")


def run_cli(argv: list[str]):
    """Run main() with argv; return (exit code or None, capsys capture)."""
    with patch("sys.argv", ["git-wt", *argv]):
        try:
            main()
            return None
        except SystemExit as e:
            return e.code


# ── completion <shell> ───────────────────────────────────────────────


@pytest.mark.parametrize("shell", SUPPORTED)
def test_completion_script_nonempty(capsys, shell):
    """Each supported shell gets a non-empty script."""
    assert run_cli(["completion", shell]) is None
    out = capsys.readouterr().out
    assert out.strip(), f"empty script for {shell}"


def test_completion_bash_registers_function(capsys):
    """Bash script sources cleanly and registers the completion."""
    assert run_cli(["completion", "bash"]) is None
    script = capsys.readouterr().out
    bash = shutil.which("bash")
    if bash is None:
        assert "complete -F" in script
        return
    result = subprocess.run(
        [bash, "-c", f"source /dev/stdin && complete -p git-wt"],
        input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "complete -F" in result.stdout


def test_completion_bash_offers_subcommands(capsys):
    """Simulated COMP_WORDS resolve subcommands and per-command flags."""
    assert run_cli(["completion", "bash"]) is None
    script = capsys.readouterr().out
    bash = shutil.which("bash")
    probe = (
        "COMP_WORDS=(git-wt ''); COMP_CWORD=1; COMPREPLY=(); __git_wt; echo \"${COMPREPLY[*]}\"\n"
        "COMP_WORDS=(git-wt start --t); COMP_CWORD=2; COMPREPLY=(); __git_wt; echo \"${COMPREPLY[*]}\"\n"
        "COMP_WORDS=(git-wt completion ''); COMP_CWORD=2; COMPREPLY=(); __git_wt; echo \"${COMPREPLY[*]}\"\n"
    )
    result = subprocess.run(
        [bash, "-c", f"{script}\n{probe}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    top, start_flag, comp_shells = result.stdout.strip().splitlines()
    assert "start" in top and "completion-install" in top
    assert start_flag == "--type"
    assert comp_shells.split() == ["bash", "zsh", "fish"]


def test_completion_zsh_script_structure(capsys):
    """Zsh script defines the function and registers it with compdef."""
    assert run_cli(["completion", "zsh"]) is None
    script = capsys.readouterr().out
    assert "_git_wt() {" in script
    assert "compdef _git_wt git-wt" in script
    assert "'start:" in script and "'finish:" in script
    zsh = shutil.which("zsh")
    if zsh is None:  # pragma: no cover
        pytest.skip("zsh not available")
    result = subprocess.run(
        [zsh, "-fc", "autoload -Uz compinit && compinit; source /dev/stdin && whence -w _git_wt"],
        input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "_git_wt is a function" in result.stdout


def test_completion_fish_script_structure(capsys):
    """Fish script offers subcommands and pipes through | source in the eval line."""
    assert run_cli(["completion", "fish"]) is None
    script = capsys.readouterr().out
    assert "complete -c git-wt" in script
    assert "-a start" in script
    fish = shutil.which("fish")
    if fish is None:  # pragma: no cover
        pytest.skip("fish not available")
    result = subprocess.run(
        [fish, "-c", "source /dev/stdin; complete -e git-wt; echo OK"],
        input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_completion_unknown_shell_exit_2(capsys):
    """Unknown shell is a usage error (exit 2)."""
    assert run_cli(["completion", "tcsh"]) == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err or "tcsh" in err


def test_eval_line_per_shell():
    """bash/zsh eval $(...); fish pipes to source."""
    assert completions.eval_line("git-wt", "bash") == 'eval "$(git-wt completion bash)"'
    assert completions.eval_line("git-wt", "zsh") == 'eval "$(git-wt completion zsh)"'
    assert completions.eval_line("git-wt", "fish") == "git-wt completion fish | source"


# ── completion-install ───────────────────────────────────────────────


def test_install_creates_block_and_bak(tmp_path, capsys):
    """First install writes the marker block and keeps a .bak backup."""
    rc = tmp_path / "rc"
    rc.write_text("# existing rc\n")
    assert run_cli(["completion-install", "bash", "--rcfile", str(rc), "--yes"]) is None
    content = rc.read_text()
    assert "existing rc" in content  # preserved
    assert "# >>> git-wt completions >>>" in content
    assert 'eval "$(git-wt completion bash)"' in content
    assert "# <<< git-wt completions <<<" in content
    assert (tmp_path / "rc.bak").read_text() == "# existing rc\n"
    assert "source" in capsys.readouterr().err


def test_install_idempotent(tmp_path, capsys):
    """Second install is a byte-identical no-op."""
    rc = tmp_path / "rc"
    assert run_cli(["completion-install", "zsh", "--rcfile", str(rc), "--yes"]) is None
    first = rc.read_text()
    err1 = capsys.readouterr().err
    assert "autoload -U compinit" in first  # zsh block ensures compinit
    assert "restart your shell" in err1
    assert run_cli(["completion-install", "zsh", "--rcfile", str(rc), "--yes"]) is None
    assert rc.read_text() == first  # unchanged
    assert "already installed" in capsys.readouterr().err


def test_install_replaces_stale_block(tmp_path):
    """A stale marker block is replaced, not duplicated."""
    rc = tmp_path / "rc"
    stale = (
        "# >>> git-wt completions >>>\n"
        'eval "$(git-wt completion OLD)"\n'
        "# <<< git-wt completions <<<\n"
    )
    rc.write_text("keep\n" + stale)
    assert run_cli(["completion-install", "bash", "--rcfile", str(rc), "--yes"]) is None
    content = rc.read_text()
    assert content.count("# >>> git-wt completions >>>") == 1
    assert 'eval "$(git-wt completion bash)"' in content
    assert "OLD" not in content
    assert "keep" in content


def test_install_default_rc_from_shell(monkeypatch, tmp_path):
    """No --rcfile: resolves the rc file for the detected $SHELL."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert run_cli(["completion-install", "--yes"]) is None
    assert (home / ".bashrc").is_file()


def test_install_unknown_shell_exit_2(tmp_path):
    """Explicit unsupported shell is a usage error (exit 2)."""
    rc = tmp_path / "rc"
    assert run_cli(["completion-install", "nu", "--rcfile", str(rc), "--yes"]) == 2
    assert not rc.exists()


def test_install_undetectable_shell_exit_2(monkeypatch, tmp_path):
    """No shell arg + unsupported $SHELL → usage error telling user to pass one."""
    monkeypatch.setenv("SHELL", "/usr/bin/nu")
    assert run_cli(["completion-install", "--yes", "--rcfile", str(tmp_path / "rc")]) == 2


def test_install_declined_confirmation(tmp_path, monkeypatch):
    """TTY confirm declined → rc untouched, exit 1."""
    rc = tmp_path / "rc"
    rc.write_text("untouched\n")
    import builtins

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "n")
    assert run_cli(["completion-install", "bash", "--rcfile", str(rc)]) == 1


def test_install_non_tty_proceeds_without_prompt(tmp_path, monkeypatch):
    """Non-TTY (agents/scripts) proceeds without prompting."""
    rc = tmp_path / "rc"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert run_cli(["completion-install", "bash", "--rcfile", str(rc), "--yes"]) is None
    assert "git-wt completions" in rc.read_text()


def test_install_quiet_suppresses_stderr(tmp_path, capsys):
    """--quiet silences the non-essential stderr hint lines."""
    rc = tmp_path / "rc"
    assert run_cli(["completion-install", "bash", "--rcfile", str(rc), "--yes", "-q"]) is None
    assert capsys.readouterr().err == ""
