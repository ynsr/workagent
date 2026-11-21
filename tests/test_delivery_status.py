"""Unit tests for workagent.delivery_status.derive_delivery_status."""
from __future__ import annotations

import pytest

from workagent.delivery_status import derive_delivery_status


def test_pending_question_highest_precedence():
    entry = {
        "harness": "agy 1234",
        "ci": "success",
        "pr_detail": {"state": "open", "number": 10},
    }
    runs = [{"state": "running", "command": "start", "pending_question": {"qid": "q1", "text": "?"}}]
    flows = [{"status": "running", "current_step": 1, "steps": ["a", "b"]}]
    res = derive_delivery_status(entry, runs, flows)
    assert res == {
        "kanban_column": "needs_you",
        "display_status": "Waiting for your answer",
        "attention_reason": "pending_question",
    }


def test_needs_input_precedence_over_working():
    entry = {"harness": "omp 99"}
    runs = [{"state": "needs_input", "command": "sync"}]
    res = derive_delivery_status(entry, runs, [])
    assert res["kanban_column"] == "needs_you"
    assert res["attention_reason"] == "needs_input"
    assert "Sync" in res["display_status"]


def test_paused_flow_needs_you():
    entry = {"harness": "omp 99"}
    flows = [{"status": "paused", "current_step": 1, "steps": ["start", "review", "fix"]}]
    res = derive_delivery_status(entry, [], flows)
    assert res == {
        "kanban_column": "needs_you",
        "display_status": "Flow paused at step 2/3",
        "attention_reason": "flow_paused",
    }


def test_failed_run_needs_you():
    res = derive_delivery_status({}, [{"state": "failed", "command": "review"}], [])
    assert res == {
        "kanban_column": "needs_you",
        "display_status": "Run failed · Review",
        "attention_reason": "run_failed",
    }


def test_ci_failure_needs_you_over_working():
    entry = {
        "harness": "omp 42",
        "ci": "failure",
        "pr_detail": {"state": "open", "number": 5},
    }
    res = derive_delivery_status(entry, [], [])
    assert res == {
        "kanban_column": "needs_you",
        "display_status": "CI failed",
        "attention_reason": "ci_failed",
    }


def test_unresolved_comments_needs_you():
    entry = {
        "ci": "success",
        "pr_detail": {"state": "open", "number": 5},
        "reviews_detail": {"reviews": 2, "unresolved": 3, "resolved": 1},
    }
    res = derive_delivery_status(entry, [], [])
    assert res == {
        "kanban_column": "needs_you",
        "display_status": "3 unresolved comments",
        "attention_reason": "unresolved_comments",
    }


def test_single_unresolved_comment_singular():
    entry = {
        "reviews_detail": {"reviews": 1, "unresolved": 1, "resolved": 0},
    }
    res = derive_delivery_status(entry)
    assert res["display_status"] == "1 unresolved comment"
    assert res["attention_reason"] == "unresolved_comments"


def test_working_from_running_flow():
    flows = [{"status": "running", "current_step": 0, "steps": ["start", "review"]}]
    res = derive_delivery_status({}, [], flows)
    assert res == {
        "kanban_column": "working",
        "display_status": "Flow step 1/2",
        "attention_reason": None,
    }


def test_working_from_running_run():
    runs = [{"state": "running", "command": "start"}]
    res = derive_delivery_status({}, runs, [])
    assert res == {
        "kanban_column": "working",
        "display_status": "Working · Start",
        "attention_reason": None,
    }


def test_working_from_live_harness():
    entry = {"harness": "agy 5555", "pr_detail": {"state": "open"}, "ci": "success"}
    res = derive_delivery_status(entry, [], [])
    assert res == {
        "kanban_column": "working",
        "display_status": "Working · agy",
        "attention_reason": None,
    }


def test_ready_to_merge_clean_open_pr():
    entry = {
        "pr_detail": {"state": "open", "number": 42},
        "ci": "success",
        "reviews_detail": {"reviews": 1, "unresolved": 0, "resolved": 2},
        "commits_detail": {"behind": 0, "ahead": 3},
    }
    res = derive_delivery_status(entry)
    assert res == {
        "kanban_column": "ready_to_merge",
        "display_status": "Ready to merge",
        "attention_reason": None,
    }


def test_ready_to_merge_merged_pr_and_pending_prodtest():
    merged_clean = derive_delivery_status({"pr_detail": {"state": "merged"}})
    assert merged_clean == {
        "kanban_column": "ready_to_merge",
        "display_status": "Merged · Ready for cleanup",
        "attention_reason": None,
    }
    merged_pt = derive_delivery_status(
        {"pr_detail": {"state": "merged"}, "prodtest": "pending_prodtest"}
    )
    assert merged_pt == {
        "kanban_column": "ready_to_merge",
        "display_status": "Merged · Pending prodtest",
        "attention_reason": None,
    }


def test_in_review_variants():
    ci_running = derive_delivery_status(
        {"pr_detail": {"state": "open"}, "ci": "running"}
    )
    assert ci_running == {
        "kanban_column": "in_review",
        "display_status": "CI running",
        "attention_reason": None,
    }

    behind_pr = derive_delivery_status(
        {
            "pr_detail": {"state": "open"},
            "ci": "success",
            "commits_detail": {"behind": 4, "ahead": 1},
        }
    )
    assert behind_pr == {
        "kanban_column": "in_review",
        "display_status": "Behind base by 4 commits",
        "attention_reason": None,
    }

    open_pr = derive_delivery_status({"pr_detail": {"state": "open"}})
    assert open_pr == {
        "kanban_column": "in_review",
        "display_status": "In review",
        "attention_reason": None,
    }

    no_pr = derive_delivery_status({})
    assert no_pr == {
        "kanban_column": "in_review",
        "display_status": "No PR yet",
        "attention_reason": None,
    }


def test_flow_step_label_uses_effective_steps():
    paused = derive_delivery_status(
        {},
        active_flows=[{"status": "paused", "current_step": 1, "effective_steps": ["a", "b", "c"]}],
    )
    assert paused == {
        "kanban_column": "needs_you",
        "display_status": "Flow paused at step 2/3",
        "attention_reason": "flow_paused",
    }

    running = derive_delivery_status(
        {},
        active_flows=[{"status": "running", "current_step": 0, "effective_steps": ["a", "b"]}],
    )
    assert running == {
        "kanban_column": "working",
        "display_status": "Flow step 1/2",
        "attention_reason": None,
    }


def test_merged_and_closed_prs_ignore_ci_failure_and_unresolved_comments():
    merged_ci_fail = derive_delivery_status(
        {
            "pr_detail": {"state": "merged"},
            "ci": "failure",
            "reviews_detail": {"unresolved": 2},
        }
    )
    assert merged_ci_fail == {
        "kanban_column": "ready_to_merge",
        "display_status": "Merged · Ready for cleanup",
        "attention_reason": None,
    }

    closed_unresolved = derive_delivery_status(
        {
            "pr_detail": {"state": "closed"},
            "ci": "failure",
            "reviews_detail": {"unresolved": 1},
        }
    )
    assert closed_unresolved == {
        "kanban_column": "in_review",
        "display_status": "PR closed",
        "attention_reason": None,
    }
