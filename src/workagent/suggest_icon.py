"""AI-suggested Lucide icons (issue #62): shared prompt + parsing + validation.

Runs ONE bare judge-style harness session (cheap model, no skills/tools/
rules/context — ``harness.judge_argv``) in a temp dir (no repo worktree
needed), parses the model answer down to a single icon name, and validates
it against the curated frontend list (``web/src/components/IconPicker.tsx``
``ACTION_ICONS`` — mirrored here so the backend stays the source of truth
for theSuggest contract without importing the frontend).
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

#: Curated icon list (mirrors web/src/components/IconPicker.tsx ACTION_ICONS).
SUGGEST_ICON_CHOICES = (
    "Zap", "Sparkles", "Wand2", "Star", "Gem", "Lightbulb", "Bot", "Brain",
    "FileText", "Files", "FilePlus", "FileSearch", "ScrollText",
    "ClipboardList", "ClipboardCheck", "BookOpen",
    "GitBranch", "GitPullRequest", "GitMerge", "Split", "Diff", "History",
    "Repeat",
    "TestTube", "TestTubes", "FlaskConical", "CheckCheck", "ListChecks",
    "ShieldCheck", "CircleCheck", "BadgeCheck",
    "Rocket", "Play", "Send", "Terminal", "Command", "Settings",
    "SlidersHorizontal", "Wrench", "Hammer",
    "Search", "Eye", "Radar", "Compass", "Target", "Flag", "Tag", "Gauge",
    "Timer", "Activity",
    "MessageSquare", "ThumbsUp", "Award", "Trophy", "Package", "Boxes",
    "Layers", "Workflow", "Network",
    "PenLine", "Bug", "Shield", "Swords", "KeyRound", "Lock", "Database",
    "Globe", "Braces",
    "Calendar", "Clock", "Bell", "Inbox", "Archive", "Folder", "Kanban",
    "Copy", "Link",
    "Palette", "Image", "Cpu", "Gift", "Smile", "Ghost",
)

_FALLBACK_ICON = "Zap"


def suggest_prompt(*, kind: str, name: str, label: str, prompt: str,
                   steps: str, pre: str, post: str) -> str:
    """Single inline prompt for the bare judge-style icon session.

    Everything the model needs travels inline (no repo context) — this is
    what keeps the call cheap. The contract: answer with exactly one icon
    name on the last line.
    """
    return (
        "Pick the single best Lucide icon name for this workagent "
        f"{kind} (custom action / flow template button).\n"
        f"Name: {(name or '').strip()[:200]}\n"
        f"Label: {(label or '').strip()[:200]}\n"
        f"Prompt: {(prompt or '').strip()[:2000]}\n"
        f"Steps: {(steps or '').strip()[:4000]}\n"
        f"Pre-execution script: {(pre or '').strip()[:2000]}\n"
        f"Post-execution script: {(post or '').strip()[:2000]}\n\n"
        "Valid choices (pick exactly one, prefer a specific match over "
        "a generic one):\n"
        + ", ".join(SUGGEST_ICON_CHOICES)
        + "\n\nReply with exactly the icon name on the last line, e.g.:\n"
        "Rocket"
    )


def parse_suggested_icon(text: str) -> str | None:
    """Parse the model answer down to one icon name (None when unusable).

    Accepts a bare trailing token (``Rocket``) or a trailing JSON line
    (``{\"icon\": \"Rocket\"}`` — the ``extract_append_prompt`` convention);
    matching is case-insensitive but the returned value keeps the canonical
    curated casing.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    last = ""
    for line in text.strip().splitlines():
        line = line.strip().strip("\"'`")
        if line:
            last = line
    if not last:
        return None
    try:
        obj = json.loads(last)
        if isinstance(obj, dict):
            cand = str(obj.get("icon", "") or "").strip()
            if cand:
                last = cand
    except (json.JSONDecodeError, ValueError):
        pass
    token = re.sub(r"[^A-Za-z0-9_-]", "", last.split()[-1] if last.split() else "")
    if not token:
        return None
    lowered = {c.lower(): c for c in SUGGEST_ICON_CHOICES}
    return lowered.get(token.lower())


def validate_suggested_icon(icon: str | None) -> str:
    """Validated icon name; raises HarnessError (exit 2) when unusable."""
    from .errors import HarnessError
    if not icon:
        raise HarnessError(
            "the AI session returned no usable icon — try again or pick one manually",
            exit_code=2)
    return icon


def run_suggest_icon(prompt_text: str, *, harness: str = "omp",
                     timeout_s: float = 120) -> str:
    """Run ONE bare judge-style harness session and return its last text.

    Cheap by construction: ``judge_argv`` (``--smol``, no skills/tools/
    rules/context/LSP/extensions/pty) in a fresh temp dir — the prompt
    carries everything inline, no repo checkout needed. Raises
    HarnessError when the harness is missing, times out, exits non-zero,
    or mints no transcript.
    """
    from . import backend
    from . import harness_summary as _hs
    from . import store_sqlite as _sq
    from .errors import HarnessError
    try:
        harness_obj = backend.get_harness(harness)
    except HarnessError:
        raise HarnessError(
            f"unsupported harness: {harness!r}", exit_code=2) from None
    with tempfile.TemporaryDirectory(prefix="workagent-suggest-icon-") as directory:
        judge_path = str(Path(directory) / "judge.jsonl")
        flags = harness_obj.session_file_flag(judge_path)
        try:
            argv_extra = [*harness_obj.judge_argv, *flags]
            code = backend.launch(
                harness, prompt_text, directory, True, argv_extra,
                env_file=None, watch=None, timeout_s=timeout_s)
        except HarnessError:
            raise
        except Exception as e:
            raise HarnessError(f"icon suggestion failed: {e}", exit_code=1) from e
        minted = _sq.newest_transcript(Path(directory))
        if code != 0 or minted is None:
            raise HarnessError(
                f"icon suggestion failed (exit {code}) — "
                "the AI session produced no answer", exit_code=1)
        text = _hs.last_assistant_text(str(minted), max_chars=None)
        if not (text or "").strip():
            raise HarnessError(
                "icon suggestion produced an empty answer — try again",
                exit_code=1)
        return text
