# Custom Actions (`custom_actions`) — Design Spec

Date: 2026-10-19. Status: approved — user pre-approved all designs, specs, and plans (2026-10-19).

## 1. Intent

Users want reusable, user-defined agent operations ("Generate Post PRD",
"Backfill tests", "Security sweep") that run against one or more linked
worktrees with a single click. Today every harness launch is one of three
hardcoded flows (start/review/sync); anything else requires hand-writing
a prompt per worktree.

Goal: a `custom_actions` table + `workagent action` CLI (CRUD + run) +
serve passthrough + an `/actions` management page + a Dashboard `Actions`
dropdown that fans out one run per selected worktree.

Confirmed decisions (user, 2026-10-19):
- Execution scope: one run per worktree (never a merged single prompt).
- Scripts: shell in the target worktree, gating (pre failure aborts that
  worktree's run; post failure marks the run failed).
- CLI parity: full (`action add/list/show/run/delete` own logic; web mirrors).
- Run mode: per-action default in `default_options`, overridable per run.

Assumptions: no CLI completion changes needed beyond existing ref
completion; no new session transcript format (reuse `.jsonl` sessions).

## 2. Scope

In scope:
- `custom_actions` table (SQLite, idempotent migration) + store CRUD.
- CLI: `action add|list|show|run|delete` (+ JSON output, `--dry-run` on run).
- Execution: pre → prompt(+paths) → harness → post per worktree; run modes
  preview / headless (`--launch`) / terminal (`--terminal`); session type
  `custom_action`; `--all`-style fan-out is per-key loop, not a flag.
- Web: `SPECS`/`BOOL_FLAGS`/`VAL_FLAGS` mirror, `GET /api/actions`,
  `GET /api/actions/{label}` read endpoints, `POST /api/runs` passthrough,
  API_CONTRACT.md + FEATURE_INVENTORY.md entries.
- Frontend: `/actions` page (table + modal detail/edit/add), nav entry,
  Dashboard `Actions` dropdown (disabled when selection empty), per-worktree
  confirm → one `createRun` each, `useActions` query hooks, TS types.
- Tests: store CRUD + migration, label normalize/validate, execution order
  + gating + prompt paths, CLI tests, webapp flag-mirror, frontend control
  presence (per parity checklist).

Out of scope:
- Scheduling/cron, action chaining/composition, per-repo action scoping,
  action versioning/history, sharing/export, `--all` bulk flag (Dashboard
  loops per key instead).

## 3. Data model + migration

Table `custom_actions` (name chosen: `custom_actions` — the user's first
option; `user_actions` implies ownership semantics we don't model):

```sql
CREATE TABLE IF NOT EXISTS custom_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT UNIQUE NOT NULL,          -- normalized machine key, immutable
  name TEXT NOT NULL,                  -- display string
  icon TEXT NOT NULL DEFAULT 'Zap',    -- lucide name, validated loosely
  prompt TEXT NOT NULL DEFAULT '',
  pre_execution TEXT NOT NULL DEFAULT '',
  post_execution TEXT NOT NULL DEFAULT '',
  default_options TEXT NOT NULL DEFAULT '{}',  -- JSON: {run_mode, harness, ...}
  created_at TEXT NOT NULL,
  modified_at TEXT NOT NULL);
```

Rules:
- `label`: required, unique, normalized from `name` when omitted
  (`lower, [^a-z0-9]+ → _, strip _`, e.g. "Generate Post PRD" →
  `generate_post_prd`); explicit `--label` validated against the same
  charset (`^[a-z0-9_]+$`); immutable after create (rename = delete+add).
- `name`: required non-blank; display only.
- `icon`: required but defaulted (`Zap`); validated as non-blank
  identifier (`^[A-Za-z0-9_-]+$`) — never fail a row on an unknown icon;
  frontend falls back to `Zap` for unmapped names.
- At least one of `prompt` / `pre_execution` non-blank (else exit 2).
  Both blank is the only invalid content combination.
- `default_options`: JSON object string; keys `run_mode`
  (`preview|headless|terminal`, default `preview`), `harness`
  (default configured), `env_file`, `extra_prompt`. Unknown keys preserved
  (forward-compatible), invalid JSON → exit 2.
- `created_at`/`modified_at`: UTC ISO; `modified_at` bumped on update.

Migration: no numeric schema version; follow the ordered-idempotent
`init_db` pattern (`store_sqlite.py` `_migrate_worktrees_active` /
`_migrate_worktrees_deleted_at` template): `SCHEMA` gains the
`custom_actions` block; `_schema_current` probes it; `init_db` chains the
create. JSON-bridge: legacy `custom_actions.json` read fallback is NOT
needed (new table, no legacy file) — but the JSON-compat removal rule
still applies to existing bridges (don't touch them).

## 4. Execution semantics

`action run <label> <ref> [options]` resolves `<ref>` via the shared
`worktrees.resolve_any` fuzzy resolver (same as cleanup/open/sync), skips
deactivated + invalid worktrees with a stderr note (never tears down),
then per worktree in order:

1. **pre_execution** (if non-blank): `/bin/sh -c <script>`, cwd = target
   worktree, via the existing `release_gates.run_repo_command`-style choke
   point (own process group, env file sourced, timeout 10 min). Non-zero →
   stderr note, skip harness for this worktree, continue to next.
2. **prompt build**: `action.prompt` + appended worktree-paths block:
   the target worktree path plus every child (`parent_key_ref == key`)
   path, one per line under a `Related worktrees:` header (target first).
   `--extra-prompt` appends after (reuse `append_extra_prompt`).
   Empty prompt + pre-only action → run scripts only, no harness session.
3. **harness**: reuse `_run_harness` with `result["command"] = "action"`,
   `run_key = <worktree key>` (one-harness-per-worktree lock applies),
   modes: default preview; `--launch` headless; `--terminal` terminal
   (XOR, same as start/review). Session type `custom_action` (extends
   `SESSION_TYPES` + `derive_session_type` + `_backfill_session_type`;
   session dir segment `custom_action`).
4. **post_execution** (if non-blank, harness exit 0 or no harness): same
   shell choke point. Non-zero → mark run failed (exit 1) with stderr note.

`--dry-run` prints label/key/worktree/mode/script presence, no side
effects. `--json` emits the result dict (stdout-only). Deactivated
worktrees are skipped in bulk fan-out (Dashboard loops per key; the CLI
takes exactly one ref per invocation — no `--all` flag).

## 5. CLI surface

`workagent action <sub>` — new module `src/workagent/cli_action.py`,
registered on the shared `cli_core.app` (same pattern as `cli_start.py`):

- `action add --name NAME [--label L] [--icon I] [--prompt P]
  [--pre-execution S] [--post-execution S] [--default-options JSON] [--json]`
- `action list [--json]` (table: id/label/name/icon/mode/updated)
- `action show <label> [--json]`
- `action run <label> <ref> [--launch|--terminal] [--harness H]
  [--extra-prompt T] [--env-file F] [--session-file S] [--dry-run] [--yes] [--json]`
  (mode override precedence: CLI flags > `default_options.run_mode` >
  `preview`; `--yes` skips the TTY confirm)
- `action delete <label> [--yes] [--json]`

Errors: unknown label → exit 2 usage; ambiguous ref → existing picker;
live harness → exit 1 (same guard text); pre/post failure notes on stderr.

## 6. Web surface

- `web_args.py`: `SPECS["action"]` (confirm True, force True for delete,
  target key `action:<label>:<first-positional>`), `SUBCOMMANDS["action"]`
  = `{add, list, show, run, delete}`, `BOOL_FLAGS`/`VAL_FLAGS` entries
  mirroring the CLI exactly (`test_specs_mirror_cli_flags` must pass).
- Read endpoints (no run): `GET /api/actions` → `{"actions": [...]}`,
  `GET /api/actions/{label}` → single row or 404. Always 200/404, never 500
  on empty table.
- Mutating path stays `POST /api/runs` (`command: "action"`,
  `args: ["run", label, key, ...]`), with `--yes` appended on confirm and
  `--force` on delete — same as cleanup/register.
- `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` entries in the same PR
  (parity checklist).

## 7. Frontend

- `/actions` page (`web/src/pages/Actions.tsx`, ≤500 lines): table
  (id/label/name/icon/mode/updated + row actions), Add button → modal form,
  row click → detail modal (all fields + created/modified), Edit → same
  modal in edit mode, Delete → confirm (`useConfirm`) → `action delete`
  run. SWR-style: render cached rows + overlay while refetching.
- Nav: `AppLayout.tsx` NAV_ITEMS gains `{ to: "/actions", label: "Actions",
  icon: Zap }`; `App.tsx` route; BottomNav grid-cols 7 → 8.
- Dashboard: top-bar `Actions` dropdown (shadcn `dropdown-menu` — new dep
  or popover reuse; prefer existing `popover`) listing action name+icon;
  `disabled={selCount === 0}` with title "Select worktrees first"; on pick
  → confirm dialog (scope detail reuses `scopeDetail`) → one `createRun`
  per selected key (`["run", label, key]` + mode args) → toast per key
  with View action (same as handleSyncAll loop).
- Queries: `useActions` (+ `queryKeys.actions`), `api.actions()` /
  `api.action(label)` in `lib/api.ts`; `Action` TS type.
- Timestamps render relative + tooltip (per product rule); icon renders
  via a small `ActionIcon` map with `Zap` fallback.

## 8. Testing

- `tests/test_custom_actions.py`: migration/idempotent create, CRUD round
  trip, label normalize + explicit-label validation, blank prompt+pre
  rejection, bad JSON rejection, `modified_at` bump, prompt path block
  (target + children order), pre-fail skips harness, post-fail marks
  failure, mode precedence (flag > default > preview), dry-run no-op,
  deactivated/invalid skip.
- `tests/test_cli_action.py`: add/list/show/run/delete incl. JSON,
  unknown-label exit 2, `--launch`/`--terminal` XOR, web flag mirror is
  covered by existing `test_specs_mirror_cli_flags` (no new test needed,
  but must stay green).
- `tests/test_webapp.py`: `/api/actions` list + detail + 404 additions.
- Frontend: control-presence test per parity checklist (Actions page route
  + Dashboard dropdown disabled-when-empty).

## 9. Risks

- `label` normalization collisions ("Foo Bar" vs "Foo-Bar" → same key):
  mitigate with UNIQUE + clear exit-2 message naming the holder.
- Script injection via prompt interpolation: scripts run as-is (user's own
  content, same trust as pre-release commands); paths are appended to the
  *prompt*, never interpolated into the shell command.
- Session-type enum extension (`custom_action`) touches sessions spec §7
  ("no ORM") — additive constant only, no schema change to sessions.
- New `popover`/`dropdown-menu` dep: prefer reusing existing `popover.tsx`;
  only add `dropdown-menu` if popover can't host the action list cleanly.
