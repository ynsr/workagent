# Fix-wave report — 2026-11-01 (blocking findings from final whole-branch review)

Worktree: `.worktrees/prodtest` @ `b8803b8` (parent `7b4fbe8`).

## Fixes
- **P0 `plan_diff_hash`**: `_finish_plan` (`src/workagent/cli_prodtest.py`) now computes
  `current_prodtest_diff` at the dirty-tree tip and persists it in the same upsert
  (`plan_diff_hash`), so byte-identical plans pass the cleanup gate. Also preserves
  `pending_prodtest` when the release columns are already recorded (no clobber to `pending`).
- **P1a Launch tab**: prodtest tab now has a Plan/Run segmented selector
  (`ProdtestActionSelect`; `prodtestAction: plan|run` in `launchConfig`, prefs slice,
  `buildArgs` emits `prodtest plan <ref>` vs `prodtest run <ref>`). `API_CONTRACT.md`
  target-key section + launch-prefs doc updated.
- **P1b Spec §4 fix-comments delta**: `_prodtest_fix_warning` in `cli_review.py` prepends a
  loud WARNING to the fix-comments prompt when the MR touches a prodtest plan and the gate
  reports `blocked:stale` / `blocked:invalid-plan` / `warn:*`. Wired into both
  fix-comments dispatch paths (reuse + fresh worktree).
- **P1c `current_prodtest_diff` CWD fallback**: returns `''` immediately when the repo dir
  is missing/unusable (no `cwd=None` accident). Never raises (unchanged).
- **P1d run binding + fix log-only**: `_plan_release_binding` compares working-tree plan
  bytes against `git show <release_sha>:<plan>` (warn on missing/unverifiable, block on
  mismatch); `_enforce_plan_release_binding` fails by default, `warn` via
  `manifest.fix_policy.plan_binding: warn`. `_create_fix_followup` files a real
  follow-up issue (jira/gh/glab by source-issue URL) for FAIL/BLOCKED when
  `fix_mode` is `issue`/`issue+mr` and the harness reported no `issue_url`;
  `report-only` keeps the old log-only line.

## Tests
- New: `tests/test_prodtest_fixwave.py` (8 tests: hash persist, status preserve,
  missing-dir `''`, warning empty/loud, binding block/missing/no-repo, follow-up
  jira create, report-only no-create).
- `uv run pytest tests/test_prodtest_fixwave.py tests/test_cli_prodtest_plan.py tests/test_cli_prodtest_run.py` → **15 passed**.
- Full non-web suite (`--ignore` 4 fastapi-dependent modules, no `fastapi` in env) →
  **847 passed, 1 failed**: `test_cli_nav.py::test_doctor_missing_and_ok` also fails on
  stashed (pre-change) tree — pre-existing, unrelated.

## Concerns / follow-ups
- `glab issue create` needs a project path/id context; bare-host repos only warn (harness owns follow-up there).
- Frontend `tsc` not run (no node toolchain invoked); Launch/StartForm/launchConfig edits are small and mirror the existing ReviewAction pattern.
- Web parity checklist: `web_args.py` needs no change (no new CLI flags — plan/run are existing subcommands).
