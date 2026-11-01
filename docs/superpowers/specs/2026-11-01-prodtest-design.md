# Prodtest design — Generate + Run Prod Test Plan (2026-11-01)

## Intent
Automated post-release production testing as two modular workagent actions backed by the
global `prodtest-plan` / `prodtest-run` skills. Plan is committed in the MR and reviewed;
results go to a run dir + redacted issue comment. Locked decisions: tombstone row lifecycle,
split buttons (Generate overlay / Run top-bar bulk), cleanup gate (warn missing, block
stale-or-invalid).

## §1 State + row lifecycle
- New `prodtest_items(issue_key|mr_ref PK, plan_path, plan_revision, plan_risk,
  plan_diff_hash, release_sha, released_at,
  status[pending|pending_prodtest|running|passed|failed|blocked], last_run_id, updated_at)`.
- Cleanup soft-delete keeps rows with `pending_prodtest|running`; `link gc/destroy-deleted`
  refuse them; `/api/status` exposes `prodtest:{status,risk,revision,last_run}` + Released section.
- Release records `release_sha/released_at`, sets issues `released`, status `prodtest_pending`.

## §2 CLI harness wiring (`src/workagent/cli_prodtest.py`)
- `workagent prodtest plan|run <ref>`; prompts = skill name + Appendix-A/B JSON; `_run_harness`
  reuse (preview/launch/terminal); `PRODTEST_*_RESULT:` last line with `result.json` fallback.
- plan: resolve via `resolve_any`/`parse_ref`; `update` when plan exists; re-run vendored
  `validate_plan.py`; commit plan + manifest additions (`prodtest: plan rN for <KEY>`), push,
  post summary (issue, else MR note); warn on `[HUMAN]`-diff / `proposed_subjects`.
- run: code preconditions (release_sha known, plan at SHA, validator exit 0); ephemeral
  read-only checkout at release_sha (`release_worktree.prepare` pattern);
  `run_dir` configurable; per-issue lock; timeout =
  `budget.max_minutes + version_check.timeout_s + 60s` via process-group kill.
- Flags mirror review; session types `prodtest_plan`/`prodtest_run` added to `derive_session_type`.

## §3 Safety guards (code-enforced, tested)
- Manifest safety diff gate (agent may touch only `path_rules` additions, `smoke_cases`
  additions, `proposed_subjects`); env allowlist (only vars named in `manifest.tools`);
  host/tool allowlist; SQL re-validation (SELECT/WITH, single stmt, no write/DDL keywords);
  mutating caps (`mutating_ok`, `max_amount`, cleanup always).
- Per-issue lock; overrun ⇒ BLOCKED + alert. Redaction (`prodtest_redact.py`, vendored
  patterns) before persisting anything. Fix loop guards: issue always; auto-MR only if
  `suspect==code, confidence==high, mode==issue+mr`, within file/line caps, no deny_paths,
  depth ≤ 1, dedupe `(issue, cases)`; fix sub-agent gets fresh worktree + restricted brief
  (no prod creds/tools/raw logs); resulting MR registered like an injected MR.
  Never merge/release/revert.

## §4 Deltas to existing actions
- Review: plan-file in MR diff ⇒ append plan-review checklist to prompt.
- Fix-Comments: stale `diff_hash` ⇒ trigger plan update mode (or loud warning).
- Cleanup: missing plan ⇒ warn (config `warn|block`); stale/invalid ⇒ block; non-empty
  `proposed_subjects` ⇒ admin warning. Enforced per row in `--merged --yes` bulk.
- Release: record sha/time, warn unresolved subjects, set `prodtest_pending`.

## §5 Web/API + observability
- `SPECS`/`BOOL_FLAGS`/`VAL_FLAGS` entries (`prodtest:<ref>`, `prodtest:run-all`) + contract +
  inventory + frontend control in same PR (parity checklist).
- Buttons: Generate in `RowActions` overlay (enabled when MR/PR exists); Run as Dashboard
  top-bar bulk (selected or all pending, confirm + scope detail, issue picker on ambiguity).
  Row badges: risk/revision/status/report link; run progress via run SSE view.
- Config: skill paths, run-dir base, fix_mode default, cleanup gate policy. Structured events
  with run ids; secrets never persisted.

## Tests / demo
- Unit: validators, transitions, gates, version-gate outcomes, loop guards/dedupe, redaction.
- Integration (fake harness + tracker): happy path, stale-block, gate-timeout BLOCKED,
  fail→issue, eligible-fix→MR-registered, ineligible→issue-only, second-run lock/dedupe.
- UI: button enablement, row persistence. Demo: fake/sample repo, one PASS + one FAIL run.

## Open risks
- `result.json` fallback requires harness cooperation; last-line parse stays fragile otherwise.
- Vendored scripts pinned by hash need a sync story with `~/.agents/skills` updates.
- Tombstone rows need GC-exemption plumbing in `link gc/destroy-deleted` + status filters.
