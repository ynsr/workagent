"""Tests for git_wt.completions — generated scripts, resolution, rc install."""

from __future__ import annotations

import builtins
import os
import subprocess
import sys

import pytest
import typer.main
from typer.testing import CliRunner

from git_wt import completions
from git_wt.cli import PROG, app

SUPPORTED = ("bash", "zsh", "fish")
SHOW = ["completions", "show"]
INSTALL = ["completions", "install"]


def _click_cmd():
    """The click command built from the Typer app (single source of truth)."""
    return typer.main.get_command(app)


def _resolve(completed: list[str], incomplete: str) -> list[str]:
    """Resolve a completion request the way the generated script would."""
    try:
        from typer._click import shell_completion
        from typer._completion_classes import completion_init
    except ImportError:  # older typer: plain click
        import click.shell_completion as shell_completion  # type: ignore[no-redef]

        def completion_init() -> None:
            pass

    completion_init()
    comp_cls = shell_completion.get_completion_class("bash")
    assert comp_cls is not None
    comp = comp_cls(_click_cmd(), {}, PROG, f"_{PROG.upper().replace('-', '_')}_COMPLETE")
    return [c.value for c in comp.get_completions(completed, incomplete)]


# ── completions show ─────────────────────────────────────────────────


@pytest.mark.parametrize("shell", SUPPORTED)
def test_show_script_nonempty(runner: CliRunner, shell: str):
    """Each supported shell gets a non-empty generated script."""
    result = runner.invoke(app, [*SHOW, shell])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip(), f"empty script for {shell}"


def test_show_bash_registers_function(runner: CliRunner):
    """Bash script defines and registers the completion function."""
    result = runner.invoke(app, [*SHOW, "bash"])
    assert result.exit_code == 0
    assert "_git_wt_completion" in result.stdout
    assert "complete " in result.stdout


def test_show_zsh_registers_compdef(runner: CliRunner):
    """Zsh script defines the function and registers it with compdef."""
    result = runner.invoke(app, [*SHOW, "zsh"])
    assert result.exit_code == 0
    assert "compdef" in result.stdout
    assert "_git_wt_completion" in result.stdout


def test_show_fish_structure(runner: CliRunner):
    """Fish script defines the performer and a complete --command registration."""
    result = runner.invoke(app, [*SHOW, "fish"])
    assert result.exit_code == 0
    assert "complete --command git-wt" in result.stdout
    # The heavy lifting is delegated to a live `git-wt` process, not static text.
    assert "_TYPER_COMPLETE_FISH_ACTION=get-args" in result.stdout


def test_show_unknown_shell_exit_2(runner: CliRunner):
    """Unknown shell is a usage error (exit 2) naming the supported shells."""
    result = runner.invoke(app, [*SHOW, "tcsh"])
    assert result.exit_code == 2
    assert "tcsh" in result.stderr
    assert "bash" in result.stderr and "fish" in result.stderr


def test_show_scripts_come_from_completion_classes_not_static_text():
    """The script is generated per program name — no hand-coded static blob."""
    script = completions.get_completion_script(PROG, "bash", click_cmd=_click_cmd())
    assert "git-wt" in script
    # Script is the eval-includable source, not a wrapped one-off.
    assert script.lstrip().startswith("_git_wt_completion()")


# ── dynamic value resolution (autocompletion= callbacks) ─────────────


def test_resolve_subcommands():
    """Top-level resolution offers the real subcommands."""
    values = _resolve([], "")
    assert "finish" in values
    assert "cleanup" in values
    assert "list" in values
    assert "completions" in values


def test_resolve_command_flags():
    """Per-command flags resolve exactly as registered on the command."""
    assert "--resume" in _resolve(["start"], "--re")
    assert "--branch" in _resolve(["start"], "--")
    assert "--worktree" in _resolve(["finish"], "--wor")
    assert "--delete-branch" in _resolve(["cleanup"], "--de")


def test_resolve_completion_subcommand():
    """`completions <TAB>` offers show/install from the sub-app."""
    values = _resolve(["completions"], "")
    assert "show" in values and "install" in values


def test_branch_callback_local_only(tmp_repo_with_feature, monkeypatch):
    """complete_branch_names returns local branches filtered by prefix."""
    monkeypatch.chdir(tmp_repo_with_feature)
    values = completions.complete_branch_names(None, "feat")
    assert "feat/42-add-auth" in values
    assert completions.complete_branch_names(None, "zzz") == []


def test_branch_callback_never_raises(tmp_path, monkeypatch):
    """Outside a repo (git fails) → [] instead of an exception."""
    monkeypatch.chdir(tmp_path)
    assert completions.complete_branch_names(None, "") == []


def test_worktree_callback_paths(tmp_repo, monkeypatch):
    """complete_worktree_paths lists worktree paths filtered by prefix."""
    monkeypatch.chdir(tmp_repo)
    from typer.testing import CliRunner

    assert CliRunner().invoke(app, ["start", "--branch", "feat/8--complete-me"]).exit_code == 0
    values = completions.complete_worktree_paths(None, "")
    assert any(p.endswith("feat/8--complete-me") for p in values)


def test_worktree_callback_never_raises(tmp_path, monkeypatch):
    """Outside a repo → [] instead of an exception."""
    monkeypatch.chdir(tmp_path)
    assert completions.complete_worktree_paths(None, "") == []


# ── eval lines ───────────────────────────────────────────────────────


def test_eval_line_per_shell():
    """bash/zsh eval $(); fish pipes to source; server stderr discarded."""
    assert completions.eval_line(PROG, "bash") == f'eval "$({PROG} completions show bash 2>/dev/null)"'
    assert completions.eval_line(PROG, "zsh") == f'eval "$({PROG} completions show zsh 2>/dev/null)"'
    assert completions.eval_line(PROG, "fish") == f"{PROG} completions show fish 2>/dev/null | source"


# ── completions install ──────────────────────────────────────────────


def test_install_creates_block_and_bak(runner: CliRunner, tmp_path):
    """First install writes the marker block and keeps a .bak backup."""
    rc = tmp_path / "rc"
    rc.write_text("# pre-existing rc\n", encoding="utf-8")
    result = runner.invoke(app, [*INSTALL, "bash", "--rcfile", str(rc), "--yes"])
    assert result.exit_code == 0, result.stderr
    content = rc.read_text(encoding="utf-8")
    assert completions.START_MARKER.format(prog=PROG) in content
    assert completions.eval_line(PROG, "bash") in content
    assert (tmp_path / "rc.bak").read_text(encoding="utf-8") == "# pre-existing rc\n"
    assert "installed" in result.stderr


def test_install_idempotent(runner: CliRunner, tmp_path):
    """Second install is a byte-identical no-op reporting 'already installed'."""
    rc = tmp_path / "rc"
    first = runner.invoke(app, [*INSTALL, "zsh", "--rcfile", str(rc), "--yes"])
    assert first.exit_code == 0, first.stderr
    before = rc.read_text(encoding="utf-8")
    second = runner.invoke(app, [*INSTALL, "zsh", "--rcfile", str(rc), "--yes"])
    assert second.exit_code == 0, second.stderr
    assert rc.read_text(encoding="utf-8") == before
    assert "already installed" in second.stderr


def test_install_replaces_stale_block(runner: CliRunner, tmp_path):
    """A stale block (old command name) is replaced, not duplicated."""
    rc = tmp_path / "rc"
    rc.write_text(
        f"{completions.START_MARKER.format(prog=PROG)}\n"
        f'eval "$(git-wt completion bash)"\n'
        f"{completions.END_MARKER.format(prog=PROG)}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, [*INSTALL, "bash", "--rcfile", str(rc), "--yes"])
    assert result.exit_code == 0, result.stderr
    content = rc.read_text(encoding="utf-8")
    assert content.count(completions.START_MARKER.format(prog=PROG)) == 1
    assert 'eval "$(git-wt completion bash)"' not in content
    assert completions.eval_line(PROG, "bash") in content


def test_install_default_rc_from_shell(monkeypatch, runner: CliRunner, tmp_path):
    """No --rcfile: resolves the rc file for the detected $SHELL."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHELL", "/usr/bin/bash")
    result = runner.invoke(app, [*INSTALL, "--yes"])
    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "home" / ".bashrc").is_file()


def test_install_unknown_shell_exit_2(runner: CliRunner, tmp_path):
    """Explicit unsupported shell is a usage error (exit 2); rc untouched."""
    rc = tmp_path / "rc"
    result = runner.invoke(app, [*INSTALL, "tcsh", "--rcfile", str(rc)])
    assert result.exit_code == 2
    assert not rc.exists()


def test_install_undetectable_shell_exit_2(runner: CliRunner, tmp_path, monkeypatch):
    """No shell arg + unsupported $SHELL → usage error telling user to pass one."""
    monkeypatch.setenv("SHELL", "/usr/bin/nu")
    result = runner.invoke(app, [*INSTALL, "--yes", "--rcfile", str(tmp_path / "rc")])
    assert result.exit_code == 2
    assert "--install" not in result.stderr  # actionable, not argparse noise
    assert "bash" in result.stderr


def test_install_declined_confirmation(runner: CliRunner, tmp_path, monkeypatch):
    """TTY confirm declined → rc untouched, exit 1."""
    rc = tmp_path / "rc"
    monkeypatch.setattr("git_wt.cli._stdin_isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda *a: "n")
    result = runner.invoke(app, [*INSTALL, "bash", "--rcfile", str(rc)])
    assert result.exit_code == 1
    assert "aborted" in result.stderr
    assert not rc.exists()


def test_install_non_tty_proceeds_without_prompt(runner: CliRunner, tmp_path, monkeypatch):
    """Non-TTY (agents/scripts) proceeds without prompting."""
    rc = tmp_path / "rc"
    monkeypatch.setattr("git_wt.cli._stdin_isatty", lambda: False)
    result = runner.invoke(app, [*INSTALL, "bash", "--rcfile", str(rc)])
    assert result.exit_code == 0, result.stderr
    assert f"{PROG} completions show bash" in rc.read_text(encoding="utf-8")


def test_install_quiet_suppresses_stderr(runner: CliRunner, tmp_path):
    """--quiet silences the non-essential stderr hint lines."""
    result = runner.invoke(app, [*INSTALL, "bash", "--rcfile", str(tmp_path / "rc"), "--yes", "-q"])
    assert result.exit_code == 0
    assert result.stderr == ""


def test_install_snippet_direct(tmp_path):
    """install_snippet renders the marker block for each shell."""
    for shell in SUPPORTED:
        snippet = completions.install_snippet(PROG, shell)
        assert completions.START_MARKER.format(prog=PROG) in snippet
        assert completions.END_MARKER.format(prog=PROG) in snippet
        assert completions.eval_line(PROG, shell) in snippet


def test_runtime_completion_protocol_lists_subcommands():
    """Regression: the env-var completion server must work in a fresh process.

    typer >= 0.27 only registers its shell completion classes while
    building an app with add_completion=True; with add_completion=False
    the `_GIT_WT_COMPLETE=complete_bash` server died with "Shell bash
    not supported." on every keystroke (ble.sh fires it constantly).
    git_wt.cli.main() now registers the classes; this exercises the real
    subprocess path and asserts stderr stays empty.
    """
    env = {
        **os.environ,
        "_GIT_WT_COMPLETE": "complete_bash",
        "COMP_WORDS": "git-wt l",
        "COMP_CWORD": "1",
    }
    r = subprocess.run(
        [sys.executable, "-c", "import sys; sys.argv = ['git-wt', '']; from git_wt.cli import main; main()"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "list" in r.stdout.split()
    assert r.stderr == "", r.stderr


def test_show_eval_line_discards_server_stderr():
    """The sourced eval line must never let the server print into the shell."""
    assert "2>/dev/null" in completions.eval_line(PROG, "bash")
    assert "2>/dev/null" in completions.eval_line(PROG, "fish")
    # zsh block includes compinit (required before compdef takes effect)
    assert "compinit" in completions.install_snippet(PROG, "zsh")
