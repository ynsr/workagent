"""Task 2: link purge-deleted (bookkeeping only)."""

from workagent import cli, store

from tests.cli_helpers import runner


def test_purge_deleted_scopes_by_repo(isolated_config, tmp_path):
    store.record_link("jira:A-1", {"issue": "A-1",
        "worktree": str(tmp_path / "a"), "branch": "b1", "repo": "r1"})
    store.record_link("jira:B-2", {"issue": "B-2",
        "worktree": str(tmp_path / "b"), "branch": "b2", "repo": "r2"})
    store.soft_delete_worktree("jira:A-1")
    store.soft_delete_worktree("jira:B-2")
    r = runner.invoke(cli.app, ["link", "purge-deleted", "--repo", "r1",
                                "--json", "--yes"])
    assert r.exit_code == 0, r.output
    import json
    rows = {row["key"]: row for row in json.loads(r.stdout)["results"]}
    assert rows == {"jira:A-1": {"key": "jira:A-1", "branch": "b1",
                                 "status": "purged"}}
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:A-1" not in links and "jira:B-2" in links


def test_purge_deleted_empty_repo_is_noop(isolated_config):
    r = runner.invoke(cli.app, ["link", "purge-deleted", "--repo", "nope",
                                "--json", "--yes"])
    assert r.exit_code == 0, r.output
