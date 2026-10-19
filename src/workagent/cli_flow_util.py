"""Shared flow-config helpers (keeps cli_flow.py at its 500-line cap).

F1/F5 fixes live here: wizard `run_mode`/`extra-flags` translation plus the
single banned-substring scanner all guards converge on.
"""
from __future__ import annotations

import shlex


#: run_mode values the wizard emits → backend manual-mode flags.
_RUN_MODES = ("preview", "dry_run", "terminal")


def _coerce(value: str) -> object:
    """Coerce pure-digit flag values to int (e.g. `--depth 7`)."""
    return int(value) if value.isdigit() else value


def normalize_config(config: object) -> dict:
    """Translate UI-only keys to real runner keys (idempotent).

    - ``run_mode: preview|terminal`` → ``{"preview"|"terminal": True}``
      (``headless`` strips any manual-mode flags).
    - ``extra-flags``/``extra_flags``: ``"--depth 7 --no-tty"`` →
      ``{"depth": 7, "no_tty": True}`` (shlex-parsed; ``--k=v`` /
      ``--k v`` / bare boolean ``--k``; dashes normalized to
      underscores so keys match the ``_pick`` allowlists; pure-digit
      values coerced to int).
    Unknown keys pass through untouched; non-dict input → ``{}``.
    """
    if not isinstance(config, dict):
        return {}
    out = dict(config)
    mode = out.pop("run_mode", None)
    if isinstance(mode, str) and mode:
        for m in _RUN_MODES:
            out.pop(m, None)
        if mode in ("preview", "dry_run"):
            out["preview" if mode == "preview" else "dry_run"] = True
        elif mode == "terminal":
            out["terminal"] = True
        # "headless" (or unknown): manual flags already stripped above.
    raw_flags = out.pop("extra-flags", None)
    if raw_flags is None:
        raw_flags = out.pop("extra_flags", None)
    else:
        out.pop("extra_flags", None)
    if not isinstance(raw_flags, str) or not raw_flags.strip():
        return out
    try:
        tokens = shlex.split(raw_flags)
    except ValueError:
        tokens = raw_flags.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok.startswith("--"):
            continue
        body = tok[2:]
        if not body:
            continue
        if "=" in body:
            k, v = body.split("=", 1)
            out[k.replace("-", "_")] = _coerce(v)
            continue
        nxt = tokens[i] if i < len(tokens) else None
        if nxt is not None and not nxt.startswith("-"):
            out[body.replace("-", "_")] = _coerce(nxt)
            i += 1
        else:
            out[body.replace("-", "_")] = True
    return out


def has_banned(config: object, banned: tuple[str, ...]) -> str | None:
    """Scan config keys + string values for banned substrings.

    Single scanner for all flow banned-key guards (template validation,
    run fail-fast, cleanup runtime): dict keys and every string value
    anywhere in the structure are checked; the raw JSON blob is never
    scanned, so innocent prose (e.g. ``extra_prompt`` text) cannot trip it.
    Returns the first banned hit, else None.
    """
    from . import store_flows as _sf
    return _sf._config_has_banned(config, banned)
