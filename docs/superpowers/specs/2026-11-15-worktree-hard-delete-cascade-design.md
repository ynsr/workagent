# Worktree Hard Delete: Cascade + Orphan Sweep — Design Spec

> Issue: [#108](https://github.com/ynsr/workagent/issues/108) — Enhance Worktree Hard deletes.
> Branch: `feat/108--enhance-worktree-hard-deletes`.

## 1. Problem

A hard delete dropped the `worktrees` row and its `runs`/`flows` rows only.
Related records survived as dangling references: `runs.worktree_ref` and
`flows.worktree_ref` had no FK (explicit purges were easy to miss), message
rows kept destroyed worktree keys in their `worktrees` JSON, child rows kept
a dead `parent_key_ref`, and the harness transcripts of a hard-deleted
worktree outlived the worktree (`cleanup --hard-delete` never removed them).
Nothing ever swept the leftovers that the missed paths produced.

## 2. Design

### 2.1 `runs.worktree_ref` is an optional cascade FK (schema v26)

```sql
worktree_ref TEXT REFERENCES worktrees(ref_key) ON DELETE CASCADE
```

- NULL is the FK's "no parent" value and means repo-scoped run (pre-link
  attempt, web run, release/prodtest step) — the historical `''` sentinel
  becomes NULL in the migration. Readers and the API keep `""`
  (`store_sqlite._run_dict` normalises NULL), so no consumer changes shape;
  `_NOT_DELETED` treats NULL as "visible, no worktree".
- SQLite cannot add a FK in place: `_migrate_runs_worktree_fk` rebuilds
  `runs` from the canonical DDL with `foreign_keys` off, copies rows,
  restores the AUTOINCREMENT high-water mark (`_restore_runs_seq`), then
  `UPDATE runs SET worktree_ref = NULL WHERE worktree_ref = ''`.
  Idempotent: skipped once the cascade FK and the nullable column exist;
  `_schema_current` probes both so a stale DB never caches as current.
- Inserts of an unknown ref now fail fast (`IntegrityError`). Call sites
  already guard (`key in load_links(...)`), and per-run recorders swallow
  the error — a bug no longer silently creates a dangling row.

### 2.2 Cascade on every teardown path

`store_links` owns the worktree lifecycle; `_purge_dependents_conn` deletes
the records with no FK:

| Record | Removal |
|---|---|
| `runs` | FK `ON DELETE CASCADE` on the worktree row |
| `flows` | explicit delete by `worktree_ref` |
| `messages.worktrees` (JSON) | the dead refs are stripped, the message row survives |
| `worktrees.parent_key_ref` | children are re-parented to top level (`''`) |
| harness transcripts | deleted from disk by the CLI layer (`_remove_transcripts`) |

Applied by `delete_worktree_row` (covers `cleanup --hard-delete`,
`link remove --hard-delete`, `link destroy-deleted`), `gc_soft_deleted`
(7-day GC + serve startup), `save_links_rows` (legacy prune), and
`remove_repo_row` (repo removal cascades its worktrees). `cleanup
--hard-delete` now snapshots the transcript targets before dropping the row
and deletes them after — the cascade reaches the AI harness sessions, not
just the DB rows.

`prodtest_items.worktree_ref` is deliberately **not** cascaded: it is an
issue-scoped ledger (`issue_key` PK) whose worktree ref is attribution that
survives teardown (`_ensure_prodtest_row`, issue #50). Deleting the ledger
row would destroy release/plan state; clearing the ref would break the
attribution. It never had an FK and is excluded from the orphan sweep.

### 2.3 Orphan sweep

`store_links.purge_orphans(path, dry_run=False)` reports
`{runs, flows, messages, parent_links, transcripts}` and, unless dry-run,
deletes: runs whose non-NULL `worktree_ref` has no `worktrees` row (pre-v26
leftovers, since the FK now prevents new ones), their flows, dangling
message refs, and dangling `parent_key_ref` labels. The orphan runs'
transcript targets (`file_path` + `metadata.session_dir`) are snapshotted
before the delete and returned, so `cli_cleanup_gc.purge_orphans` can remove
the files (`_remove_transcripts`). There is no blanket walk of the `runs/`
tree: detached run dirs (`run_store`), harness-minted files, and live
sessions share that root, so only recorded targets are touched.

Surfaced as `workagent link purge-orphans [--dry-run] [--json]` and run by
serve startup maintenance after the soft-delete GC (`run_startup_maintenance`
now returns `{"purged", "orphans", "pruned"}`). Idempotent; a second sweep
reports zeros.

## 3. Risks

- The v26 rebuild copies every run row; a crash between `DROP TABLE runs`
  and the rename loses the table only if SQLite does not roll back — the
  rebuild commits once at the end, so an interrupted run rolls back (same
  pattern as the v16 fold and the worktrees rebuild).
- Failing inserts on an unknown ref surface bugs that previously produced
  silent dangling rows (acceptable: the call sites guard and swallow).
- `link purge-orphans` deletes transcripts of dangling runs. Only recorded
  paths are removed, never a blanket tree walk, and the sweep is scoped to
  refs with no worktree row.

## 4. Tests

`tests/test_worktree_purge.py`: FK shape + cascade, dangling insert
rejected, hard delete / prune / repo removal cascade (runs, flows, message
refs, parent labels), orphan sweep counts + transcript targets + dry-run +
idempotence, CLI `--dry-run`/real purge deleting transcript files.
`tests/test_store_sqlite.py` and `tests/test_webapp_runs.py` seed worktree
rows for FK-backed refs; `tests/test_serve_startup.py` covers the new
maintenance key.
