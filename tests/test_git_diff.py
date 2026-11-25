"""Tests for src/workagent/git_diff.py (prodtest YAML plan reader)."""

from __future__ import annotations

from pathlib import Path

import pytest

from workagent import git_diff, store


def test_read_prodtest_plan(isolated_config: Path, tmp_path: Path) -> None:
    plan = tmp_path / "IPG-42.yaml"
    plan.write_text(
        "issue: IPG-42\n"
        "revision: 2\n"
        "risk: medium\n"
        "summary: Verify checkout endpoint\n"
        "preconditions:\n"
        "  - id: PRE-1\n"
        "    title: Service healthy\n"
        "cases:\n"
        "  - id: TC-1\n"
        "    title: Happy path\n"
        "    kind: smoke\n"
        "    priority: must\n"
        "    safety: read-only\n",
        encoding="utf-8",
    )
    store.upsert_prodtest(
        "IPG-42",
        status="pending",
        plan_path=str(plan),
        plan_revision=2,
        plan_risk="medium",
    )
    out = git_diff.read_prodtest_plan("IPG-42")
    assert out["issue_key"] == "IPG-42"
    assert out["plan_revision"] == 2
    assert out["plan_risk"] == "medium"
    assert out["parse_error"] is None
    assert out["summary"] == "Verify checkout endpoint"
    assert len(out["cases"]) == 1
    assert out["cases"][0]["id"] == "TC-1"
    assert len(out["preconditions"]) == 1

    with pytest.raises(FileNotFoundError):
        git_diff.read_prodtest_plan("UNKNOWN-999")
