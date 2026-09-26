# Worktree active/deactivated states + DB-only removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reversible deactivated state + DB-only row deletion for worktree links, with bulk ops skipping deactivated rows.

**Architecture:** `active` INTEGER column on `worktrees` (default 1) via idempotent migration; `load_links()` filters to active-only by default with `include_inactive` opt-in; new `link deactivate|reactivate|remove` CLI commands (pure DB, proxied to web via existing `POST /api/runs`); Dashboard gains Delete-from-DB/Deactivate buttons plus a collapsed searchable deactivated table.

**Tech Stack:** Python (Typer CLI, SQLite via `store_sqlite.py`/`store_links.py`), React + TS frontend (`web/src`), pytest, `tsc -b` + `vite build`.

**Spec:** `docs/superpowers/specs/2026-09-26-worktree-active-states-design.md`

## Global Constraints

- Mirror webapp runs over CLI: web dialog buttons run `link deactivate|reactivate|remove` as confirmed runs via existing `POST /api/runs`; no new endpoint except the `include_inactive` read path.
- `ONLY session-owned runs persist` — non-session `link` runs never mirror by design (unchanged).
- Human-readable labels with (?) tooltips: new dialog buttons use `CheckRow`/`FieldHelp` convention (`label (?)` with `title="--flag — description"`); no inline `--xxx` text.
- stdout carries ONLY data; every log/progress/confirmation line → stderr (CLI).
- `tsc -b --noEmit` AND `npm run build` must both pass (`npx tsc --noEmit -p .` can pass while vite fails on strict `TS6133`).
- Suite runs via `.venv/bin/python -m pytest tests -q` (system `python3` lacks deps).
- No behavior change to `cleanup <key>` full teardown; `link remove` is DB-row-only.
- `workagent serve` serves `web/dist` (gitignored); must `npm run build` after web changes.

## Review Focus

- Deactivating a worktree with a live harness still blocks bulk ops the same way (harness guard unchanged; deactivated check is additive).
- `link remove` on a key with session history also drops `sessions`/`runs` via CASCADE — the confirm copy must say history is deleted.
- `save_links_rows()` upsert must never reset `active` to 1 (regression: re-saving links resurrects deactivated rows).
- `link list` / `link remove` (old worktree-link path) must resolve deactivated keys too, or users can't manage rows they can't see.
- Deactivated table search filter must match the active table's `matchesQuery` semantics (same fields) or users report "missing" rows.

---

### Task 1: `active` column migration + filtered reads

**Files:**
- Modify: `src/workagent/store_sqlite.py:106-118` (add `_migrate_worktrees_active` next to `_migrate_runs_output`)
- Modify: `src/workagent/store_sqlite.py:281-310` (call it from `init_db`)
- Modify: `src/workagent/store_sqlite.py:25-29` (canonical `SCHEMA` worktrees DDL gains `active`)
- Modify: `src/workagent/store_links.py:15-36` (`load_links_rows` gains `include_inactive`)
- Test: `tests/test_store_sqlite.py` (append new tests)

**Interfaces:**
- Consumes: existing `_migrate_runs_output(conn)` pattern, `PRAGMA table_info` probe, `connect(path)`.
- Produces: `_migrate_worktrees_active(conn) -> None`; `load_links_rows(path, include_inactive=False) -> dict` with each entry carrying `active: int`.

- [ ] **Step 1: Write the failing tests**

```python
def test_migrate_worktrees_active_defaults_to_active(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('k1', '/tmp/w1', 'b1', '', '2026-01-01', '{}')")
    sq._migrate_worktrees_active(sq.connect(db))
    with sq.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
        assert "active" in cols
        assert conn.execute("SELECT active FROM worktrees WHERE ref_key='k1'").fetchone()[0] == 1


def test_migrate_worktrees_active_idempotent(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        sq._migrate_worktrees_active(conn)
        sq._migrate_worktrees_active(conn)
        with conn:
            conn.execute("UPDATE worktrees SET active=0 WHERE 0")


def test_load_links_rows_filters_inactive(tmp_path):
    from workagent import store_sqlite as sq, store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload, active)"
            " VALUES ('a', '/tmp/wa', 'ba', '', '2026-01-01', '{}', 1),"
            " ('d', '/tmp/wd', 'bd', '', '2026-01-01', '{}', 0)")
    assert set(sl.load_links_rows(db).keys()) == {"a"}
    all_rows = sl.load_links_rows(db, include_inactive=True)
    assert set(all_rows.keys()) == {"a", "d"}
    assert all_rows["d"]["active"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store_sqlite.py::test_migrate_worktrees_active_defaults_to_active tests/test_store_sqlite.py::test_migrate_worktrees_active_idempotent tests/test_store_sqlite.py::test_load_links_rows_filters_inactive -v`
Expected: FAIL (`_migrate_worktrees_active` not defined / `load_links_rows` takes no `include_inactive`).

- [ ] **Step 3: Write minimal implementation**

```python
def _migrate_worktrees_active(conn) -> None:
    """Schema v4: worktrees gains active (1 = active, 0 = deactivated).

    ALTER TABLE is enough (NOT NULL DEFAULT 1, so old rows read back as
    active). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
    if not cols:
        return
    if "active" not in cols:
        conn.execute("ALTER TABLE worktrees ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
```

In `init_db`, call `_migrate_worktrees_active(conn)` alongside `_migrate_runs_output(conn)`. In canonical `SCHEMA`, append `active INTEGER NOT NULL DEFAULT 1` to the `worktrees` DDL. In `store_links.load_links_rows(path, include_inactive=False)`: add `WHERE w.active != 0` (or `= 1`) unless `include_inactive`, and add `"active": r["active"]` to each entry dict.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store_sqlite.py -q`
Expected: PASS (all, incl. 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_links.py tests/test_store_sqlite.py
git commit -m "feat(db): worktree active flag with filtered reads"
```

### Task 2: `set_worktree_active` + `delete_worktree_row` mutations

**Files:**
- Modify: `src/workagent/store_links.py` (append two functions)
- Modify: `src/workagent/store.py` (re-export both, next to `load_links`/`save_links`)
- Test: `tests/test_store_sqlite.py` (append)

**Interfaces:**
- Consumes: `store_sqlite.connect`, `store_sqlite.init_db`, `HarnessError` for missing key (exit code 2, usage).
- Produces: `set_worktree_active(path: Path, key: str, active: bool) -> None` (raises `HarnessError` when key unknown); `delete_worktree_row(path: Path, key: str) -> None` (raises `HarnessError` when key unknown; row delete only, never touches disk).

- [ ] **Step 1: Write the failing tests**

```python
def test_set_worktree_active_round_trip(tmp_path):
    from workagent import store_sqlite as sq, store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('k1', '/tmp/w1', 'b1', '', '2026-01-01', '{}')")
    sl.set_worktree_active(db, "k1", False)
    assert "k1" not in sl.load_links_rows(db)
    assert sl.load_links_rows(db, include_inactive=True)["k1"]["active"] == 0
    sl.set_worktree_active(db, "k1", True)
    assert sl.load_links_rows(db)["k1"]["active"] == 1


def test_delete_worktree_row_leaves_disk(tmp_path):
    import sqlite3
    from workagent import store_sqlite as sq, store_links as sl
    db = tmp_path / "state.db"
    sq.init_db(db)
    wt = tmp_path / "wt1"
    wt.mkdir()
    with sq.connect(db) as conn:
        conn.execute(
            "INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at, payload)"
            " VALUES ('k1', ?, 'b1', '', '2026-01-01', '{}')", (str(wt),))
    sl.delete_worktree_row(db, "k1")
    assert wt.exists()
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM worktrees WHERE ref_key='k1'").fetchone()[0] == 0
    import pytest
    from workagent.errors import HarnessError
    with pytest.raises(HarnessError):
        sl.delete_worktree_row(db, "k1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store_sqlite.py::test_set_worktree_active_round_trip tests/test_store_sqlite.py::test_delete_worktree_row_leaves_disk -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Write minimal implementation**

```python
def set_worktree_active(path: Path, key: str, active: bool) -> None:
    """Flip a worktree's active flag (pure DB; no git/host side effects)."""
    from .errors import HarnessError
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("UPDATE worktrees SET active = ? WHERE ref_key = ?",
                           (1 if active else 0, key))
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)


def delete_worktree_row(path: Path, key: str) -> None:
    """Delete a worktree row only; files/branch/PR untouched.

    Note: sessions/runs CASCADE off the worktree row, so this key's
    session history is deleted too.
    """
    from .errors import HarnessError
    init_db(path)
    with connect(path) as conn:
        cur = conn.execute("DELETE FROM worktrees WHERE ref_key = ?", (key,))
        if cur.rowcount == 0:
            raise HarnessError(f"no worktree link for {key}", exit_code=2)
```

Re-export both names in `src/workagent/store.py` (facade) so `store.set_worktree_active` / `store.delete_worktree_row` work.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store_sqlite.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_links.py src/workagent/store.py tests/test_store_sqlite.py
git commit -m "feat(db): set_worktree_active + delete_worktree_row mutations"
```

### Task 3: `link deactivate|reactivate|remove` CLI + bulk skip

**Files:**
- Modify: `src/workagent/cli_manage.py:275-305` (add three commands after `link_remove`)
- Modify: `src/workagent/web_args.py:65-67` (`SUBCOMMANDS["link"]` gains the three names; `SPECS` confirm flags mirror)
- Modify: `src/workagent/cli_cleanup.py` (`--merged` loop skips `active == 0` with `skipped:deactivated` row)
- Modify: `src/workagent/cli_sync.py` (`--all` loop skips deactivated likewise)
- Modify: `src/workagent/cli_review.py` (`--all` loop skips deactivated likewise)
- Test: `tests/test_cli.py` or `tests/test_sync.py` (bulk-skip test); new `tests/test_link_active.py` for the three commands

**Interfaces:**
- Consumes: `store.set_worktree_active`, `store.delete_worktree_row`, `worktrees.resolve_worktree` (must resolve against `include_inactive` links so deactivated keys stay manageable), `_print_result`, `_catch_harness_errors`, existing `skipped:live-harness` row shape.
- Produces: CLI `link deactivate <key>`, `link reactivate <key>`, `link remove <key>` (DB-row delete only; confirm copy states session history is deleted); bulk loops emit `{"key","status":"skipped:deactivated"}` rows.

- [ ] **Step 1: Write the failing tests**

```python
def test_link_deactivate_reactivate_round_trip(runner, tmp_config):
    r = runner.invoke(app, ["link", "deactivate", "jira:IPG-1"])
    assert r.exit_code == 0
    assert "jira:IPG-1" not in store.load_links()
    r = runner.invoke(app, ["link", "reactivate", "jira:IPG-1"])
    assert r.exit_code == 0
    assert "jira:IPG-1" in store.load_links()


def test_link_remove_drops_row_only(runner, tmp_config):
    wt = tmp_config / "wt1"
    wt.mkdir()
    r = runner.invoke(app, ["link", "remove", "jira:IPG-1"])
    assert r.exit_code == 0
    assert wt.exists()


def test_sync_all_skips_deactivated(runner, tmp_config, capsys):
    store.set_worktree_active(store._sqlite_path(), "jira:IPG-9", False)
    r = runner.invoke(app, ["sync", "--all", "--yes"])
    assert r.exit_code == 0
    assert "skipped:deactivated" in r.stderr
```

(Adapt fixture names to the repo's existing CLI test fixtures — check `tests/test_cli.py` helpers for `runner`/`tmp_config` equivalents and use those verbatim.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_link_active.py -q`
Expected: FAIL (unknown command `deactivate`).

- [ ] **Step 3: Write minimal implementation**

```python
@link_app.command("deactivate")
@_catch_harness_errors
def link_deactivate(
    ref: str = typer.Argument(..., help="Worktree key (issue/PR ref, branch, or worktree path)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Deactivate a worktree link (pure DB flag; files/branch/PR untouched)."""
    resolved = worktrees.resolve_worktree(ref, store.load_links(include_inactive=True))
    if resolved is None:
        _fail(f"no worktree link for {ref}", EXIT_USAGE)
    store.set_worktree_active(store._sqlite_path(), resolved, False)
    _print_result({"deactivated": resolved}, json_output)
```

Mirror for `reactivate` (True). `link remove <key>`: resolve via `include_inactive`, call `store.delete_worktree_row`, result `{"removed": resolved}` with eprint note that session history is deleted. Bulk loops: after the live-harness guard, add `if entry.get("active", 1) == 0: rows.append({... "status": "skipped:deactivated"}); continue`. Also update `link_remove`'s worktree branch to resolve `include_inactive` so deactivated keys stay removable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_link_active.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_manage.py src/workagent/web_args.py src/workagent/cli_cleanup.py src/workagent/cli_sync.py src/workagent/cli_review.py tests/test_link_active.py
git commit -m "feat(cli): link deactivate/reactivate/remove, bulk ops skip deactivated"
```

### Task 4: Web read path for deactivated rows

**Files:**
- Modify: `src/workagent/webapp.py:144-150` (`status` gains `include_inactive: bool = False`, passes to `store.load_links`)
- Modify: `web/src/lib/api.ts:292-296` (`statusAll` gains `{ refresh?, includeInactive? }`)
- Modify: `web/src/lib/queries.ts:25-30` (new `useStatusInactive` hook or param; new `queryKeys.statusInactive`)
- Test: `tests/test_webapp.py` (status with `?include_inactive=true` returns deactivated row; default hides it)

**Interfaces:**
- Consumes: `store.load_links(include_inactive=...)` (facade must accept and forward the kwarg — add it in Task 1 if the facade lacks it).
- Produces: `GET /api/status?include_inactive=true -> WorktreeMap` (deactivated included); `api.statusAll({includeInactive: true})`; `useStatusInactive()` hook.

- [ ] **Step 1: Write the failing test**

```python
def test_status_include_inactive(client, tmp_config):
    store.set_worktree_active(store._sqlite_path(), "jira:IPG-9", False)
    assert "jira:IPG-9" not in client.get("/api/status").json()
    body = client.get("/api/status", params={"include_inactive": "true"}).json()
    assert "jira:IPG-9" in body
```

(Adapt to the repo's existing webapp test-client fixture names.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_webapp.py::test_status_include_inactive -v`
Expected: FAIL (unexpected query param / key still hidden).

- [ ] **Step 3: Write minimal implementation**

```python
@app.get("/api/status")
def status(ref: str | None = None, refresh: bool = False, include_inactive: bool = False):
    links = store.load_links(include_inactive=include_inactive)
    ...
```

Frontend `api.statusAll`: append `include_inactive` query param when `opts.includeInactive`. `queries.ts`: add `statusInactive: ["status", "inactive"]` key + `useStatusInactive()` calling `api.statusAll({ includeInactive: true })` with the same 15s refetch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_webapp.py -q && cd web && npx tsc -b --noEmit`
Expected: PASS, no TS errors.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/webapp.py web/src/lib/api.ts web/src/lib/queries.ts tests/test_webapp.py
git commit -m "feat(web): include_inactive status read path"
```

### Task 5: CleanupDialog Delete-from-DB + Deactivate buttons

**Files:**
- Modify: `web/src/components/CleanupDialog.tsx` (extend `onClose` union, add two footer buttons)
- Modify: `web/src/pages/Dashboard.tsx:233-246` (`submitCleanup` handles the three outcomes)
- Test: manual via `npm run build` + click-through (dialog is thin wiring over `createRun`)

**Interfaces:**
- Consumes: `useCreateRun` (`createRun.mutateAsync({command: "link", args: ["deactivate"|"remove", key], confirm: true})`), existing `CleanupDialog` props.
- Produces: `onClose` accepts `false | { force: boolean } | { action: "deactivate" } | { action: "deleteFromDb" }`; footer shows Remove (destructive, existing) + "Delete from DB" + "Deactivate" with `FieldHelp`-style tooltips (no inline `--xxx`).

- [ ] **Step 1: Extend the dialog result type and buttons**

```tsx
onClose: (ok: false | { force: boolean } | { action: "deactivate" } | { action: "deleteFromDb" }) => void
```

Add descriptive copy: Delete from DB — "Drops the database row only. Worktree files, branch and PR are left untouched; this key's session history is deleted." Deactivate — "Hides this worktree from bulk operations. Reversible at any time; nothing on disk or the host is touched."

- [ ] **Step 2: Handle outcomes in `submitCleanup`**

```tsx
async function submitCleanup(key: string, res: { force: boolean } | { action: "deactivate" } | { action: "deleteFromDb" }) {
  setCleanupTarget(null)
  if ("action" in res) {
    const cmd = res.action === "deactivate" ? ["deactivate", key] : ["remove", key]
    const { run_id } = await createRun.mutateAsync({ command: "link", args: cmd, confirm: true })
    runCreated(run_id, res.action === "deactivate" ? `Deactivated ${key}` : `Deleted ${key} from DB`)
    return
  }
  // ... existing cleanup path
}
```

- [ ] **Step 3: Typecheck + build**

Run: `cd web && npx tsc -b --noEmit && npm run build`
Expected: clean, `✓ built`.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/CleanupDialog.tsx web/src/pages/Dashboard.tsx
git commit -m "feat(web): CleanupDialog Delete-from-DB + Deactivate actions"
```

### Task 6: Deactivated table (collapsed, searchable, Reactivate)

**Files:**
- Create: `web/src/components/DeactivatedTable.tsx` (collapsed section + search + `StatusTable` reuse with reactivate-only actions)
- Modify: `web/src/pages/Dashboard.tsx` (render below active table; update 4 bulk-button descriptions to "active linked worktrees")
- Test: `npx tsc -b --noEmit` + `npm run build` + click-through

**Interfaces:**
- Consumes: `useStatusInactive()`, `StatusTable` + `StatusTableActions`, `matchesQuery` from `StatusCells`, `createRun` for `link reactivate`.
- Produces: `<DeactivatedTable onReactivate />` — collapsed by default (`<details>` or explicit toggle "Show deactivated worktrees (N)"), search input filtering via `matchesQuery`, rows muted (`bg-muted/40`) with pause icon, single Reactivate button per row.

- [ ] **Step 1: Create the component**

```tsx
export function DeactivatedTable({ worktrees }: { worktrees: WorktreeMap }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const keys = Object.keys(worktrees)
  const filtered = Object.fromEntries(Object.entries(worktrees).filter(([k, e]) => matchesQuery(k, e, q)))
  if (keys.length === 0) return null
  return (
    <section className="mt-4 rounded-md border bg-muted/40">
      <button onClick={() => setOpen((o) => !o)}>
        {open ? "Hide" : "Show"} deactivated worktrees ({keys.length})
      </button>
      {open && (
        <>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter deactivated worktrees" />
          <StatusTable worktrees={filtered} actions={{ onOpenWorktree, onReactivate }} ... />
        </>
      )}
    </section>
  )
}
```

`StatusTableActions` needs an optional `onReactivate?: (key: string) => void`; `StatusActions` row buttons render Reactivate (only) when provided — check `StatusActions.tsx` for the row-button pattern and follow it. Reactivate runs `link reactivate <key>` as a confirmed run, then invalidates `statusAll` + inactive queries.

- [ ] **Step 2: Wire into Dashboard + update bulk copy**

Render `<DeactivatedTable>` below the active table. Change the four bulk descriptions: "every linked worktree" → "every active linked worktree" (Sync all, Review all, Fix all PR comments, Cleanup merged).

- [ ] **Step 3: Typecheck + build + full suite**

Run: `cd web && npx tsc -b --noEmit && npm run build`
Expected: clean. Then: `.venv/bin/python -m pytest tests -q`
Expected: 439 + new tests PASS.

- [ ] **Step 4: Commit + rebuild dist + reinstall**

```bash
git add web/src/components/DeactivatedTable.tsx web/src/components/StatusTable.tsx web/src/components/StatusActions.tsx web/src/pages/Dashboard.tsx
git commit -m "feat(web): deactivated worktrees table with reactivate"
./install.sh
```
