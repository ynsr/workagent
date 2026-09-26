# Session type + metadata — design

Date: 2026-09-26. Status: approved (all points confirmed). Follows the
`harness_name` / `worktrees.active` migration precedent.

## 1. Intent

- Sessions table gains `session_type: start | review | sync | fix_comments`
  (NULL when unknowable) and `metadata` (JSON object string; SQLite has no
  enum/jsonb — `TEXT` + app-level validation).
- `review` sessions record fix outcomes in metadata instead of a dedicated
  boolean: `metadata.review_comments_fixed_at` + `metadata.fixed_by_run_id`
  (point 7: the `reviewCommentsFixed` boolean is dropped; timestamps are
  self-describing and never go stale the way a bare flag does).
- Launch → Review → "Fix review comments" gains a "Create a new session"
  checkbox: checked → fresh `fix_comments` session; unchecked → the run
  reuses the latest review session's transcript and, on exit 0, stamps the
  fix metadata onto that review row.

## 2. Agreed rules (user-confirmed)

1. **Derivation = subcommand + flag.** `start`→`start`, `sync`→`sync`
   (including `sync --harness` conflict sessions — type follows the
   originating subcommand, point 9), `review`→`review`, `review
   --fix-comments`→`fix_comments`. `review --all` children each get their
   own typed row. `cleanup/register/open` run no harness → no session row.
2. **"Current" review session** = latest `session_type='review'` row for
   that `worktree_ref`, any state except `running`.
3. **Concurrency** = the existing one-harness-per-worktree lock; a second
   fix run fails there instead of interleaving one transcript.
4. **New fix session** = fresh transcript, same `worktree_ref`,
   `metadata.fixed_from_session_id = <review session id>`.
5. **Backfill** = starts-with match on `start|review|sync`;
   `--fix-comments` present → `fix_comments`; unrecognizable → NULL type +
   `'{}'` metadata. Never guess.
6. **Migration** = additive + idempotent (`_migrate_sessions_type`),
   same `PRAGMA table_info` probe pattern as `_migrate_sessions_harness`.
7. **Fixed-signal** = run exit code 0. Non-zero → flag fields untouched,
   session `state` still goes `failed` (point 8).
8. **Column name** = `session_type` (not `type`), `metadata TEXT NOT NULL
   DEFAULT '{}'`.
9. **Web surface** = `SessionRow` gains `session_type: string | null` +
   `metadata` object; Sessions table gains a Type column + fixed-badge;
   SessionDetail shows metadata; Review form gains the new-session checkbox
   visible only when fixComments is on (point 10).

## 3. Correction found during design: initiator_command is always "omp"

The agreed backfill rule (point 5) assumes `initiator_command` holds the
subcommand. It does not: no caller sets `result["command"]`, so
`insert_session` falls back to `harness_name` — live rows carry
`initiator_command='omp'` / `'start'`-less strings (verified: the only
writer is `cli_harness.py:128-132` via `result.get("command",
harness_name)`, and no `result = {... "command" ...}` exists in
`cli_start/review/sync.py`).

Consequences, both adopted:

- **Backfill parses the prompt, not the command.** Prompts are the only
  surviving discriminator: fix prompts start with
  `Fix all open (not-resolved) review comments on this PR/MR:`
  (`backend.prompt_for_fix_comments`), review prompts carry the review
  header, the rest → starts-with on `initiator_command` → else NULL.
  Expected outcome: most history backfills correctly; truly ambiguous rows
  stay NULL per "never guess".
- **Going forward, set `result["command"]` at each call site**
  (`start`/`review` incl. `--fix-comments` variant/`sync`) so derivation
  never depends on prompt-sniffing again. `insert_session` gains explicit
  `session_type` + `metadata` params; `session_type` is derived once in
  `_run_harness` from `(result["command"], fix_comments-ish flag)` via a
  tested `derive_session_type()` helper.

## 4. Data model & migrations

- `_migrate_sessions_type(conn)`: `ADD COLUMN session_type TEXT` (NULL =
  unknown; CHECK omitted — SQLite CHECK on ALTER is not enforceable
  retroactively and validation lives in `insert_session`), `ADD COLUMN
  metadata TEXT NOT NULL DEFAULT '{}'`; then backfill per §3 over
  `(initiator_command, prompt)`. Re-runnable: skips columns present;
  backfill only touches rows where `session_type IS NULL`.
- `SESSION_TYPES = ("start", "review", "sync", "fix_comments")` in
  `store_sqlite.py` (mirrors `VENDORS`); `insert_session` validates and
  raises `ValueError` outside the enum (NULL allowed).
- `set_session_metadata(path, sid, patch: dict)` helper: read-modify-write
  of the JSON object under the same `connect()` lock discipline; used by
  the fix-stamp path. `get_session`/`list_sessions` parse `metadata` to
  dict (fallback `{}` on corrupt).

## 5. CLI & continue/new-session mechanics

- New `review` flag `--new-fix-session` (requires `--fix-comments`;
  web `BOOL_FLAGS` mirror + inventory test): forces a fresh transcript
  even when a review session exists.
- Continue path (no flag): CLI resolves the latest non-running `review`
  session for the worktree ref (`latest_review_session()` helper in
  `store_sessions.py`); passes its `file_path` as the effective
  `--session-file` (explicit `--session-file` still wins); records
  `result["reuse_session_id"]` so `_run_harness` skips `insert_session`
  and `_mirror_run`/CLI-finish stamps
  `metadata.review_comments_fixed_at/fixed_by_run_id` on exit 0 only.
- New-session path: normal insert with `session_type='fix_comments'`,
  `metadata.fixed_from_session_id` set; transcript is fresh (point 4).
- `--dry-run`/preview writes nothing (unchanged).

## 6. Web backend & frontend

- `POST /api/runs` `review` already proxies argv — add `--new-fix-session`
  to `BOOL_FLAGS["review"]` + `_validate_args` passthrough (inventory test
  `test_specs_mirror_cli_flags` covers drift).
- Continue needs the backend to find the session + inject `--session-file`
  without CWD touch: new read `GET /api/review-session?ref=` returning
  `{session_id, file_path} | null` (latest non-running review row);
  Launch Review form, when fixComments && !newSession, queries it and
  appends `--session-file <path>` to the run args. New-session checked →
  appends `--new-fix-session` instead.
- `launchConfig.ts`: `LaunchForm` gains `newFixSession: boolean`;
  checkbox rendered only when `mode === "review" && form.fixComments`.
- Sessions table: Type column (`session_type ?? "—"`), fixed-badge
  (`✓ fixed <relative time>` from `review_comments_fixed_at`, tooltip with
  run id) on `review` rows only; Detail page metadata block.
- `sessionKind()` (initiator_command→label) is superseded by `session_type`
  for new rows but kept as fallback for NULL-type history rows.

## 7. Testing & rollout

- Migration: idempotent re-run; backfill matrix (fix/review/start/sync
  prompts, junk → NULL); corrupt-metadata fallback.
- Derivation: `derive_session_type()` unit matrix incl. `--all` children.
- Continue: exit-0 stamps metadata (+at/run_id), non-zero leaves it;
  explicit `--session-file` wins; `--new-fix-session` inserts fresh
  `fix_comments` with `fixed_from_session_id`.
- API shape (`session_type`, `metadata` in rows/detail) + `tsc` + `vite
  build`; full `pytest -q`.
- Rollout (user-approved scope): `./install.sh` (rebuilds `web/dist`),
  restart `serve --port 3344`, verify Type column + fix flow; state.db
  migrates in place, history preserved.
