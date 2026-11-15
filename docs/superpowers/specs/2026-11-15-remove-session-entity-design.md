# Remove Session Entity, Fold Into Runs — Design Spec

> Issue: [#104](https://github.com/ynsr/workagent/issues/104) — Remove Session entity and table.
> Mode: plan only (no implementation). Branch: `feat/104--remove-session-entity-and-table`.
> Decisions (user-approved 2026-11-15): approach **A (Runs absorb transcript)**; transcript dir **rename** `sessions/` → `runs/`; migration **fold-and-drop**; fix-continue **keep** (resolved from runs); API/frontend **remove entirely, no shims**.

## 1. Problem

Two execution entities exist where users expect one. `sessions` (1 row per harness
transcript) and `runs` (N rows per transcript: preview + execution + fix-continue)
split one mental unit — "I ran the harness in a worktree" — across two DB tables,
two API namespaces (`/api/sessions*` vs `/api/runs*`), two frontend pages
(Sessions vs Runs), and two store modules (`store_sessions.py` vs `web_runs.py`
+ `runs` table). Users must learn which entity owns the transcript, which owns
the log, and how resume/copy buttons resolve between them.

## 2. Goal

One execution entity end to end: every harness launch (preview, terminal,
headless, flow step, prodtest, adhoc) is a **run** that optionally carries a
transcript. No user-visible "session" concept remains in CLI output, API,
database schema, or UI. Existing history survives via a one-way fold migration.

Non-goals (explicit): changing how transcripts are minted (omp `--session-dir`
/ `--resume` flow untouched); changing the harness launch protocol; adding new
features to Runs while migrating.

## 3. Current state (verified 2026-11-15)

- `sessions` table (`store_sqlite.py` SCHEMA): `id` (= transcript filename stem),
  `worktree_ref` FK, `state` (running/finished/failed/preview), `harness_name`,
  `initiator_command`, `prompt`, `file_path`, `created_at`, `session_type`
  (start/review/sync/fix_comments/custom_action/flow/prodtest_plan/prodtest_run),
  `metadata` JSON, `flow_id` FK.
- `runs` table: `id`, `session_id` FK → sessions (CASCADE), `command`, `args`,
  `exit_code`, `created_at`, `output`, `truncated`, `validation`, `flow_id`.
- Cardinality: **1 session → N runs** (preview row + real row share a session;
  review fix-continue reuses the review session via `reuse_session_id`).
- Session-only behaviors runs must absorb: transcript identity
  (`file_path`/`prompt`/`session_type`/`metadata.session_dir`), fix-continue
  (`--use-review-session`, review `--fix-comments` reuse, `GET
  /api/review-session` via `latest_review_session`), run resume/copy buttons
  (`_resolve_session_file`, `_child_session_file` stem matching), web preview
  rows (`record_preview_session`), StatusTable overlay re-run, flow step tagging
  (`tag_flow_session`), cleanup GC (`_remove_harness_sessions`).
- No `session` CLI subcommand exists; `--session-file` flags are the
  transcript-path mechanism and are orthogonal to the DB entity.
- Transcripts live under `~/.config/workagent/sessions/<harness>/<branch>/<action>/`.

## 4. Design (approved)

### 4.1 Schema + migration (v16, one-way fold-and-drop)

`ALTER TABLE runs ADD COLUMN`: `worktree_ref TEXT DEFAULT ''`,
`harness_name TEXT DEFAULT ''`, `initiator_command TEXT DEFAULT ''`,
`prompt TEXT DEFAULT ''`, `file_path TEXT DEFAULT ''`, `session_type TEXT`,
`metadata TEXT DEFAULT '{}'`, `state TEXT DEFAULT 'finished'`,
`continued_from_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL`.
Backfill: for every session row, `UPDATE runs SET (transcript cols) =
session.* WHERE session_id = session.id`; fix-continue chains (runs sharing a
session where a later run references `reuse_session_id`) set
`continued_from_run_id` to the earliest run of the same transcript. Preview rows
keep `state='preview'`. Then `DROP TABLE sessions`. On-disk: move
`~/.config/workagent/sessions/` → `~/.config/workagent/runs/`, rewriting stored
`file_path` prefixes; readers try the new path first, old path as fallback.
No down-migration.

### 4.2 Backend

- Delete `src/workagent/store_sessions.py`; re-implement needed helpers on runs
  in `store_sqlite.py` (or a slim `store_runs.py`): `insert_run` gains
  transcript kwargs (`worktree_ref`, `file_path`, `prompt`, `session_type`,
  `metadata`, `state`); `latest_review_run(worktree_ref)` replaces
  `latest_review_session`; `run_by_file_path` replaces `session_by_file_path`.
- `cli_harness.py`: preview/terminal/headless paths write runs only
  (`record_preview_session` → runs insert with `state='preview'`); launch adopts
  the preview row by `file_path` stem match instead of session PK; fix-continue
  sets `continued_from_run_id`.
- Follow-ons: `cli_review.py` reuse lookup, `cli_flow_steps.py` flow tagging
  (`flow_id` already on runs), `cli_cleanup_gc.py` transcript GC by
  `file_path`, `web_runs.py` precreate/adopt/mirror keyed by run id.
- `--session-file` CLI flags stay (transcript path in/out, not the entity).

### 4.3 API

- Delete `GET /api/sessions`, `GET /api/sessions/{sid}`, `POST
  /api/sessions/{sid}/resume` (persisted-run resume `POST
  /api/runs/db-<id>/resume` already covers it).
- `GET /api/review-session` resolves the latest finished/failed run with
  `session_type='review'` for the worktree (same response shape:
  `{ref, session_id→run_id, file_path}`; field rename `session_id`→`run_id`
  acceptable since no-shim decision).
- No compatibility shims (user decision).

### 4.4 Frontend

- Delete `web/src/pages/Sessions.tsx`, routes `/sessions`, `/sessions/:id`,
  nav entry, `useSessions`/`useSession`/`useResumeSession` query keys,
  `SessionRow`/`SessionDetail` API types. In-app `/sessions*` links redirect to
  `/runs*` (no server shim).
- Runs table/detail gain transcript affordances: kind label from `session_type`
  (reuse `sessionKind`), resume/copy buttons already run-based stay.
- Update `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, parity checklist
  (`SPECS` mirror test).

### 4.5 Testing + rollout

- Migration test: seed sessions+runs (incl. fix-continue chain) → migrate →
  assert transcript cols backfilled, `continued_from_run_id` linked, sessions
  gone, old-DB `file_path` fallback resolves.
- Behavior tests: fix-continue from runs; resume/copy post-rename; preview
  adopt by stem; `GET /api/review-session` from runs.
- Full `pytest` + web contract tests green; `CHANGELOG.md` one-liner `[104]`.
- Risks: one-way migration (no down migration — the mitigation is copying
  `state.db` plus the `runs/` and `sessions/` trees before upgrading;
  `backup export` only serializes custom actions + flow templates, never the
  DB); transcript-path rewrite (mitigate with old-path fallback read +
  `_rehome_transcripts` retried by every init pass); scope creep into
  launch-protocol changes (out of scope, §2).

## 5. Spec self-review

- Placeholders: none — all behaviors named with owning modules.
- Consistency: §4.1 rename + fallback matches user decision; §4.3 no-shim
  matches "remove entirely"; §4.4 redirect is in-app routing, not an API shim.
- Scope: single migration + deletion; launch protocol explicitly out of scope.
- Ambiguity resolved: `--session-file` stays; `session_id` response field may
  rename to `run_id`; transcript dir physically moves.
