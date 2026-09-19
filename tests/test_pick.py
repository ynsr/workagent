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
