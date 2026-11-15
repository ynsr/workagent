"""Hard-delete cascade + orphan sweep (issue #108).

runs.worktree_ref is an optional FK to worktrees(ref_key) with ON DELETE
CASCADE (v26): deleting a worktree row removes its runs, and every hard
delete path additionally purges flows, message worktree links, and child
parent labels. purge_orphans removes pre-FK leftovers with their
transcripts.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tests.cli_helpers import _invoke


def _init(db):
    from workagent import store_sqlite as sq
    sq.init_db(db)
    return sq


def _seed_link(db, key: str, *, parent: str = "") -> None:
    from workagent import store_sqlite as sq
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees"
            " (ref_key, path, branch, added_at, payload, parent_key_ref)"
            " VALUES (?, ?, 'b', '2026-01-01', '{}', ?)",
            (key, f"/tmp/{key}", parent))


def _legacy_db(path) -> None:
    """A pre-v26 DB: runs.worktree_ref NOT NULL DEFAULT '' and no FK."""
    from workagent import store_sqlite as sq
    old = sq.SCHEMA.replace(
        "worktree_ref TEXT REFERENCES worktrees(ref_key) ON DELETE CASCADE",
        "worktree_ref TEXT NOT NULL DEFAULT ''")
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.commit()
    conn.close()


def _legacy_run(conn, ref: str, *, file_path: str = "", metadata: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO runs (command, args, exit_code, created_at, worktree_ref,"
        " file_path, metadata) VALUES ('start', '[]', 0, '2026-01-01', ?, ?, ?)",
        (ref, file_path, json.dumps(metadata or {})))


def test_runs_worktree_ref_is_nullable_cascade_fk(tmp_path):
    db = tmp_path / "state.db"
    sq = _init(db)
    with sq.connect(db) as conn:
        fks = conn.execute("PRAGMA foreign_key_list(runs)").fetchall()
        assert any(r[2] == "worktrees" and r[3] == "worktree_ref"
                   and (r[6] or "").upper() == "CASCADE" for r in fks)
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(runs)")}
        assert cols["worktree_ref"][3] == 0  # nullable: NULL = no parent
    repo = sq.insert_run(db, "start", [], 0, worktree_ref="",
                         file_path="/f/repo.jsonl")
    with sq.connect(db) as conn:
        row = conn.execute("SELECT worktree_ref FROM runs WHERE id = ?",
                           (repo,)).fetchone()
    assert row[0] is None
    assert sq.get_run(db, repo)["worktree_ref"] == ""
    _seed_link(db, "k")
    sq.insert_run(db, "start", [], 0, worktree_ref="k", file_path="/f/k.jsonl")
    with pytest.raises(sqlite3.IntegrityError):
        sq.insert_run(db, "start", [], 0, worktree_ref="missing")


def test_hard_delete_cascades_runs_flows_messages_children(tmp_path):
    from workagent import store_links as sl
    db = tmp_path / "state.db"
    sq = _init(db)
    _seed_link(db, "k")
    _seed_link(db, "child", parent="k")
    _seed_link(db, "other")
    other = sq.insert_run(db, "start", [], 0, worktree_ref="other",
                          file_path="/f/other.jsonl")
    repo = sq.insert_run(db, "start", [], 0, worktree_ref="",
                         file_path="/f/repo.jsonl")
    sq.insert_run(db, "start", [], 0, worktree_ref="k", file_path="/f/k.jsonl")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO flows (template_label, worktree_ref,"
                     " created_at, modified_at)"
                     " VALUES ('tpl', 'k', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO messages (message_id, worktrees, added_at)"
                     " VALUES ('m1', ?, '2026-01-01')", (json.dumps(["k", "other"]),))
    sl.delete_worktree_row(db, "k")
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE worktree_ref = 'k'"
                            ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM flows WHERE worktree_ref = 'k'"
                            ).fetchone()[0] == 0
        assert json.loads(conn.execute("SELECT worktrees FROM messages WHERE"
                                       " message_id = 'm1'").fetchone()[0]) == ["other"]
        assert conn.execute("SELECT parent_key_ref FROM worktrees WHERE ref_key ="
                            " 'child'").fetchone()[0] == ""
    ids = {r["id"] for r in sq.list_runs(db, include_deleted_worktrees=True)}
    assert ids == {other, repo}


def test_save_links_prune_purges_flows_and_message_links(tmp_path):
    from workagent import store_links as sl
    db = tmp_path / "state.db"
    sq = _init(db)
    _seed_link(db, "keep")
    _seed_link(db, "gone")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO flows (template_label, worktree_ref,"
                     " created_at, modified_at)"
                     " VALUES ('tpl', 'gone', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO messages (message_id, worktrees, added_at)"
                     " VALUES ('m1', ?, '2026-01-01')", (json.dumps(["gone"]),))
    sl.save_links_rows(db, {"keep": {"worktree": "/tmp/keep", "branch": "b",
                                     "repo": "/tmp/rk"}})
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0] == 0
        assert json.loads(conn.execute("SELECT worktrees FROM messages"
                                       ).fetchone()[0]) == []


def test_repo_removal_purges_flows_and_message_links(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.register_repo_row(db, "proj", "/proj", "jira:IPG")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key,"
                     " added_at, payload)"
                     " VALUES ('k', '/tmp/k', 'b', 'proj', '2026-01-01', '{}')")
        conn.execute("INSERT INTO flows (template_label, worktree_ref,"
                     " created_at, modified_at)"
                     " VALUES ('tpl', 'k', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO messages (message_id, worktrees, added_at)"
                     " VALUES ('m1', ?, '2026-01-01')", (json.dumps(["k"]),))
    assert sq.remove_repo_row(db, "proj") is True
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0] == 0
        assert json.loads(conn.execute("SELECT worktrees FROM messages"
                                       ).fetchone()[0]) == []


def test_purge_orphans_removes_dangling_records_and_reports_transcripts(tmp_path):
    from workagent import store_links as sl
    db = tmp_path / "state.db"
    _legacy_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, added_at,"
                     " payload) VALUES ('live', '/tmp/live', 'b', '2026-01-01', '{}')")
        conn.execute("INSERT INTO runs (command, args, created_at, worktree_ref)"
                     " VALUES ('start', '[]', '2026-01-01', 'live')")
        conn.execute("INSERT INTO runs (command, args, created_at, worktree_ref)"
                     " VALUES ('start', '[]', '2026-01-01', '')")
        _legacy_run(conn, "gone", file_path="/runs/omp/x/gone.jsonl",
                    metadata={"session_dir": "/runs/omp/x"})
        _legacy_run(conn, "gone2", file_path="/runs/omp/y/gone2.jsonl")
        conn.execute("INSERT INTO flows (template_label, worktree_ref, created_at,"
                     " modified_at)"
                     " VALUES ('tpl', 'gone', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO messages (message_id, worktrees, added_at)"
                     " VALUES ('m1', ?, '2026-01-01')",
                     (json.dumps(["gone", "live"]),))
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, added_at,"
                     " payload, parent_key_ref)"
                     " VALUES ('kid', '/tmp/kid', 'b', '2026-01-01', '{}', 'gone')")
    sq = _init(db)  # v26 migration: '' -> NULL, dangling rows survive

    dry = sl.purge_orphans(db, dry_run=True)
    assert dry["runs"] == 2 and dry["flows"] == 1 and dry["messages"] == 1
    assert dry["parent_links"] == 1
    assert dry["transcripts"] == ["/runs/omp/x", "/runs/omp/x/gone.jsonl",
                                  "/runs/omp/y/gone2.jsonl"]
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 4

    out = sl.purge_orphans(db)
    assert out == dry
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs"
                            " WHERE worktree_ref IN ('gone', 'gone2')").fetchone()[0] == 0
        assert conn.execute("SELECT worktree_ref FROM runs").fetchall()[0][0] in (
            "live", None)
        assert conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0] == 0
        assert json.loads(conn.execute("SELECT worktrees FROM messages"
                                       ).fetchone()[0]) == ["live"]
        assert conn.execute("SELECT parent_key_ref FROM worktrees WHERE ref_key ="
                            " 'kid'").fetchone()[0] == ""
    # Idempotent: nothing left to sweep.
    assert sl.purge_orphans(db)["runs"] == 0


def test_link_purge_orphans_cli_deletes_rows_and_transcripts(isolated_config):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    _legacy_db(db)
    transcript = isolated_config / "runs" / "omp" / "feat-x" / "review" / "t.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    with sqlite3.connect(db) as conn:
        _legacy_run(conn, "gone", file_path=str(transcript))
    sq._INIT_CACHE.pop(str(db), None)

    preview = _invoke("link", "purge-orphans", "--dry-run", "--json")
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["orphans"]["runs"] == 1
    assert transcript.exists()

    r = _invoke("link", "purge-orphans", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)["orphans"]
    assert out["runs"] == 1 and out["transcripts"] == 1
    assert not transcript.exists()
    assert not transcript.parent.exists()  # emptied branch dirs pruned
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
