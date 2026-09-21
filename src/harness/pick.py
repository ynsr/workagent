"""Interactive selection: one arrow-key picker for every multi-choice prompt.

Renders the option list to stderr (stdout stays data-only), moves the
cursor with ↑/↓ or j/k, selects with Enter, aborts with q, Esc, or
Ctrl-C. stdin is read in raw mode; the terminal is always restored.
Callers keep their own non-TTY error path — ``pick`` simply returns
``None`` when stdin is not a TTY or the user aborts, so a piped or
scripted run never blocks.

Each option is a string, or ``(item, description)`` — the description
renders dim/gray after the item and is optional.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

__all__ = ["pick", "pick_index"]

_CURSOR_DOWN = "\x1b[1B"
_CURSOR_UP = "\x1b[1A"
_ERASE_LINE = "\x1b[2K"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_DIM = "\x1b[2m"
_BOLD_CYAN = "\x1b[1;36m"
_RESET = "\x1b[0m"


def pick(label: str, options: list[str | tuple[str, str | None]]) -> int | None:
    """Arrow-key selection over the real terminal (alias of pick_index)."""
    return pick_index(label, options)


def _read_key(read: Callable[[int], str]) -> str:
    """Classify one keypress: enter/up/down/abort."""
    ch = read(1)
    if not ch:
        return "abort"  # EOF — never spin
    if ch in ("\r", "\n"):
        return "enter"
    if ch in ("q", "Q", "\x03", "\x04"):
        return "abort"
    if ch == "\x1b":
        nxt = read(1)
        if nxt != "[":
            return "abort"
        code = read(1)
        if code == "A":
            return "up"
        if code == "B":
            return "down"
        return "abort"
    return "other"

def _option_line(opt: str | tuple[str, str | None], selected: bool) -> str:
    name, *rest = opt if isinstance(opt, tuple) else (opt,)
    marker = f"{_BOLD_CYAN}❯{_RESET}" if selected else " "
    desc = f"  {_DIM}{rest[0]}{_RESET}" if rest and rest[0] else ""
    return f"{_ERASE_LINE}{marker} {name}{desc}\n"


def _draw(stream, options: list[str | tuple[str, str | None]], idx: int) -> None:
    for i, opt in enumerate(options):
        stream.write(_option_line(opt, i == idx))
    stream.flush()


def _erase(stream, n: int) -> None:
    """Remove the n option lines; cursor ends one line below the block."""
    stream.write(_CURSOR_UP * n)
    for _ in range(n):
        stream.write(_ERASE_LINE + _CURSOR_DOWN)
    stream.flush()


def _redraw(stream, options: list[str | tuple[str, str | None]], idx: int) -> None:
    """Erase the whole option block, then draw it again at *idx*."""
    stream.write(_CURSOR_UP * len(options))
    for _ in options:
        stream.write(_ERASE_LINE + "\n")
    stream.write(_CURSOR_UP * len(options))
    _draw(stream, options, idx)


def _raw_mode(fd: int):
    import termios
    import tty
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    return _ctx()


def pick_index(label: str, options: list[str | tuple[str, str | None]], *,
               read: Callable[[int], str] | None = None, stream=None) -> int | None:
    """Arrow-key selection over *options*; returns the chosen index or None.

    Non-TTY stdin (or empty *options*, or an abort key) → None. ``read``
    and ``stream`` are injection seams for tests.
    """
    stream = stream or sys.stderr
    if not options:
        return None
    raw = None
    if read is None:
        stdin = sys.stdin
        try:
            if not stdin.isatty():
                return None
            raw = _raw_mode(stdin.fileno())
        except (OSError, ValueError):
            return None
        read = stdin.read

    idx = 0
    stream.write(f"{label}\n")
    _draw(stream, options, 0)
    chosen: int | None = None
    try:
        if raw is not None:
            raw.__enter__()
        stream.write(_HIDE_CURSOR)
        try:
            while True:
                key = _read_key(read)
                if key in ("enter", "abort"):
                    chosen = None if key == "abort" else idx
                    break
                if key in ("up", "down"):
                    idx = (idx + (-1 if key == "up" else 1)) % len(options)
                    _redraw(stream, options, idx)
        finally:
            if raw is not None:
                raw.__exit__(None, None, None)
    finally:
        _erase(stream, len(options))
        stream.write(_SHOW_CURSOR)
        stream.flush()
    return chosen
