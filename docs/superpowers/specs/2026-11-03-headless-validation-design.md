# Headless harness validation loop — design spec

Date: 2026-11-03. Status: implemented.

## Intent

Headless (`--no-tty --launch`) harness runs must self-validate before
exiting so incomplete work is retried automatically instead of surfacing
as silent success. Scope: headless only (preview/terminal/interactive
unchanged). Retry resumes the same session (`omp --resume`); the judge
runs as a fresh headless session. Deterministic gates run first (zero
LLM cost); the AI judge runs only when gates pass.

## Verdict protocol

The harness is instructed to end its reply with exactly one bare token
line. Parser: last non-empty line of `last_assistant_text(transcript)`,
stripped of quotes/fences, case-insensitive substring match (tolerant).
Anything else = parse-fail, treated as `INCOMPLETE_OR_FAILED_JOB` and
logged as `parse_fail`.

| Token | Emitter | Meaning |
|---|---|---|
| `JOB_DONE` | main, judge | job complete / validation passed |
| `INCOMPLETE_OR_FAILED_JOB` | main, judge | incomplete or failed |
| `INTENTIONALLY_UNCOMMITTED_CHANGES` | main, Phase-1 only | dirty tree is intentional; accept and exit |
| `WAITING_USER_RESPONSE` | main only, never judge | blocked on a human decision; exit 2, no retry consumed |
| `MAX_RETRIES_EXCEEDED` | workagent (terminal) | 3 re-runs exhausted; exit 1 |

Priority on multi-token lines: `MAX_RETRIES_EXCEEDED` >
`WAITING_USER_RESPONSE` > `INTENTIONALLY_UNCOMMITTED_CHANGES` >
`JOB_DONE` / `INCOMPLETE_OR_FAILED_JOB`.

`WAITING_USER_RESPONSE` exits immediately (exit 2, names worktree +
session file); the attempt counter in session metadata is preserved so a
manual resume restarts validation where it stopped. If the judge cannot
decide, it returns `INCOMPLETE_OR_FAILED_JOB` with reasons (never
`WAITING_USER_RESPONSE`).

## Phase 1 — dirty-tree gate (session types `start`, `fix_comments` only)

1. After headless exit: `git status --porcelain` in the worktree.
2. Clean → proceed to Phase 2.
3. Dirty → resume same session with a continuation prompt carrying the
   porcelain output and branch: continue the job (commit + push to
   `origin/<branch>`), or reply `INTENTIONALLY_UNCOMMITTED_CHANGES`
   (last line) and exit.
4. That token → accept-and-exit 0, logged `intentional_dirty`, judge
   skipped. Otherwise re-check porcelain; still dirty = one retry
   attempt; clean → Phase 2.

## Phase 2 — deterministic gates, then AI judge (all other cases)

Deterministic gates (no LLM, run before any judge):
1. Harness exit code == 0 (non-zero → retry with stderr tail).
2. Branch pushed: `HEAD` == upstream tip (or no upstream + clean tree →
   pass for no-change sessions).
3. Transcript sanity: `last_assistant_text` non-empty; toolResult
   error-rate < 50%.

Gate fail → resume same session with gate output embedded; no judge
spent. Gate pass → fresh headless judge session with `{original prompt
(capped 8k), previous output (capped 4k), gate results}`; instruction:
validate only, end with `JOB_DONE` or `INCOMPLETE_OR_FAILED_JOB` plus
1–3 reason lines. Extra artifact check: `git diff --stat` shown to the
judge, so an empty diff behind a `JOB_DONE` claim fails validation.

## Retry loop + Run-record logging

- Loop lives in new `src/workagent/cli_validate.py` (≤500 lines),
  called from the `_run_harness` headless path only.
- Shared counter `validation_attempts` in session metadata; default
  max 3 re-runs (4 attempts total). The cap is configurable —
  `WORKAGENT_MAX_RETRIES` env var, else `validation.max_retries` in
  `config.json`, else 3 (see `cli_validate._resolve_max_retries`).
  Exceeding it → `MAX_RETRIES_EXCEEDED`, exit 1 with attempts summary.
- `ALTER TABLE runs ADD COLUMN validation TEXT NOT NULL DEFAULT '{}'`
  (additive; old rows read as `{}`). One `insert_run` per attempt with
  `validation` = `{attempt, phase, deterministic_gates, judge_session_id,
  judge_verdict, judge_reasons, verdict_parse, harness_output_tail
  (2k)}`. Full output stays in `output` + transcript; this column is the
  trace index. Read-only in `GET /api/runs` (full row already
  returned) — no new endpoint, no webapp SPECS drift (no new flags).

## Touch list

- NEW `src/workagent/cli_validate.py`: gates, judge prompt, loop, parser.
- `src/workagent/cli_harness.py`: hook validation into headless path.
- `src/workagent/backend.py`: verdict-token suffixes on headless prompts.
- `src/workagent/store_sqlite.py` + `store_sessions.py`: `validation`
  column, insert/list plumbing.
- `tests/test_cli_validate.py` (new): parser, gate logic, retry cap.

## Open risks

- Judge doubles LLM cost/latency per headless run (accepted: gates catch
  the common fails for free).
- Resume-carried poisoned context can loop 3× before terminal (accepted:
  cap bounds it; trace column debugs it).
- Tolerant parse may accept a `JOB_DONE` quoted inside a failure
  narrative (accepted; priority order + judge reason lines mitigate).
