# Design: Running-Harness Tracking + `review --all` (issue #6)

Date: 2026-09-21 · Status: draft for review · Issue: https://github.com/ynsr/harness/issues/6

## Problem

The app has no record of which AI harness is running in which worktree, so two
harnesses can end up in the same worktree. Users also cannot see, from the CLI
or the web UI, which worktrees have an active harness. Reviewing many
worktrees means launching `harness review` one at a time by hand.

## Goals

1. Record the running AI harness per worktree — set by `start`, `review`,
   `sync`'s AI conflict-resolution run, and any other harness launch.
2. Hard guard rail: at most one live harness per worktree; a second launch is
   refused. No override flag — the issue says "hard".
3. CLI (`status`) and web UI show worktrees with an active harness.
4. `harness review --all`: review every not-reviewed linked worktree.
   `--all` implies non-TTY auto-approval and appends to the review prompt:
   "Auto add all comments to the PR/MR at yielding and don't wait for user
   approval". A worktree counts as reviewed once `harness review` successfully
   launched a harness for it.
5. `--sequential` (only with `--all`): review one-by-one instead of in
   parallel. `--fix` (only with `--all`): children additionally get
   "Auto-fix all identified issues after yielding and don't wait for user
   approval."
6. `reviewed` auto-resets when the branch gains commits past the reviewed-at
   tip (user decision), so `review --all` stays useful after new pushes.

## Non-goals

- No webapp run-registry rework: `harnesses.json` is the single
  cross-boundary truth for harness liveness; the webapp's child-run registry
  stays as-is.
- No remote/multi-host coordination (single-machine guard).
- No new backend endpoints — the web reads harness state via `/api/status`.

## State model

New store file `~/.config/harness/harnesses.json`, keyed by link key
(`jira:KEY`, `pr:<url>`, `branch:<branch>` — the same keys `links.json` uses):

```json
{ "<key>": { "harness": "omp", "pid": 4242, "started_at": 1758412345.1 } }
```

Written through the existing atomic+locked machinery in `store.py`. New
`store.py` functions:

- `load_harnesses() -> dict` — read + liveness sweep: entries whose `pid` is
  dead are dropped and the cleaned mapping persisted (crash-safe self-healing;
  stale entries never block). Liveness = `os.kill(pid, 0)` succeeds, or fails
  with `PermissionError` (process exists, owned by another user).
- `record_harness_run(key, harness)` — records `os.getpid()` (the CLI process
  about to `execvp` into the harness, or the parent that waits on the
  non-TTY child). Callers that wait clear it in `finally`.
- `clear_harness_run(key)`.
- `active_harness(key) -> dict | None` — swept live entry or None.

## Guard rail

`cli.py` helper `_guard_harness(key, worktree)`: if
`active_harness(key)` (or any other key whose recorded worktree path equals
this worktree) is live → `_fail` exit 1 with actionable stderr:
`worktree <path> already has a live harness (<name>, pid <N>, started <ts>) —
wait for it to finish or kill it`.

Guard call sites (before any harness launch):
- `start` — after the link key is known, before `_run_harness`.
- `review` — both the reuse path and the fresh-worktree path.
- `sync`'s AI conflict-resolution run (`--harness` / `--yes` non-TTY resolve) —
  before spawning omp in the worktree.
- Webapp runs are covered automatically: they execute `python -m harness`
  children, which run the same guards.

`--no-harness` starts nothing → not guarded, not recorded.

## Recording

In `_run_harness` (non-preview path): `store.record_harness_run(key, harness)`
immediately before `backend.launch`. TTY mode `execvp`s — the recorded pid is
the harness itself; when it exits the entry self-cleans on next read.
Non-TTY mode runs the child and waits — `_run_harness` clears the record in a
`finally` after `backend.launch` returns. `sync`'s conflict-run wraps its omp
spawn the same way (record before, clear in `finally`).

## Review bookkeeping

Link entry gains `reviewed: true` and `reviewed_at: <branch tip sha>`, set in
`review` immediately before the harness launch (launch failure on the
non-exec path clears both; on the exec path the process is replaced, so a
launched review is by definition successful). `--no-harness` previews do not
set it.

`review --all` picks a worktree as reviewable when: link is valid
(`worktrees.is_valid_worktree`), not `is_reviewed`, an open PR/MR URL resolves
(`worktrees.worktree_pr_url`), and no live harness is recorded for it
(pre-filter — the child guard would refuse anyway; skipped ones get a stderr
note). `is_reviewed(entry)` = entry has `reviewed` AND current branch tip ==
`reviewed_at`; a moved tip makes the worktree reviewable again (the stale flag
is simply ignored; the next successful review overwrites it).

## `review --all` orchestration

New `review` options: `--all`, `--sequential` (exit 2 without `--all`),
`--fix`, plus two internal prompt-segment flags the parent passes to children:
`--post-comments` (the issue's "auto add comments" sentence) and `--fix`
implies it plus the auto-fix sentence. These segments append to the existing
pr-reviewer prompt.

Parent flow: collect reviewable keys → for each, spawn
`sys.executable -m harness review <key> --no-tty --post-comments [--fix]` via
`subprocess.Popen` (parallel default; `--sequential` waits on each child
before starting the next) → stream per-child stderr notes → wait all →
per-key summary (exit code, key, PR URL) on stdout (JSON array with
`--json`). Children inherit the guard; the parent does not mark reviewed —
each child's normal review flow does.

## Display

- CLI `status`: new `harness` column — `omp 4242` when live, `—` otherwise
  (liveness-swept read; no extra git/subprocess cost beyond the pid check).
- Web: `/api/status` rows gain `harness` (name+pid or null). `StatusTable`
  shows a badge cell; `WorktreeDetail` (view mode) shows the same in its
  field list. No new endpoints, no new polling.

## Error handling

- Dead-pid entries: swept on read, never fatal.
- Lock contention on `harnesses.json`: existing fcntl blocking lock.
- `--all` with zero reviewable worktrees: prints "nothing to review" (stderr),
  exit 0.
- Child failure: reported in the summary; other children unaffected
  (mirrors `sync --all` continuation semantics).

## Testing

- `store.py`: record/clear/sweep round-trip; liveness with a spawned-then-
  killed pid; sweep persists the cleaned file.
- Guard: busy worktree refuses start/review with exit 1 and actionable message;
  free worktree passes; `--no-harness` unaffected.
- Recording: `_run_harness` records before launch, clears after non-TTY return.
- `review --all`: mocked Popen — parallel spawn count and argv shape
  (`--no-tty --post-comments [--fix]`), sequential ordering, no-PR skip note,
  nothing-to-review exit 0, summary JSON shape.
- Reviewed flag: set on launch, auto-reset when tip moves, stale flag ignored.
- Webapp: `/api/status` includes `harness` field.
- Web: `npm run build` + eslint on touched files.
