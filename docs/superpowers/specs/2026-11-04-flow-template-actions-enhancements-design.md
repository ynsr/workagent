# Flow Templates + Custom Actions: Release Config, Run-Modal Saves, Protection/Order/Dedicated Buttons

Date: 2026-11-04. Status: design approved, not implemented.

## Intent

- Template add/edit forms and the Run-flow modal expose Release Checks + Diff config.
- Run-flow modal gains Save / Save-as / Reset, grouped left vs. execution right.
- Both management tables gain: protected toggle, order column with drag-reorder,
  dedicated_top_bar + dedicated_overlay flags rendering shortcut buttons on the
  Dashboard top bar and StatusTable row overlays.

## Decisions (user-confirmed)

- Release editor: Checks + Diff only (`ci|local|both|none`, `remote|local`).
- Protected: UI-only (hides edit/delete affordances); CLI delete stays unconditional.
- Dedicated buttons: per-item direct-run (each flagged item gets its own button).
- Run-modal save: persists the full edited chain including launch overrides.
- Ordering: single `sort_order` drives management tables + Dashboard dropdowns.

## Changes

### 1. Schema (`store_sqlite.py`, `store_flows.py`, `store_custom_actions.py`)

- `flow_templates` + `custom_actions` each gain:
  `protected INTEGER NOT NULL DEFAULT 0`,
  `sort_order INTEGER NOT NULL DEFAULT 0`,
  `dedicated_top_bar INTEGER NOT NULL DEFAULT 0`,
  `dedicated_overlay INTEGER NOT NULL DEFAULT 0`.
- New migrations following the `_migrate_*` pattern; backfill `sort_order = id`.
- `list_templates` / `list_actions` → `ORDER BY sort_order, id`.
- Constraint: `store_sqlite.py` is ~1150 lines vs. the 500-line cap — migrations
  stay in the existing chain, no new module.

### 2. CLI (`cli_flow.py`, `cli_action.py`)

- `flow template add/update`: `--protected/--no-protected`, `--sort-order N`,
  `--dedicated-top-bar/--no-…`, `--dedicated-overlay/--no-…`.
- `action add/update`: same four flags.
- `show` / `list --json` include the four fields; `update_*` whitelists them
  (bumps `modified_at`).

### 3. API + contract (`webapp.py`, `web_args.py`, `web/src/lib/api.ts`)

- `GET /api/flow-templates`, `/api/actions` (+ detail routes) include the fields.
- `SPECS`/`BOOL_FLAGS`/`VAL_FLAGS` mirror new flags; `test_specs_mirror_cli_flags` stays green.
- Update `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md`.

### 4. ChainEditor (`web/src/components/ChainEditor.tsx`)

- `StepConfigForm` renders Checks + Diff selects only when `kind === "release"`
  (same visible-field pattern as `merge_only`/`update`), via `patch()` so
  launch-mode hint/delta semantics carry over. Defaults: `ci`, `remote`.
- `store_flows.validate_template` rejects other values (exit 2).
- No runner change: `RELEASE_FLAGS` / `run_release_step` already forward both.

### 5. FlowWizard (`web/src/components/FlowWizard.tsx`)

- Footer: left persistence group [Save] [Save as] [Reset] `|` right [Run flow].
- Save → `flow template update <label> --steps <working>` (confirm run + refetch).
- Save-as → prompt name/label → `flow template add` (clone-style lineage note).
- Reset → `setWorking(structuredClone(template.steps))`.
- All three disabled while `running`; Save/Run gated on `chainErrors`/repo.

### 6. Management tables (`web/src/pages/FlowTemplates.tsx`, `web/src/pages/Actions.tsx`)

- Columns: Protected (lock toggle, hides edit/delete/clone when on), Order
  (drag handle, persists `sort_order` via update on drop), Top-bar, Overlay.
- Tables render in `sort_order` order; CSV/JSON exports include the fields.

### 7. Dashboard + StatusTable (`Dashboard.tsx`, `StatusTable*.tsx`, `FlowsMenu.tsx`, `ActionsMenu.tsx`)

- Top bar: one button per `dedicated_top_bar=1` item in `sort_order` order.
  Templates → `FlowWizard` (selection) or Launch picker (no selection);
  actions → run on selected worktrees (disabled until ≥1 selected).
- Row overlay: one icon-button per `dedicated_overlay=1` item in `sort_order`
  order; template → wizard for that key, action → run on that key.
- No flagged items → no extra buttons (zero layout shift).

### 8. Tests

- `test_flows.py` + `test_custom_actions.py`: defaults, sort ordering, update round-trip.
- `test_webapp.py`: payload fields + specs-mirror-flags.
- `ChainEditor.test.tsx` / `FlowWizard.test.tsx`: release-only selects; footer groups + wiring.
- Gate: `uv run pytest -v` green.

## Non-goals

- No backend enforcement of protected (revisit if users delete protected rows via CLI).
- No `env_file` / timeout / `no_status` fields in the release editor.
- No single-slot dedicated buttons; no localStorage-only ordering.
