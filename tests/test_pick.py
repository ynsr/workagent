"""Tests for the shared arrow-key picker (harness.pick)."""

from __future__ import annotations

import io

from harness import pick as pick_mod


class _FakeTty:
    """stdin stand-in that looks like a TTY but needs no real fd."""

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise OSError("no real fd in tests")


def _feed(keys: str, options, label="pick one"):
    """Run pick_index with per-character key feed; return (index, output)."""
    chars = iter(keys)
    out = io.StringIO()
    idx = pick_mod.pick_index(label, options, read=lambda n: next(chars), stream=out)
    return idx, out.getvalue()


def test_non_tty_returns_none():
    out = io.StringIO()

    class _NotTty:
        def isatty(self):
            return False

    import sys as _sys
    old = _sys.stdin
    _sys.stdin = _NotTty()
    try:
        assert pick_mod.pick_index("label", ["a"]) is None
    finally:
        _sys.stdin = old
    assert out.getvalue() == ""


def test_enter_selects_first():
    idx, out = _feed("\r", ["a", "b"])
    assert idx == 0
    assert "pick one" in out


def test_down_moves_selection():
    idx, _ = _feed("\x1b[B\r", ["a", "b", "c"])
    assert idx == 1


def test_up_wraps_to_last():
    idx, _ = _feed("\x1b[A\r", ["a", "b", "c"])
    assert idx == 2


def test_q_aborts_returns_none():
    idx, _ = _feed("q", ["a", "b"])
    assert idx is None


def test_esc_aborts():
    idx, _ = _feed("\x1bx", ["a", "b"])
    assert idx is None


def test_empty_options_returns_none():
    idx, _ = _feed("\r", [])
    assert idx is None


def test_descriptions_render_dim_and_marker_tracks_selection():
    idx, out = _feed("\x1b[B\r", [("a", None), ("b", "the b option")])
    assert idx == 1
    assert "\x1b[2mthe b option\x1b[0m" in out
    # marker lands on the selected row (b), not on a
    lines = [ln for ln in out.splitlines() if "❯" in ln]
    assert len(lines) == 2
    assert "b" in lines[-1]


def test_plain_string_options_render_without_desc():
    idx, out = _feed("\r", ["a", "b"])
    assert idx == 0
    assert "\x1b[2m" not in out


def test_list_is_erased_when_done():
    _, out = _feed("\x1b[B\r", ["a", "b"])  # move once so a redraw happens
    assert out.endswith("\x1b[?25h")
    body = out[: -len("\x1b[?25h")]
    # every visible option line is erased again before the picker exits
    tail = body.rsplit("❯", 1)[-1]
    assert "\x1b[2K" in tail or tail.strip() == ""
    assert out.count("\x1b[2K") >= 4  # 2 initial + 1 redraw + 2 erase


def test_navigation_leaves_no_padding():
    """Up/down redraws must not accumulate leading padding (issue #4)."""
    options = ["aaa", "bbb", "ccc"]
    idx, out = _feed("\x1b[B\x1b[A\r", options)  # down, up, enter
    assert idx == 0
    # clear-then-draw: each navigation redraw erases the whole option
    # block (cursor up, one standalone erased line per option, cursor up
    # again) before drawing; without that pass, redrawn content piles
    # onto stale rows and the block drifts into leading-space padding
    up = "\x1b[1A" * len(options)
    erase_pass = "\x1b[2K\r\n" * len(options)
    erased = out.count(up + erase_pass + up)
    assert erased == 2, f"padding artifact: only {erased} of 2 redraws erase the block"


def test_option_lines_end_crlf():
    """Raw mode clears OPOST: every line the picker streams ends \r\n (issue #7)."""
    _, out = _feed("\r", ["a", "b"])
    lines = [ln for ln in out.splitlines() if "❯" in ln]
    assert len(lines) == 1  # the selected option line is streamed
    # a bare \n moves down without returning to column 0, so each redraw
    # staircases one full list-width right; every \n must be a \r\n
    assert out.count("\n") == out.count("\r\n")
    assert out.count("\r\n") >= 2  # and both option lines end in \r\n


def test_jk_navigation():
    idx, _ = _feed("jj\r", ["a", "b", "c"])
    assert idx == 2
    idx, _ = _feed("jk\r", ["a", "b", "c"])
    assert idx == 0


def test_picker_zero_and_single_options():
    idx, _ = _feed("\r", ["only"])
    assert idx == 0
    idx, out = _feed("\r", [])
    assert idx is None
    assert out == ""  # empty options return before anything is drawn
