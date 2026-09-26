# Worktree active/deactivated states + DB-only removal — design

Date: 2026-09-26. Status: approved (all 4 sections). Approach A.

## 1. Intent

- "Remove worktree xxx" modal (`CleanupDialog`) gains **Delete from DB**
  (drop the `worktrees` row only; files/branch/PR untouched) and
  **Deactivate** (reversible flag flip, no git/host side effects).
- Deactivated worktrees live in a collapsed, searchable table below the
  active table, visually distinct, with a per-row **Reactivate** button.
- Review All, Sync All, Fix all PR comments, Cleanup merged (web buttons
  AND CLI `sync --all`, `review --all`, `cleanup --merged`) operate on
  active worktrees only.

## 2. Data model & read path (Section 1, approved)

- Migration `_migrate_worktrees_active` in `store_sqlite.py`, following
  the existing `_migrate_*` idempotent pattern (`PRAGMA table_info`
  probe): `ALTER TABLE worktrees ADD COLUMN active INTEGER NOT NULL
  DEFAULT 1`. Existing rows default to active; no backfill needed.
- `store_links.load_links_rows()` gains `include_inactive=False`; each
  entry carries `active` (1/0) so the UI can split tables. All default
  callers (status, `sync`/`review`/`cleanup --all`, candidates dedup,
  `GET /api/status`) see active-only with no code change at call sites.
- `save_links_rows()` preserves `active` on upsert (never resets to 1).
- New mutations in `store_links.py` (pure DB, no git/host touch):
  - `set_worktree_active(path, key, active)` — flag flip.
  - `delete_worktree_row(path, key)` — row delete only.
- Accepted cost: `sessions`/`runs` CASCADE off the worktree row, so
  Delete-from-DB also drops that key's session history.

## 3. CLI & API surface (Section 2, approved)

- New `link` subcommands (bookkeeping group, no git/host side effects):
  - `link deactivate <key>` → `set_worktree_active(..., 0)`.
  - `link reactivate <key>` → `set_worktree_active(..., 1)`.
  - `link remove <key>` → `delete_worktree_row(...)` (DB-row delete
    only; distinct from `cleanup <key>`, which keeps the full git/host
    teardown).
- Bulk CLIs need no flag changes: active-only `load_links()` makes
  deactivated rows skip with a `skipped:deactivated` stderr note,
  mirroring the existing `skipped:live-harness` rows.
- Web: `POST /api/runs` already proxies CLI argv — dialog buttons run
  `link deactivate|reactivate|remove` as confirmed runs, no new endpoint.
- `GET /api/status` stays active-only; deactivated rows served via
  `GET /api/status?include_inactive=1` (or a `deactivated` payload key).

## 4. Dashboard UI (Section 3, approved)

- `CleanupDialog` footer: destructive Remove (unchanged) + "Delete from
  DB" (bookkeeping-only) + "Deactivate" (flag flip).
- Below the active `StatusTable`: collapsed-by-default section ("Show
  deactivated worktrees (N)") with a search/filter input. Deactivated
  table reuses `StatusTable` columns; row actions collapse to a single
  Reactivate button per row; muted background + pause icon for visual
  distinction.
- All four bulk buttons (Sync all, Review all, Fix all PR comments,
  Cleanup merged): descriptions updated to say "active linked worktrees".

## 5. Testing & rollout (Section 4, approved)

- Backend tests: migration idempotency (run twice, existing rows
  `active=1`); `load_links` active-only vs `include_inactive`;
  deactivate→reactivate round-trip; delete-row-only (worktree path still
  on disk); bulk-skip (`sync --all` emits `skipped:deactivated`).
- Frontend: `npx tsc -b --noEmit` AND `npm run build` (strict TS6133 —
  both must pass); dialog buttons wire to `link` runs; collapsed section
  + search filter behavior.
- Rollout: migration inside existing `init_db` retry path
  (DB-locked-safe). Full suite: `.venv/bin/python -m pytest tests -q`.
