"""Shell completions: static scripts + idempotent rc install."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .errors import HarnessError

COMMANDS = ["start", "review", "cleanup", "repo", "status", "doctor",
            "completion", "completion-install"]
GLOBAL_FLAGS = ["--json", "-v", "--verbose", "-q", "--quiet", "--version", "-h", "--help"]
START_FLAGS = ["--repo", "--depth", "--base", "--harness", "--no-tty", "--dry-run", "--yes"]
REVIEW_FLAGS = ["--repo", "--depth", "--harness", "--no-tty", "--dry-run", "--yes"]
CLEANUP_FLAGS = ["--force", "--yes", "--dry-run"]

_BASH = """\
# harness bash completion
_harness_complete() {
  local cur prev cmds="start review cleanup repo status doctor completion completion-install"
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "$cmds" -- "$cur") )
    return 0
  fi
  case "${COMP_WORDS[1]}" in
    start) COMPREPLY=( $(compgen -W "--repo --depth --base --harness --no-tty --dry-run --yes --json -v -q" -- "$cur") ) ;;
    review) COMPREPLY=( $(compgen -W "--repo --depth --harness --no-tty --dry-run --yes --json -v -q" -- "$cur") ) ;;
    cleanup) COMPREPLY=( $(compgen -W "--force --yes --dry-run --json -v -q" -- "$cur") ) ;;
    repo) COMPREPLY=( $(compgen -W "add list remove" -- "$cur") ) ;;
    *) COMPREPLY=( $(compgen -W "--json -v -q" -- "$cur") ) ;;
  esac
}
complete -F _harness_complete harness
"""

_ZSH = """\
#compdef harness
_harness() {
  local -a cmds=(start review cleanup repo status doctor completion completion-install)
  if (( CURRENT == 2 )); then
    compadd -a cmds
  else
    case "${words[2]}" in
      start) compadd -- --repo --depth --base --harness --no-tty --dry-run --yes --json -v -q ;;
      review) compadd -- --repo --depth --harness --no-tty --dry-run --yes --json -v -q ;;
      cleanup) compadd -- --force --yes --dry-run --json -v -q ;;
      repo) compadd -- add list remove ;;
      *) compadd -- --json -v -q ;;
    esac
  fi
}
_harness "$@"
"""

_FISH = """\
# harness fish completion
complete -c harness -f -n '__fish_use_subcommand' -a start -d 'Create worktree and launch'
complete -c harness -f -n '__fish_use_subcommand' -a review -d 'Review PR/MR'
complete -c harness -f -n '__fish_use_subcommand' -a cleanup -d 'Close and clean up'
complete -c harness -f -n '__fish_use_subcommand' -a repo -d 'Manage repos'
complete -c harness -f -n '__fish_use_subcommand' -a status -d 'Show links'
complete -c harness -f -n '__fish_use_subcommand' -a doctor -d 'Self-check'
for f in --repo --depth --base --harness --no-tty --dry-run --yes --json -v -q; complete -c harness -l (string trim -l -c - $f) -f; end
"""

SCRIPTS = {"bash": _BASH, "zsh": _ZSH, "fish": _FISH}
DEFAULT_RCFILES = {"bash": "~/.bashrc", "zsh": "~/.zshrc", "fish": "~/.config/fish/config.fish"}
MARKER_BEGIN = "# >>> harness completion >>>"
MARKER_END = "# <<< harness completion <<<"


def script(shell: str) -> str:
    try:
        return SCRIPTS[shell]
    except KeyError:
        raise HarnessError(f"unknown shell: {shell}", exit_code=2)


def detect_shell() -> str | None:
    shell = os.environ.get("SHELL", "")
    base = os.path.basename(shell)
    return base if base in SCRIPTS else None


def install(shell: str | None, rcfile: Path | None, yes: bool) -> dict:
    import sys
    shell = shell or detect_shell()
    if shell not in SCRIPTS:
        raise HarnessError("cannot detect shell; pass bash|zsh|fish explicitly", exit_code=2)
    rc = Path(str(rcfile) if rcfile else DEFAULT_RCFILES[shell]).expanduser()
    block = f"{MARKER_BEGIN}\neval \"$(harness completion {shell})\"\n{MARKER_END}\n"
    if shell == "fish":
        block = f"{MARKER_BEGIN}\nharness completion {shell} | source\n{MARKER_END}\n"
    existing = rc.read_text() if rc.exists() else ""
    if MARKER_BEGIN in existing and block.strip() in existing:
        return {"shell": shell, "rcfile": str(rc), "action": "already-installed"}
    if not yes and sys.stdin.isatty():
        try:
            answer = input(f"Edit {rc} to install {shell} completion? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            raise HarnessError("aborted", exit_code=2)
    # Replace stale block or append; atomic write with .bak backup.
    if MARKER_BEGIN in existing and MARKER_END in existing:
        before, rest = existing.split(MARKER_BEGIN, 1)
        _, after = rest.split(MARKER_END, 1)
        new = before + block + after.lstrip("\n")
    else:
        new = existing + ("" if existing.endswith("\n") or not existing else "\n") + block
    rc.parent.mkdir(parents=True, exist_ok=True)
    if rc.exists():
        shutil.copy2(rc, rc.with_suffix(rc.suffix + ".bak") if rc.suffix else Path(str(rc) + ".bak"))
    tmp = rc.with_suffix(".tmp")
    tmp.write_text(new)
    tmp.replace(rc)
    eprint(f"installed {shell} completion into {rc} (restart shell or: source {rc})")
    return {"shell": shell, "rcfile": str(rc), "action": "installed"}


def eprint(*a, **k):
    import sys
    print(*a, file=sys.stderr, **k)
