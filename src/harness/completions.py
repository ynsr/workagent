"""Shell completion: `completions show|install` + value-completion helpers.

The app is constructed with ``add_completion=False`` so there is exactly one
completion system. ``completions show <shell>`` prints a script for
``eval "$(harness completions show bash)"``; ``completions install`` drops
that eval line into ~/.bashrc / ~/.zshrc idempotently inside a marker block
(atomic write, .bak backup kept).

Completion order (subcommands -> positional values -> flags) is NOT
hand-coded: Click resolves the cursor position and offers only valid next
tokens. The only custom code is an ``autocompletion=`` callback per dynamic
value (registered repo names). Those callbacks MUST be fast, offline, and
never raise — return [] on any failure so Tab never breaks the shell.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

__all__ = [
    "SUPPORTED_SHELLS",
    "RC_FILES",
    "START_MARKER",
    "END_MARKER",
    "detect_shell",
    "eval_line",
    "install_snippet",
    "install_completion",
    "ensure_completion_classes",
    "get_completion_script",
    "print_install_hint",
]

SUPPORTED_SHELLS = ("bash", "zsh", "fish")

RC_FILES = {
    "bash": "~/.bashrc",
    "zsh": "~/.zshrc",
    "fish": "~/.config/fish/config.fish",
}

START_MARKER = "# >>> {prog} completions >>>"
END_MARKER = "# <<< {prog} completions <<<"

def ensure_completion_classes() -> None:
    """Register Typer's shell completion classes for the runtime server.

    typer >= 0.27 vendors click but only registers its bash/zsh/fish
    completion classes inside ``completion_init()``, which the env-var
    completion server (``_HARNESS_COMPLETE=complete_<shell>``) never
    calls — without this every Tab dies with "Shell bash not
    supported." (ble.sh fires the server on every keystroke). Idempotent;
    no-op when typer pairs with a plain click that self-registers.
    """
    try:
        from typer._click import shell_completion
    except ImportError:  # typer with a real click: classes self-register
        return
    if not shell_completion.get_completion_class("bash"):
        from typer._completion_classes import completion_init
        completion_init()


def detect_shell() -> Optional[str]:
    """Best-effort shell name from $SHELL; None if unknown/unsupported."""
    shell = os.path.basename(os.environ.get("SHELL", "")).strip()
    return shell if shell in SUPPORTED_SHELLS else None


def eval_line(prog: str, shell: str) -> str:
    """The rc line the user sources. fish uses a pipe instead of $()."""
    if shell == "fish":
        return f"{prog} completions show fish 2>/dev/null | source"
    return f'eval "$({prog} completions show {shell} 2>/dev/null)"'


def install_snippet(prog: str, shell: str) -> str:
    """Marker block written into the rc file. zsh needs compinit first."""
    lines = [START_MARKER.format(prog=prog)]
    if shell == "zsh":
        lines.append("autoload -U compinit && compinit  # required for completion (added by %s)" % prog)
    lines.append(eval_line(prog, shell))
    lines.append(END_MARKER.format(prog=prog))
    return "\n".join(lines) + "\n"


def install_completion(prog: str, shell: str, rcfile: Optional[Path] = None) -> tuple[Path, bool]:
    """Idempotently ensure the marker block is in the rc file.

    Returns (rc path, changed). Atomic write (tmp + rename); keeps a
    ``.bak`` copy on first modification. Re-running is a no-op.
    Raises ValueError on unsupported shell.
    """
    if shell not in SUPPORTED_SHELLS:
        raise ValueError(f"unsupported shell {shell!r} (choose from: {', '.join(SUPPORTED_SHELLS)})")
    rc = Path(rcfile).expanduser() if rcfile else Path(RC_FILES[shell]).expanduser()
    snippet = install_snippet(prog, shell)
    existing = rc.read_text(encoding="utf-8") if rc.is_file() else ""
    if snippet.strip() in existing:
        return rc, False
    # Replace a stale block from an older version rather than duplicating;
    # also strip blocks written by the pre-0.2.0 `completion-install`
    # (singular "completion" markers, pointing at a command that no longer
    # exists).
    legacy_start = START_MARKER.format(prog=prog).replace("completions", "completion")
    legacy_end = END_MARKER.format(prog=prog).replace("completions", "completion")
    pattern = re.compile(
        re.escape(START_MARKER.format(prog=prog)) + r".*?" + re.escape(END_MARKER.format(prog=prog)) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(snippet, existing)
    else:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        updated = existing + (sep + "\n" if existing else "") + snippet
    updated = re.sub(
        re.escape(legacy_start) + r".*?" + re.escape(legacy_end) + r"\n?",
        "",
        updated,
        flags=re.DOTALL,
    )
    rc.parent.mkdir(parents=True, exist_ok=True)
    if rc.is_file():
        shutil.copy2(rc, rc.parent / (rc.name + ".bak"))  # backup before overwrite
    tmp = rc.parent / (rc.name + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(rc)
    return rc, True


def complete_names(list_fn: Callable[[], object]) -> Callable:
    """Build an ``autocompletion=`` callback over locally stored names.

    ``list_fn`` returns an iterable of names (read LOCAL state only, never
    the network); wrapped so ANY failure (missing dir, bad JSON) yields []
    instead of breaking Tab.
    """


    def _complete(ctx, incomplete: str) -> list[str]:
        try:
            raw = list_fn()
            names = list(raw.keys()) if isinstance(raw, dict) else list(raw)
        except Exception:
            return []
        return sorted(n for n in names if n.startswith(incomplete))
    return _complete


def _cwd_git_branches() -> list[str]:
    """Local branch names in the current working directory (offline, fast).

    Raises on failure (not a repo, git missing) — ``complete_names`` turns
    that into [] so Tab never breaks the shell.
    """
    out = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                         capture_output=True, text=True, timeout=2, check=True).stdout
    return out.split()


def get_completion_script(prog: str, shell: str, click_cmd=None) -> str:
    """Render the Click-generated completion script for *shell*.

    ``click_cmd`` is the resolved click command (``typer.main.get_command(app)``);
    pass it explicitly so this module never imports your app (no cycles).
    Typer vendors click (``typer._click``) and registers its shell completion
    classes lazily via ``completion_init``. Raises ValueError on unsupported
    shell.
    """
    from typer import _completion_classes
    from typer import _click

    if shell not in SUPPORTED_SHELLS:
        raise ValueError(f"unsupported shell {shell!r} (choose from: {', '.join(SUPPORTED_SHELLS)})")
    if click_cmd is None:
        raise ValueError("click_cmd is required (pass typer.main.get_command(app))")
    _completion_classes.completion_init()
    cls = _click.shell_completion.get_completion_class(shell)
    complete_var = f"_{prog.upper().replace('-', '_')}_COMPLETE"
    return cls(click_cmd, {}, prog, complete_var).source()


def print_install_hint(prog: str, shell: Optional[str], rc: Path, file=sys.stderr) -> None:
    """Tell the user to reload — completion needs a fresh shell."""
    print(f"installed {prog} completion for {shell} in {rc}", file=file)
    print(f"restart your shell or run: source {rc}", file=file)


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)
