# Flow linkage: orphaned worktrees, missing sessions, lost failures — design

Date: 2026-10-23. Approach: **A — first-class linkage** (approved in chat).
Status: spec review gate pending.

## 1. Intent

Fix five reported flow bugs against the existing flow chaining spec
(`docs/superpowers/specs/2026-10-20-flows-design.md`):

1. Flows not linked to any worktree are never cleaned up after their
   worktree is cleaned (e.g. paused flow #32 for IPG-964 survived the
   cleanup of `jira:IPG-964@projectx`; #1, #27, #28 are worktree-less
   and still pending).
2. No sessions are created for any flow step (e.g. flow #32; run
   `01abc2d9febb` has no session).
3. A failed flow step is not saved in the DB; state is lost (the failed
   `01abc2d9febb` run belongs to no flow row that shows it).
4. Flows page needs filters by state, worktree, and repo.
5. Sessions and Runs pages need filters by their flow.

Success: every flow step leaves a session + run row; every failure is
persisted with the cursor held; cleanup settles linked flows; all three
pages filter by flow linkage server-side.

## 2. Root causes (verified against live DB 2026-10-22)

- **R1 — orphaned paused flows.** `flows.worktree_ref` stays `""` when
  the start step pauses (preview/terminal) before the worktree exists.
  The flow row carries only `repo_snapshot.launch_ref`. Cleanup of a
  worktree key (`_cleanup_one`, `delete_worktree_row`, `gc_soft_deleted`)
  only touches `flows WHERE worktree_ref = ?`, so empty-ref flows are
  never matched. Resume (`_find_resumable`) matches empty-ref flows only
  by exact `launch_ref == ref`, so the same URL launched via a different
  ref (issue key vs URL) creates a second row instead of adopting the
  first.
- **R2 — sessions blocked by FK.** `sessions.worktree_ref NOT NULL
  REFERENCES worktrees ON DELETE CASCADE`. `record_preview_session`,
  `record_headless_attempt`, and `_run_harness` all warn-and-skip when
  no linked worktree exists. Pre-start pauses and pre-link failures
  therefore persist nothing. Web `_mirror_run` only mirrors
  `--launch` runs with a `--session-file`; live `flow run` children
  (no session file, unattached) are skipped by design.
- **R3 — lost failures.** `flow_run.py` catches only `HarnessError`
  around step dispatch. `typer.Exit` (raised by `_fail` inside `_call`'d
  runners) and any other exception skip `update_flow(status="failed")`,
  leaving the flow `running` with no new step result. Post-hoc
  `tag_flow_session` stamps only the newest session for the key, so
  headless-step sessions dispatched through runners that bypass the tag
  window keep no flow link.

## 3. Schema (`store_sqlite.py` DDL + migration v15)

- `sessions.flow_id INTEGER NULL REFERENCES flows(id) ON DELETE SET
  NULL` — nullable second key; worktree stays the primary key while
  attached.
- `runs.flow_id INTEGER NULL REFERENCES flows(id) ON DELETE SET NULL`
  — set at insert for flow children; backfilled from the session's
  `flow_id` for existing rows.
- `sessions.worktree_ref` becomes **nullable** (`TEXT NULL`, no FK
  change): unattached flow rows store `NULL` instead of `""` and are
  exempt from the worktree FK (CASCADE still fires for attached rows).
  `""` legacy rows migrate to `NULL` only when they carry a `flow_id`
  (pre-link flow attempts); attached `""` rows are untouched.
- Migration `v15` (idempotent): `ALTER TABLE … ADD COLUMN` for the two
  `flow_id` columns (when missing); rebuild `sessions` from canonical
  DDL only when `worktree_ref` is still `NOT NULL`; backfill
  `sessions.flow_id` from `metadata.flow_id` (JSON extract, integer
  cast, only when the flow row exists); backfill `runs.flow_id` from
  the parent session; `_schema_current` requires the new columns.
- `_NOT_DELETED` (sessions + flows) becomes NULL-safe:
  `(s.worktree_ref IS NULL OR s.worktree_ref = '' OR …)`.
- `flows` gains `FOREIGN KEY (worktree_ref)`? **No** — rejected:
  pre-Start flows legitimately carry no ref, and soft-delete hides
  (not drops) linked rows. Linkage stays application-level.

Backfill keeps `metadata.flow_id` in place (audit trail); the column
is the query path, metadata the history.

## 4. Flow lifecycle (`cli_flow_run.py`, `cli_flow_steps.py`)

- `flow run` inserts the flow row, then a `session_type='flow'`
  session (NULL worktree when unresolvable, `flow_id` set) plus its
  run row — every flow run owns history even when step 0 pauses.
- Each step creates its session with `flow_id` already set at insert
  (no post-hoc newest-match tagging): headless steps pass
  `session_metadata={"flow_id": fid}` through the runners; pause
  steps (`persist_terminal_pause`, manual/preview) record with
  `flow_id` + NULL worktree when unattached.
- On resume adoption (start key changes, skipped-start adopt, launch_ref
  re-resolve) the flow row's `worktree_ref` is backfilled and all
  NULL-worktree sessions/runs of this `flow_id` are re-attached
  (`backfill_flow_worktree`).
- `_find_resumable` additionally matches empty-ref flows by parsed
  issue identity: `launch_ref` and the new `ref` parse to the same
  issue key (not string equality), so `jira:IPG-964` resumes the flow
  launched as the Jira URL.
- Step dispatch catches `Exception` (not only `HarnessError`):
  `status=failed`, cursor held at the step, result appended, and a
  best-effort failure session + run row (`state=failed`,
  `exit_code=1`, NULL worktree when unattached, `flow_id` set).
  `typer.Exit` from `_fail` maps to exit code preservation.
- `tag_flow_session` becomes a backfill-only repair (kept for old
  rows), not the live path.

## 5. Cleanup coupling (`cli_cleanup.py`, `store_links.py`, `cli_manage.py`)

- New `store_flows.fail_open_flows(db, worktree_ref)` — sets
  `status=failed` + appends `{kind: <last-step-kind>, status: failed,
  detail: worktree cleaned}` for every non-terminal
  (`pending|running|paused`) flow with this `worktree_ref`. Best
  effort: never blocks teardown.
- `_cleanup_one` (soft + hard paths) and `delete_worktree_row`
  (hard) call it after the row is stamped/dropped; `gc_soft_deleted`
  calls it per GC'd row.
- Empty-ref flows whose `repo_snapshot.launch_ref` parses to the
  cleaned worktree's issue key are failed the same way
  (`match_launch_ref` helper: parse both, compare `issue_key`).
  Explicit `launch_ref`-less empties are left alone (nothing to match).
- `link remove` (manage path) shares `_cleanup_one`, so it inherits
  the behavior.

## 6. Query + API (`store_flows.py`, `store_sessions.py`, `webapp.py`)

- `list_flows(worktree="", status="", repo="")`: `status` exact match
  against `FLOW_STATUSES`; `repo` substring-matches
  `repo_snapshot.repo` (path or name) — covers post-cleanup rows whose
  link is gone. Combined AND with worktree.
- `list_sessions(…, flow_id=0)` / `list_runs(…, flow_id=0)`: join on
  the new columns (not metadata). `include_deleted_worktrees`
  unchanged.
- `GET /api/flows?worktree=&status=&repo=` (all optional, always 200).
- `GET /api/sessions?flow=<id>` and `GET /api/runs?flow=<id>`
  (flow id integer; unknown id → `[]`, never 404).
- `SessionRow`/`FlowRow` unchanged; run summaries carry `flow_id`
  (persisted + live `flow:`-target children resolved through the
  target key `flow:<tpl>:<ref>` against `launch_ref` + `worktree_ref`).
- `web/API_CONTRACT.md` §flows/sessions/runs updated; parity test
  (`test_specs_mirror_cli_flags`) extended for any new CLI flags
  (`flow list --status --repo`; `flow show` unchanged).

## 7. CLI (`cli_flow_run.py` list/show)

- `flow list [--worktree] [--status] [--repo] [--json/--csv]` — new
  filters mirror the API; invalid `--status` exits 2.
- `flow show <id>` resolves soft-deleted rows (uses
  `include_deleted_worktrees=True`) so a failed-after-cleanup flow is
  still inspectable.

## 8. UI (`Flows.tsx`, `Sessions.tsx`, `Runs.tsx`, `api.ts`, `queries.ts`)

- Flows page: status dropdown (all/pending/running/paused/failed/done),
  repo text filter, existing worktree picker — all server-driven via
  `useFlows(worktree, status, repo)`, URL params `?status=&repo=`
  alongside `?worktree=`.
- Sessions page: flow picker (from `useFlows()` ids + templates,
  `?flow=`), server-driven `useSessions(flow)`; keeps existing
  client worktree/text/repo filters.
- Runs page: `?flow=` resolves server-side (`/api/runs?flow=`),
  replacing the current `target`-string match against
  `flow:<tpl>:<ref>`; keeps state/repo/target filters.
- Detail dialogs keep their Sessions/Runs link-outs (`?flow=<id>`).

## 9. Edge cases

- Paused flow cleaned before resume: shows `failed (worktree cleaned)`;
  resume re-creates a fresh row (`_find_resumable` skips terminal).
- Undelete does not revive failed-after-cleanup flows (terminal state).
- GC of a soft-deleted worktree fails (not deletes) its open flows.
- `metadata.flow_id` rows for deleted flow ids: column backfill skips
  them; metadata stays as history.
- `runs.flow_id` for pre-migration web-mirrored rows: backfilled from
  the parent session at migration time only.
- Concurrent `flow run` resume races: `update_flow` is last-write-wins
  (existing behavior, unchanged).

## 10. Testing

- Migration: `flow_id` cols exist, metadata backfill maps integer ids
  only, NULL-worktree flow sessions survive FK ON, attached CASCADE
  still fires, `_schema_current` gates.
- Orphan adopt: empty-ref paused flow resumes via issue-key match and
  backfills `worktree_ref` + NULL sessions.
- Pre-start pause: terminal/preview pause with no worktree persists a
  session + run with `flow_id` and NULL ref.
- Failure: non-`HarnessError` (and `typer.Exit`) in a step → flow
  `failed`, cursor held, result appended, failure session + run rows.
- Cleanup: soft + hard + GC fail open flows for the key and
  launch_ref-matched empties; teardown never blocked by flow failure.
- Filters: `list_flows(status, repo)`, `list_sessions(flow_id)`,
  `list_runs(flow_id)`; API `?status=&repo=&flow=` incl. unknown flow
  → `[]`; CLI `--status/--repo` + exit 2 on bad status.
- Parity: `test_specs_mirror_cli_flags` green; `tsc + build`; CLI
  smokes on a scratch template.
