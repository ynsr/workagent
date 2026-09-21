# Sessions on SQLite — design (issues #9-core + #10 deltas)

Status: approved by user 2026-09-21 (chat).
Scope: SQLite sessions core ONLY. Deferred: #9-remainder batch (Launch 1h
cache + `--reset-cache`, Repos tracker-project save bug, Links tracker
dropdown, auto-link buttons, dropdown UX, Dashboard link-outs), Launch
TTY-foreground display, Repos-page editing, template sync.

## 1. Storage: stdlib `sqlite3` repository module

New `src/harness/store_sqlite.py` (no ORM — see §7). One `state.db` in
`config_dir()` holds everything; the one-shot migration deletes
`links.json` / `pr_cache.json` / `harnesses.json` after row-count
verification and keeps `config.json` (repos registry + new `scan_root`).

## 2. Schema v1

- `trackers(key_ref TEXT PK, remote_url TEXT, vendor TEXT)` — vendor in
  (`github`, `gitlab`, `jira`).
- `repos(key_ref TEXT PK, path TEXT UNIQUE, tracker_key → trackers,
  remote TEXT)`.
- `worktrees(ref_key TEXT PK, path TEXT UNIQUE, branch TEXT UNIQUE,
  repo_key → repos ON DELETE CASCADE, issue_url TEXT, pr_url TEXT,
  added_at TEXT)` — triple-key uniqueness from #2 becomes real UNIQUE
  constraints.
- `pr_cache(branch TEXT PK, payload JSON, checked_at TEXT)` — same TTLs:
  PR 3h, negative 30m, CI 10m sha-gated. TTL logic moves unchanged.
- `sessions(id TEXT PK, worktree_ref → worktrees ON DELETE CASCADE,
  state TEXT, runtime_name TEXT, initiator_command TEXT, prompt TEXT,
  file_path TEXT, created_at TEXT)` — id shape §3; state in (`running`,
  `finished`, `failed`); session history dies with its worktree (user
  chose hard FK cascade).
- `runs(id INTEGER PK AUTOINCREMENT, session_id → sessions ON DELETE
  CASCADE, command TEXT, args JSON, exit_code INT, created_at TEXT)` —
  ONLY session-owned runs persist (§5).

## 3. Session id: timestamp + 4-digit random

`strftime('%Y-%m-%dT%H-%M-%S') + '-' + ms(3) + 'Z-' + rand(1000-9999)`,
e.g. `2026-09-15T00-15-24-928Z-4821` (matches the #10 example shape).
`TEXT PRIMARY KEY`; sort by `created_at`, not id. PK violation on same-ms
collision → retry with a fresh suffix. Human-scannable, roughly ordered.

## 4. Runtime interface refactor

`backend.py` gains a `Runtime` ABC (`name`, `command_argv`,
`launch`, `session_file_flag`); `OmpRuntime` first implementation.
`_run_harness` takes a `Runtime`, not a `harness: str`; the
`if harness != "omp"` gate becomes a runtime registry. New runtimes add
a subclass, no dependent-class changes.

## 5. Runs: session-owned persist, rest ephemeral

Web runs registry stays in-memory as today; runs spawned for a session
are additionally mirrored into `runs`. DB keeps only session-relevant
runs — no clutter. `_run_harness` inserts the session row (`running`)
immediately before `backend.launch`, flips to `finished`/`failed` in the
existing `finally` (alongside the harness-guard clear). Only real
launches write rows: `--dry-run` / `--no-harness` / never-exec paths
write nothing. Non-session CLI runs (`cleanup`, `repo`, `link`, …) stay
ephemeral.

## 6. `file_path`: persist + pass to runtime (recommended)

`~/.config/harness/sessions/<runtime>/<session_id>.jsonl`, created
eagerly at session insert, stored in `sessions.file_path`, passed to the
runtime on every execution via its `session_file_flag`. If the runtime
ignores the flag and the file is missing, surface `transcript: missing`
(detectable) rather than silently assuming a derived path. Cost: one
mkdir per session + per-runtime flag mapping. Alternative rejected:
derive-on-read breaks silently when a runtime writes elsewhere.

## 7. ORM tradeoff (answers #10 question)

stdlib `sqlite3` + thin repository: zero new deps (web extra already
opt-in; every CLI invocation pays import cost), full control over the
one-shot JSON→SQLite migration, no model/migration ceremony for ≤6
tables with a single writer + one `serve` process. SQLAlchemy would buy
declarative models + Alembic, at the cost of a hard dependency,
lazy-import weight, and impedance mismatch with dict-shaped `store.py`
call sites. Revisit only if schema v2 proves painful.

## 8. Cleanup merges open PR/MRs (from #10)

`_cleanup_one`: open `pr_data.state` → merge branch into destination via
`gh pr merge --squash` / `glab mr merge --squash` (new `--no-squash`
opts out to the old close-PR path), then remove worktree, drop link,
delete remote branch LAST (`git push origin --delete` after local
`--delete-branch` succeeds). Dry-run prints the merge step; `--merged`
sweep applies per row. Never lose in-progress work to a close.

## 9. API + web

- `GET /api/sessions` → list (id, worktree_ref, runtime, command, state,
  created_at); `GET /api/sessions/{id}` → detail + prompt + file_path +
  runs.
- `Sessions` page: table + detail (prompt viewer, transcript-missing
  badge, per-session runs).
- `GET /api/candidates` gains `worktrees` key (issue #8, implemented
  separately).

## 10. Migration

`harness migrate` (or auto-migrate on first run): read the three JSON
files + `config.json` repos/trackers → write rows → verify counts →
delete JSON files. Failure modes: missing files (fresh install — init
empty), corrupt JSON (abort, leave files, exit 2 with path), count
mismatch (abort, leave files). Idempotent: re-run on an existing
`state.db` is a no-op.

## 11. Tests

Repository unit tests (CRUD, cascade delete, PK-retry), migration tests
(JSON fixture → row counts → files deleted; corrupt-JSON abort),
`_run_harness` session-row tests (launch writes, dry-run doesn't),
endpoint tests (list/detail shape), Sessions page via tsc+eslint (no web
test runner — T6 binding).
