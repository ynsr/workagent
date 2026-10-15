# Pre-Release Command — Design Spec

Date: 2026-10-15. Status: awaiting user review.
Terminology: **Release** everywhere (backend + frontend). The word Deploy MUST NOT appear in command names, flags, columns, buttons, or components.

## 1. Outcome

Repo owners attach a per-repo quality gate to the release flow: a shell
command stored on the repo row (`commands.pre-release`), auto-suggested
from project type at add-time (overridable), editable inline in the
Repos table, executed locally in the repo path after the diff gate and
before MR-open. A failing gate aborts the release with the command's
reason; nothing is pushed, no MR is opened.

## 2. Schema (repos table)

- `repos.commands TEXT NOT NULL DEFAULT '{}'` — JSON object, extensible
  for future keys (`post-release`, …). `pre-release` key holds
  `str | None`; absent key = null = no gate.
- Migration backfills existing rows to `'{}'`; never raises (failure
  leaves `'{}'` + stderr warning).
- Read path carries it through: `store_trackers.load_repos` SELECT +
  `register_repo_row` upsert + `GET /api/repos` spread + `Repo` type +
  CLI `--json`. `repo list` gains the column.

## 3. Detection (`repos.detect_prerelease_command(path)`)

Pure function, local path in, `str | None` out. Never raises (unknown =
null). Used at add-time only — never re-derived silently on update.

- Node.js (`package.json`): `npm test` when `scripts.test` exists, else
  `npm run build` when `scripts.build` exists, else null.
- Java Maven (`pom.xml`): `mvn clean; mvn verify`.
- Java Gradle (`build.gradle[.kts]`, `settings.gradle[.kts]`,
  `gradlew`): `./gradlew clean test` when `gradlew` is executable, else
  `gradle clean test`.
- Python (`pyproject.toml`): `uv sync; uv run pytest` when `uv.lock`
  present, else `pip install -e .; python -m pytest`; `requirements.txt`
  fallback: `pip install -r requirements.txt; python -m pytest`.
- No markers: null.
- Collision priority (deterministic, documented): Node > Maven >
  Gradle > Python.

## 4. CLI (owns the logic)

- `repo add --pre-release CMD`: omitted → auto-detect from `--path`;
  `--pre-release ""` clears to null.
- `repo update NAME --pre-release CMD`: None-preserve semantics (same as
  the `--default-branch`/`--release-branch` flags); `""` clears.
- `repo list`: gains the `pre-release` column (empty renders `—`).

## 5. Web wiring (CLI owns logic; web mirrors flags)

- `BOOL_FLAGS`/`VAL_FLAGS` gain `repo add/update` entries;
  `SPECS`/`_validate_args` accept `--pre-release`. Parity checklist
  applies: `API_CONTRACT.md` + `FEATURE_INVENTORY.md` + frontend
  control + `tests/test_cli.py` + `tests/test_webapp.py`.
- `GET /api/repos/detect?path=…` → `{pre-release: str | null}`
  (read-only, always 200; unknown path → null, never 404).
- Add form: `pre-release` text field auto-fills on path-field blur via
  the detect endpoint; user can override before submit; empty = null.

## 6. UI

- **Repos table**: `pre-release` cell is click-to-edit (reuse the
  existing inline-edit cell pattern); empty renders `—`; save dispatches
  `repo update`; failure toasts and the value reverts.
- **Release flow UI**: no change — gate errors surface through the
  existing run-failure path (toast + run log).

## 7. Release execution (hook point)

In `run_release` (`src/workagent/cli_release.py`), after the
diff-gate/releasable check and the `--dry-run` plan exit, before
existing-MR reuse / MR-open:

1. Read `pre-release` from the repo row; null/empty → skip straight to
   MR-open.
2. `--dry-run`: print the command with `dry_run: True`, do not execute.
3. Otherwise run via `run_cmd` (`errors.py` convention) with
   `cwd=repo path`, shell, output streamed to stderr.
4. Non-zero exit / timeout / missing binary → `HarnessError` →
   `_fail("pre-release command failed: …", EXIT_GENERAL)`: no MR opened,
   nothing pushed.

## 8. Testing

- Migration: existing rows backfill to `'{}'`; bad JSON never raises.
- Detection matrix: one fixture dir per project type (Node test-script,
  Node build-only, Node bare, Maven, Gradle wrapper, Gradle bare,
  Python uv, Python requirements, empty) + collision + unreadable path.
- CLI: `repo add` auto-detect, explicit override, `""` clears; `repo
  update` None-preserves; `repo list` shows the column.
- Release: failing gate → no MR call, exit 1; null gate → straight to
  MR; dry-run shows but does not execute.
- Web: specs-mirror test, detect endpoint 200-on-unknown, frontend
  `tsc` + build.
- Full suite green + `./install.sh` + commit/push before done.

## 9. Out of scope

Post-release hooks (schema allows, not implemented), remote-CI gating,
per-branch gates, gate output parsing (pass/fail only), scheduled
release, secret/env management for gate commands.
