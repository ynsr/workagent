"""Pure delivery-stage reducer for worktree status & Kanban board.

Derives three deterministic fields for every worktree at read time:
- ``kanban_column``: ``"working" | "needs_you" | "in_review" | "ready_to_merge"``
- ``display_status``: short human-readable actionable phrase
- ``attention_reason``: machine-readable reason when ``kanban_column == "needs_you"``,
  else ``None``

Strict precedence: ``needs_you`` > ``working`` > ``ready_to_merge`` > ``in_review``.
"""
from __future__ import annotations

from typing import Any, Literal, Sequence

KanbanColumn = Literal["working", "needs_you", "in_review", "ready_to_merge"]


def _flow_step_label(flow: dict[str, Any]) -> str:
    step = flow.get("current_step")
    if step is None:
        step = flow.get("step_index")
    steps = flow.get("steps")
    total = len(steps) if isinstance(steps, (list, tuple)) and steps else None
    if isinstance(step, int) and step >= 0:
        if total:
            return f"step {step + 1}/{total}"
        return f"step {step + 1}"
    return ""


def _run_cmd_label(run: dict[str, Any]) -> str:
    cmd = str(run.get("command") or "").strip()
    if not cmd:
        return ""
    return cmd.replace("-", " ").replace("_", " ").capitalize()


def derive_delivery_status(
    entry: dict[str, Any],
    active_runs: Sequence[dict[str, Any]] | None = None,
    active_flows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive Kanban column, display status, and attention reason for a worktree.

    Pure function — performs no I/O or store lookups.
    """
    runs = list(active_runs or [])
    flows = list(active_flows or [])

    pr_detail = entry.get("pr_detail") if isinstance(entry.get("pr_detail"), dict) else None
    pr_state = str((pr_detail or {}).get("state") or "").strip().lower()
    ci = str(entry.get("ci") or "").strip().lower()
    reviews_detail = (
        entry.get("reviews_detail") if isinstance(entry.get("reviews_detail"), dict) else None
    )
    unresolved = int((reviews_detail or {}).get("unresolved") or 0)
    commits_detail = (
        entry.get("commits_detail") if isinstance(entry.get("commits_detail"), dict) else None
    )
    behind = int((commits_detail or {}).get("behind") or 0) if commits_detail is not None else 0
    harness = str(entry.get("harness") or "").strip()
    prodtest = str(entry.get("prodtest") or "").strip().lower()

    # ── 1. needs_you (highest precedence) ─────────────────────────────
    for run in runs:
        if run.get("pending_question"):
            return {
                "kanban_column": "needs_you",
                "display_status": "Waiting for your answer",
                "attention_reason": "pending_question",
            }

    for run in runs:
        if str(run.get("state") or "").strip().lower() == "needs_input":
            cmd = _run_cmd_label(run)
            return {
                "kanban_column": "needs_you",
                "display_status": f"Needs input · {cmd}" if cmd else "Needs your input",
                "attention_reason": "needs_input",
            }

    for flow in flows:
        if str(flow.get("status") or "").strip().lower() == "paused":
            step_lbl = _flow_step_label(flow)
            return {
                "kanban_column": "needs_you",
                "display_status": f"Flow paused at {step_lbl}" if step_lbl else "Flow paused",
                "attention_reason": "flow_paused",
            }

    for run in runs:
        if str(run.get("state") or "").strip().lower() == "failed":
            cmd = _run_cmd_label(run)
            return {
                "kanban_column": "needs_you",
                "display_status": f"Run failed · {cmd}" if cmd else "Run failed",
                "attention_reason": "run_failed",
            }

    if ci == "failure":
        return {
            "kanban_column": "needs_you",
            "display_status": "CI failed",
            "attention_reason": "ci_failed",
        }

    if unresolved > 0:
        noun = "comment" if unresolved == 1 else "comments"
        return {
            "kanban_column": "needs_you",
            "display_status": f"{unresolved} unresolved {noun}",
            "attention_reason": "unresolved_comments",
        }

    # ── 2. working ────────────────────────────────────────────────────
    for flow in flows:
        if str(flow.get("status") or "").strip().lower() == "running":
            step_lbl = _flow_step_label(flow)
            return {
                "kanban_column": "working",
                "display_status": f"Flow {step_lbl}" if step_lbl else "Flow running",
                "attention_reason": None,
            }

    for run in runs:
        if str(run.get("state") or "").strip().lower() == "running":
            cmd = _run_cmd_label(run)
            return {
                "kanban_column": "working",
                "display_status": f"Working · {cmd}" if cmd else "Working",
                "attention_reason": None,
            }

    if harness:
        harness_name = harness.split()[0]
        return {
            "kanban_column": "working",
            "display_status": f"Working · {harness_name}",
            "attention_reason": None,
        }

    # ── 3. ready_to_merge ─────────────────────────────────────────────
    if pr_state == "merged":
        if prodtest in ("pending", "pending_prodtest"):
            status_text = "Merged · Pending prodtest"
        else:
            status_text = "Merged · Ready for cleanup"
        return {
            "kanban_column": "ready_to_merge",
            "display_status": status_text,
            "attention_reason": None,
        }

    if pr_state == "open" and ci == "success" and unresolved == 0 and behind == 0:
        return {
            "kanban_column": "ready_to_merge",
            "display_status": "Ready to merge",
            "attention_reason": None,
        }

    # ── 4. in_review (default / fallback) ─────────────────────────────
    if pr_state == "open":
        if ci == "running":
            status_text = "CI running"
        elif behind > 0:
            noun = "commit" if behind == 1 else "commits"
            status_text = f"Behind base by {behind} {noun}"
        else:
            status_text = "In review"
        return {
            "kanban_column": "in_review",
            "display_status": status_text,
            "attention_reason": None,
        }

    if pr_state == "closed":
        return {
            "kanban_column": "in_review",
            "display_status": "PR closed",
            "attention_reason": None,
        }

    return {
        "kanban_column": "in_review",
        "display_status": "No PR yet",
        "attention_reason": None,
    }
