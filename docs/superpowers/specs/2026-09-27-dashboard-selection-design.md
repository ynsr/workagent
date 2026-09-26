# Dashboard Row Selection + Bulk Actions Design

**Date:** 2026-09-27
**Status:** approved (Approach 1; all sections approved, spec+plan without further gate — blocking/ambiguous questions only)

## 1. Goal

Selectable worktree rows on the Dashboard. The four top-bar bulk buttons
(Sync All, Review All, Fix all PR comments, Cleanup merged) act on the
selection when non-empty, else on all worktrees in the table scope.
Fix the `Review all` / `Fix all PR comments` failure
`--session-file cannot be used with --all`, and make Sync All read
enabled.

## 2. Decisions (locked)

- **D1 Selection UX:** checkbox column (Approach A). Narrow leading
  column + header tri-state; row-click still toggles details only.
  Mobile cards get the same checkbox in the card header row.
- **D2 Empty-selection scope:** today's all-linked semantics (Approach A).
  Empty = every active linked worktree (`--all` / `--merged`, filter
  ignored). Non-empty = exactly the selected keys.
- **D3 Review/Fix `--all` fix:** skip `--session-file` injection on the
  parent; CLI fans out per-worktree children (Approach A). One parent
  run, no new endpoints, no registry change.
- **D4 Selected-subset transport:** N repeat-per-key runs under one
  confirm (Approach A). No CLI grammar change. `cleanup --merged`
  stays all-only; disabled with tooltip while a selection exists.
- **D5 Approach:** Approach 1 (frontend selection + 3-line backend
  carve-out + N-run fan-out). Approaches 2 (key-list CLI flags) and 3
  (run-group fan-out) deferred; the `?sel=` + N-run foundation composes
  with both later.

## 3. Selection model

- `StatusTableMain` owns `sel: Set<string>`, synced to `?sel=` as
  comma-joined keys (same shape as the existing `?q=` pattern in
  `StatusTableMain.tsx:95-123`). Unknown keys dropped on load; survives
  refresh/share; cleared via a "Clear" affordance beside the filter pill.
- Header checkbox tri-state (checked / indeterminate / unchecked) over
  the **filtered visible** keys (`keys` memo, `StatusTableMain.tsx:100-116`).
- Per-row `Checkbox` (shadcn/Radix primitive,
  `web/src/components/ui/checkbox.tsx`) with
  `aria-label="Select <key>"`; `onCheckedChange` toggles membership.
  Row click / expand-details toggle (`expanded` state,
  `StatusTableMain.tsx:97`) unchanged — checkbox stops propagation.
- Selected rows: `aria-selected="true"` + `data-[state=selected]`
  highlight (existing shadcn table selected-row token); invalid-row
  `bg-destructive/5` keeps precedence via `cn()` order.
- Selection vs filter: selecting then filtering keeps off-screen keys
  selected (bulk acts on full `sel`); header box reflects visible keys
  only. Repo tabs (`repoTabs` prop) behave like the `?q=` filter.
- `StatusTable` reports both the filtered visible keys and `sel` up to
  `Dashboard` via new optional props
  (`selected: Set<string>`, `onSelectionChange: (s: Set<string>) => void`,
  plus `visibleKeys: string[]` or a `selectAllVisible` callback).
  `StatusTable.tsx` re-exports/extends `StatusTableActions` untouched.
- Deactivated table (`DeactivatedTable`) stays selection-free.

## 4. Toolbar semantics + Sync All styling

- Empty selection: today's calls verbatim —
  `sync --all`, `review --all [--force-all]`, `review --all --fix-comments`,
  `cleanup --merged` (`Dashboard.tsx:110-227`). Confirm copy unchanged
  except the Scope row reads "Every active linked worktree".
- Non-empty selection of N keys: N sequential `createRun` calls, one per
  key — `sync <key>`, `review <key>`, `review <key> --fix-comments`
  (+`--force-all` passthrough for Review), `cleanup <key>` — each with
  `confirm: true` (server appends `--yes`). Single confirm dialog; Scope
  row lists keys (truncate after ~5 with "+M more"). Sequential awaits
  keep toast ordering sane; one failure doesn't stop the rest; toast
  group shows per-run ok/error with "View" links (`runCreated`,
  `Dashboard.tsx:103-108`).
- Button labels scope-aware: `Sync all` → `Sync (3)` when 3 selected
  (same for Review / Fix / Cleanup); `title` tooltips spell it out
  ("Sync 3 selected worktrees" vs "Sync every active linked worktree").
- Sync All styling: `variant="secondary"` → `variant="outline"`
  (`Dashboard.tsx:345`) — the "disabled" read was the secondary-on-toolbar
  contrast; no `disabled` prop exists today. All four buttons end up
  `outline` + same size.
- Cleanup merged with selection: button `disabled` + tooltip
  "Clear selection to clean all merged (or Delete per row)" because
  `cleanup --merged` rejects a positional ref (`cli_cleanup.py:57-58`).
  Row-level Delete (`CleanupDialog`) unchanged.

## 5. `--session-file` carve-out (the bug)

- Root cause: `POST /api/runs` unconditionally appends
  `--session-file <one path>` to every `review`/`start` run without an
  explicit one (`web_runs_routes.py:72-81`); the CLI
  (`cli_review.py:229`, `cli_sync.py:66`) and the web validator
  (`web_args.py:142`) reject `--session-file` alongside `--all`.
- Fix (3 lines): in `register_runs_routes.create_run`, when
  `"--all" in body.args`, skip the injection entirely
  (`run.session_file = ""`); the CLI parent fans out to
  `review <pr> --no-tty` children (`cli_review.py:247-288`), each
  generating its own transcript as on the CLI today. Explicit
  user-passed `--session-file` + `--all` still 400s (validator unchanged).
- `--dry-run` preview path untouched (never reaches the harness).
- `cleanup --merged` never took `--session-file`; unaffected.
- Contract note under the `--all` runs entry in `web/API_CONTRACT.md`;
  `FEATURE_INVENTORY.md` bulk-action row updated.

## 6. Selected-key run shapes

- `sync <key> --yes` (+`--merge` from the dialog toggle,
  `Dashboard.tsx:120-131`); `review <key> --yes` (+`--force-all`
  passthrough); `review <key> --fix-comments --yes` for Fix;
  `cleanup <key> --yes` (bulk cleanup reuses the simple confirm — no
  per-key force dialog; force stays row-level via `CleanupDialog`).
- Review-by-key reuses worktree→PR resolution (`cli_review.py:303-316`):
  keys without a PR fail that single run with today's message while
  siblings continue.
- No CLI grammar change, no `SPECS`/`VAL_FLAGS` change
  (`web_args.py:18-44`); per-key target keys (`sync:<key>` etc.) already
  distinct — no registry collision.

## 7. Files touched

- `web/src/components/StatusTableMain.tsx` — checkbox column (desktop
  `TableHeader`/`TableRow` + mobile card header), `sel` ↔ `?sel=` sync,
  tri-state header over filtered keys, selection props.
- `web/src/components/StatusTable.tsx` — selection prop types
  (actions interface untouched).
- `web/src/pages/Dashboard.tsx` — selection state, scope-aware labels,
  N-run handlers, confirm Scope rows, Sync All `outline`, Cleanup-merged
  disabled-with-tooltip.
- `web/src/pages/Links.tsx` — same toolbar pattern only if in scope
  (deferred; Dashboard first).
- `src/workagent/web_runs_routes.py:72-81` — skip `--session-file`
  injection when `--all` present.
- `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md` — notes.
- Tests: `tests/test_webapp_runs.py` (or new `test_webapp_bulk.py`) for
  the carve-out; `tests/test_cli_review_all.py` unchanged (CLI already
  fans out); frontend `tsc -b --noEmit` + `npm run build` clean.

## 8. Verification

- `.venv/bin/python -m pytest tests -q` (system `python3` lacks `typer`).
- `./web/node_modules/.bin/tsc --noEmit --project web` AND
  `npm run build --prefix web` (both — vite-only failures exist).
- Manual: select 2 rows → labels show `(2)` → Review selection fires 2
  runs; clear → labels revert; `review --all` parent run succeeds
  (no `--session-file` error); Sync All reads enabled.
- No `./install.sh` — user reinstalls manually; new `web/dist` built locally only.

## 9. Out of scope (deferred)

- Server-side key-list CLI flags (Approach 2).
- Run-group fan-out with group ids (Approach 3).
- Selection in `DeactivatedTable`, `Links.tsx` toolbar, Candidates page.
- Persisting selection server-side or across repos.
