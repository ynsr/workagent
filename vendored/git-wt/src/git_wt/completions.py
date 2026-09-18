"""Shell completion for git-wt: `completions show|install` + value callbacks.

Why this exists instead of Typer's built-in `--install-completion`: the app is
constructed with ``add_completion=False`` so there is exactly one completion
system. ``completions show <shell>`` prints a script for
``eval "$(git-wt completions show bash)"``; ``completions install`` drops that
eval line into ~/.bashrc / ~/.zshrc / fish config idempotently inside a marker
block (atomic write, backup kept).

Completion order (subcommands -> positional values -> flags) is NOT hand-coded:
the generated script asks the running ``git-wt`` process to resolve the cursor
position, so subcommands and ``-``/``--`` flags always complete correctly. The
only custom code is an ``autocompletion=`` callback per dynamic value (branch
names, worktree paths). Those callbacks MUST be fast, offline (LOCAL git state
only — never network), and never raise: return [] on any failure so Tab never
breaks the shell.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "PROG",
    "SUPPORTED_SHELLS",
    "RC_FILES",
    "START_MARKER",
    "END_MARKER",
    "detect_shell",
    "eval_line",
    "install_snippet",
    "install_completion",
    "complete_branch_names",
    "complete_worktree_paths",
    "get_completion_script",
]

PROG = "git-wt"

SUPPORTED_SHELLS = ("bash", "zsh", "fish")

RC_FILES = {
    "bash": "~/.bashrc",
    "zsh": "~/.zshrc",
    "fish": "~/.config/fish/config.fish",
}

START_MARKER = "# >>> {prog} completions >>>"
END_MARKER = "# <<< {prog} completions <<<"


def detect_shell() -> Optional[str]:
    """Best-effort shell name from $SHELL; None if unknown/unsupported."""
    shell = os.path.basename(os.environ.get("SHELL", "")).strip()
    return shell if shell in SUPPORTED_SHELLS else None


def eval_line(prog: str, shell: str) -> str:
    """The rc line the user sources. fish uses | source instead of $()."""
    if shell == "fish":
        return f"{prog} completions show fish | source"
    return f'eval "$({prog} completions show {shell})"'


def install_snippet(prog: str, shell: str) -> str:
    """Marker block written into the rc file. zsh needs compinit first."""
    lines = [START_MARKER.format(prog=prog)]
    if shell == "zsh":
        lines.append(
            "autoload -U compinit && compinit  # required for completion (added by %s)"
            % prog
        )
    lines.append(eval_line(prog, shell))
    lines.append(END_MARKER.format(prog=prog))
    return "\n".join(lines) + "\n"


def install_completion(prog: str, shell: str, rcfile: Optional[Path] = None) -> tuple[Path, bool]:
    """Idempotently ensure the marker block is in the rc file.

    Returns (rc path, changed). Atomic write (tmp + rename); keeps a ``.bak``
    copy on first modification. Re-running is a no-op. A stale block from an
    older version is replaced, not duplicated.
    Raises ValueError on unsupported shell.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"unsupported shell {shell!r} (choose from: {', '.join(SUPPORTED_SHELLS)})"
        )
    rc = Path(rcfile).expanduser() if rcfile else Path(RC_FILES[shell]).expanduser()
    snippet = install_snippet(prog, shell)
    existing = rc.read_text(encoding="utf-8") if rc.is_file() else ""
    if snippet.strip() in existing:
        return rc, False
    # Replace a stale block from an older version rather than duplicating.
    pattern = re.compile(
        re.escape(START_MARKER.format(prog=prog))
        + r".*?"
        + re.escape(END_MARKER.format(prog=prog))
        + r"\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(snippet, existing)
    else:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        updated = existing + (sep + "\n" if existing else "") + snippet
    rc.parent.mkdir(parents=True, exist_ok=True)
    if rc.is_file():
        import shutil

        shutil.copy2(rc, rc.parent / (rc.name + ".bak"))  # backup before overwrite
    tmp = rc.parent / (rc.name + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(rc)
    return rc, True


# ── dynamic value completion (autocompletion= callbacks) ────────────
#
# Contract: read LOCAL state only (never network), filter on the `incomplete`
# prefix, never raise — any failure returns [] so Tab never breaks the shell.


def _local_branch_names() -> list[str]:
    """Local branch names from the repo at cwd (subprocess, local only)."""
    from .git_utils import _run_git

    out = _run_git(Path.cwd(), "branch", "--format", "%(refname:short)", check=False)
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _local_worktree_paths() -> list[str]:
    """Worktree checkout paths from the repo at cwd (local only)."""
    from .git_utils import GitRepo

    return [w.get("path", "") for w in GitRepo(Path.cwd()).worktree_paths() if w.get("path")]


def complete_branch_names(ctx: Any, incomplete: str) -> list[str]:
    """``autocompletion=`` callback: local branch names (e.g. for --branch/--resume)."""
    try:
        names = _local_branch_names()
    except Exception:
        return []
    return sorted(n for n in names if n.startswith(incomplete))


def complete_worktree_paths(ctx: Any, incomplete: str) -> list[str]:
    """``autocompletion=`` callback: worktree paths (e.g. for --worktree)."""
    try:
        paths = _local_worktree_paths()
    except Exception:
        return []
    return sorted(p for p in paths if p.startswith(incomplete))


# ── generated completion script ──────────────────────────────────────


def get_completion_script(prog: str, shell: str, click_cmd: Any = None) -> str:
    """Render the shell init script for *shell*.

    ``click_cmd`` is the resolved click command (``typer.main.get_command(app)``);
    pass it explicitly so this module never imports your app (no cycles). The
    script is generated from the shell completion classes shipped with Typer —
    never hand-coded. Raises ValueError on unsupported shell or missing cmd.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(
            f"unsupported shell {shell!r} (choose from: {', '.join(SUPPORTED_SHELLS)})"
        )
    if click_cmd is None:
        raise ValueError("click_cmd is required (pass typer.main.get_command(app))")
    try:  # typer >= 0.27 vendors click and ships its completion classes
        from typer._click import shell_completion
        from typer._completion_classes import completion_init
    except ImportError:  # older typer: plain click
        import click.shell_completion as shell_completion  # type: ignore[no-redef]

        def completion_init() -> None:
            pass

    completion_init()
    cls = shell_completion.get_completion_class(shell)
    if cls is None:
        raise ValueError(
            f"unsupported shell {shell!r} (choose from: {', '.join(SUPPORTED_SHELLS)})"
        )
    complete_var = f"_{prog.upper().replace('-', '_')}_COMPLETE"
    return cls(click_cmd, {}, prog, complete_var).source()
