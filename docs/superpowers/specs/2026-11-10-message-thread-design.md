# Message & Thread Interaction System — design (issue #44)

Date: 2026-11-10. Status: design approved, not implemented.

## Intent

Registered chat messages (Mattermost) become first-class rows: stored in
`state.db`, listed in CLI + a `/messages` web page, and actionable via
thin wrappers over the existing engines (adhoc prompts, custom actions,
flows, `mm` draft replies). A strict `category` enum on actions/flows
(`worktree | message`) keeps the two surfaces from leaking into each other.

## Decisions (user-confirmed)

- **Ingest:** manual `message add` now, `mm` import later. `message_id` +
  `app_name` jointly unique so a future import is idempotent, no schema churn.
- **Actions:** thin wrappers only — no new built-in prompt presets in MVP.
- **Category:** strict enum with CHECK constraint; existing rows backfill
  `worktree`; runners reject mismatches (exit 2, actionable text).
- **Exec target:** hybrid — explicit `--ref` worktree wins, else resolve the
  message's `repos[]` like `start`'s tracker→repo picker (flag → single
  default → `pick.py` chooser). Harness runs in the worktree or the repo
  checkout.
- **UI:** StatusTable twin page (`/messages`), reusing table primitives with
  message columns and `category=message`-filtered dropdowns.

## Assumptions

- `message_id` = Mattermost post id; `thread_id` = root post id
  (`''` or `= message_id` for top-level posts).
- Sends stay drafts-only per the `mm` guard: `message draft-reply` uses
  `mm messages draft save --reply-to <thread-root>`, never direct send.
- Jira creation from `validate` is a harness prompt outcome, not a new
  `message` code path.

## Changes

### 1. Schema (`store_sqlite.py`)

- New `messages` table (schema v19 + `_migrate_messages`):
  `id INTEGER PRIMARY KEY AUTOINCREMENT`,
  `message_id TEXT UNIQUE NOT NULL`,
  `thread_id TEXT NOT NULL DEFAULT ''`,
  `app_name TEXT NOT NULL DEFAULT 'mattermost'
   CHECK (app_name IN ('mattermost'))`,
  `sender_username/content/ts TEXT NOT NULL DEFAULT ''`,
  `repos TEXT NOT NULL DEFAULT '[]'` (JSON array of `repos.key_ref`),
  `added_at TEXT NOT NULL`.
- `custom_actions` + `flow_templates` gain
  `category TEXT NOT NULL DEFAULT 'worktree'
   CHECK (category IN ('worktree','message'))` via `_migrate_category`;
  existing rows backfill `worktree`.
- `repos[]` validated: ≥1 entry, each `EXISTS` in `repos` (HarnessError
  exit 2 otherwise). Caller passes repo keys or paths; stored as keys.

### 2. Store (`store_messages.py` new, `store.py` facade)

- `create_message / list_messages / get_message / update_message /
  delete_message`; ref resolution helper: row `id` → `message_id` →
  unambiguous `thread_id`/substring match (ambiguous → exit 2 listing
  candidates). `list_messages(thread_id=...)` for thread views.
- Category plumbed through `store_custom_actions.create/update_action`
  and `store_flows.create/update_template` (`category` param, validated
  against `('worktree','message')`, default `worktree`).

### 3. CLI (`cli_message.py` new, `cli_action.py` / `cli_flow.py` edits)
- New `message` Typer group: `add / list / show / update / remove`
  (data-only stdout, `--json`/`--csv` like `link list`).
- `message adhoc <msg> --prompt ...` — builds prompt = action-style header
  (message content + thread link + repos block) + user prompt; hybrid
  resolve; same modes/confirms as `adhoc`.
- `message action run <label> <msg> [--ref ...]` — rejects
  `category != 'message'` (exit 2); prompt builder gains a message branch
  (content/thread context instead of worktree paths).
- `message flow run <label> <msg> [--ref ...]` — same category guard;
  `action` steps inside the chain inherit the guard (a `message` flow may
  only reference `message` actions — enforced in `validate_template`
  when `category == 'message'`).
- Worktree-kind flow steps (`start`/`review`/`sync`/`cleanup`/`release`)
  require `--ref`; without one the run exits 2 naming the offending step —
  repo checkouts have no branch/PR to operate on.
- `message validate <msg>` — adhoc wrapper with a fixed validate-claims
  prompt template (report confirmed/refuted claims; Jira creation stays
  inside the harness conversation).
- `message draft-reply <msg> --text ... [--dry-run]` — `mm messages
  get-msg <message_id>` for the channel, then `mm messages draft save
  --reply-to <thread-root>`; send-guard errors surface verbatim.
- `action add/update`, `flow template add/update`: `--category
  worktree|message` (default `worktree`).

### 4. Web backend (`webapp.py`, `web_args.py`, `web/src/lib/api.ts`)

- `GET /api/messages` → `{"messages": [...]}`,
  `GET /api/messages/{id}` (404 on unknown) — same entry shape as status
  rows (message fields + `repos` resolved names).
- `POST /api/runs` gains `message` command; `SPECS` target keys
  (`message:<id>` for runs, `config` for CRUD), `BOOL_FLAGS`/`VAL_FLAGS`
  mirror every new flag, `_validate_args` entries.
- `GET /api/actions` + `/api/flow-templates` include `category`;
  Dashboard dropdowns pass `?category=worktree`.
- Update `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md`;
  `test_specs_mirror_cli_flags` must stay green.

### 5. UI (`web/src/pages/Messages.tsx` new + components)

- `/messages` route reusing StatusTable primitives: columns
  app / sender / thread / repos / updated + expandable content detail
  (`MessageDetail.tsx`); filters (app, sender, repo tab, text search,
  thread grouping toggle).
- Per-row overlay (`MessageRowActions.tsx`): Adhoc, Validate,
  Draft reply, Run action / Run flow (`category=message`-filtered
  dropdowns), Delete. Mutations run through the existing run/SSE
  plumbing; detail dialog highlights code blocks like the Actions page.
- Dashboard worktree dropdowns filter `category=worktree` (no layout
  change when no `message` items exist).

### 6. Tests

- `tests/test_store_messages.py`: CRUD, `repos[]` validation (empty /
  unknown repo → error), unique `message_id`, thread listing, category
  CHECK + backfill default.
- `tests/test_cli_message.py`: CRUD round-trip, category rejection on
  `action run`/`flow run`, `draft-reply --dry-run` argv, hybrid resolve
  (single-repo default vs. `--ref` win).
- `tests/test_webapp.py`: `/api/messages` shapes + specs-mirror-flags.
- Gate: `uv run pytest -v` green + `tsc`; smoke: add → list → adhoc
  dry-run → action run dry-run → serve page render.

## Non-goals

- No `mm` import/sync command (follow-up; schema already shaped for it).
- No auto-Jira creation outside harness prompts.
- No thread-tree view (flat rows + thread filter instead).
- No new prompt presets shipped as protected actions/flows.
