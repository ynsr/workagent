"""Web flow endpoints: templates list/detail + flows filter (Task 3)."""

from __future__ import annotations

from tests.webapp_helpers import client  # noqa: F401 (pytest fixture)
from workagent import store
from workagent.webapp import _target_for, _validate_args


def test_flow_endpoints_list_templates(client):
    store.create_template(name="Full", label="full",
                          steps='[{"kind": "start"}]')
    body = client.get("/api/flow-templates").json()
    assert [t["label"] for t in body["templates"]] == ["full"]
    detail = client.get("/api/flow-templates/full").json()
    assert detail["name"] == "Full"
    assert client.get("/api/flow-templates/nope").status_code == 404


def test_flow_endpoints_filter_by_worktree(client):
    store.create_template(name="Full", label="full",
                          steps='[{"kind": "start"}]')
    store.create_flow(template_label="full", worktree_ref="jira:IPG-1")
    store.create_flow(template_label="full", worktree_ref="jira:IPG-2")
    body = client.get("/api/flows").json()
    assert len(body["flows"]) == 2
    scoped = client.get("/api/flows", params={"worktree": "jira:IPG-1"}).json()
    assert [f["worktree_ref"] for f in scoped["flows"]] == ["jira:IPG-1"]


def test_flow_run_validates_and_targets(client):
    _validate_args("flow", ["run", "full", "IPG-1", "--dry-run"])
    assert _target_for("flow", ["run", "full", "IPG-1"]) == "flow:full:IPG-1"
    _validate_args("flow", ["template", "list"])
