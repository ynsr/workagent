# Add Antigravity AI Harness Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `AgyHarness` (invoking `agy-guard`), refactor the `Harness` interface (`abc.ABC`) so zero `omp` calls/assumptions remain outside `OmpHarness`, switch `DEFAULT_HARNESS` to `"agy"`, add `config` CLI + `/api/config`, and restore the Harness choosing input across all Web UI action forms, Launch pages, Run Flow modals/cards, and Settings.

**Architecture:** `backend.Harness` becomes a strict `abc.ABC` owning argv construction, launch, session continuity, resume/bare-terminal commands, transcript discovery, and transcript parsing; `OmpHarness` and `AgyHarness` implement it. `AgyHarness` captures `conversation_id` from `agy-guard --output-format stream-json` (headless) or `~/.gemini/antigravity-cli/brain/` (interactive/terminal), mirrors normalized JSONL into `~/.config/workagent/runs/agy/…`, and resumes via `agy-guard --conversation <id>`. The React UI adds a shared `<HarnessSelect>` driven by `GET /api/config` across all action/launch/flow/settings surfaces.

**Tech Stack:** Python 3.12+ (Typer, FastAPI, SQLite), React + TypeScript + TanStack Query + Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-antigravity-harness-support-design.md`

## Global Constraints

- Always use `agy-guard` instead of `agy` everywhere in `AgyHarness`, tests, docs, and UI hints.
- Zero `omp` CLI calls, `--session-dir`/`--resume` flags, or `"omp"` defaults may exist outside `OmpHarness` (and its dedicated tests / migration fixtures).
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Parity checklist: CLI flag → `web_args.py` (`SPECS`/`BOOL_FLAGS`/`VAL_FLAGS`) → `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` → frontend control → `tests/test_cli_*.py` + `tests/test_webapp_data.py` (`test_specs_mirror_cli_flags`) + `tests/test_webapp_runs.py`.
- Keep modules focused and under existing size conventions (no code duplication between `OmpHarness` and `AgyHarness`).

## Review Focus

- `agy-guard` `-p` and `-i` are string-valued flags that consume the next token unless passed as `-p=<prompt>` / `-i=<prompt>` with other flags placed before them.
- `AgyHarness` headless `launch()` must stream NDJSON, persist `conversation_id` immediately, mirror normalized events to `runs/agy/...jsonl`, enforce `timeout_s` via process-group `SIGTERM`/`SIGKILL`, and fail when `result.status != "SUCCESS"` or exit code != 0.
- `sync --harness` transitions cleanly from `bool` to `Optional[str]` across `cli_sync.py`, `cli_flow_util.py`, `web_args.py`, and tests.
- Every Web UI surface listed in Spec §4.2 passes `--harness` (when explicitly chosen) and displays effective defaults accurately.

---

### Task 1: Refactor `Harness` ABC, `OmpHarness`, and `store.DEFAULT_HARNESS`

**Files:**
- Modify: `src/workagent/backend.py`
- Modify: `src/workagent/store.py`
- Modify: `src/workagent/doctor.py`
- Test: `tests/test_backend.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `store.load_config`, `env.merged_env`, `env.launch_env`, `errors.HarnessError`, `errors.HarnessTimeout`.
- Produces: abstract `Harness(abc.ABC)` (`name`, `binary`, `judge_argv`, `command_argv`, `launch`, `session_file_flag`, `resume_argv`, `bare_terminal_argv`, `extract_last_assistant_text`, `tool_error_rate`, `watch_transcript`), `OmpHarness`, `store.DEFAULT_HARNESS = "agy"`, `store.SUPPORTED_HARNESSES = ("agy", "omp")`, `store.default_harness()`, `doctor.check` including `"agy-guard"`.

- [ ] **Step 1: Write failing tests**

Add tests in `tests/test_backend.py` and `tests/test_store.py`:
- `Harness` cannot be instantiated directly (`TypeError` from `abc.ABC`).
- `OmpHarness` implements `resume_argv` (`omp --resume` vs `omp --session-dir`), `bare_terminal_argv` (`omp --session-dir` vs `omp`), and `session_file_flag`.
- `store.DEFAULT_HARNESS == "agy"` and `store.default_harness()` returns `"agy"` when unset.
- `doctor.check()` checks `"agy-guard"` in `tools`.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_backend.py tests/test_store.py -v`
Expected: FAIL (abstract `Harness`, `resume_argv`, `bare_terminal_argv`, `DEFAULT_HARNESS == "agy"` not yet implemented).

- [ ] **Step 3: Implement `Harness` ABC, extract shared process/timeout helper, move all `omp` logic into `OmpHarness`, update `store.py` & `doctor.py`**

- In `backend.py`: make `Harness(abc.ABC)`, remove `omp` `--resume`/`--session-dir` implementation from base `Harness`, add shared subprocess/timeout/exec helper `_run_process(...)` used by `OmpHarness` (and Task 2's `AgyHarness`).
- Update `store.py`: `DEFAULT_HARNESS = "agy"`, `SUPPORTED_HARNESSES = ("agy", "omp")`, add `default_harness() -> str`.
- Update `doctor.py`: `("git-wt", "agy-guard", "omp", "gh", "glab", "jira")`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_backend.py tests/test_store.py -v`
Expected: PASS (update any existing `test_store.py` assertion that pinned `"omp"` as default).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/backend.py src/workagent/store.py src/workagent/doctor.py tests/test_backend.py tests/test_store.py
git commit -m "[113] Make Harness an ABC, isolate OmpHarness, default to agy"
```

---

### Task 2: Implement `AgyHarness` + Normalized Transcript Mirroring & `harness_summary` Support

**Files:**
- Modify: `src/workagent/backend.py` (or add `src/workagent/agy_harness.py` imported by `backend.py` to keep file sizes small)
- Modify: `src/workagent/harness_summary.py`
- Test: `tests/test_backend.py`, `tests/test_harness_summary.py`

**Interfaces:**
- Consumes: `agy-guard` CLI contract (`-p=<prompt>`, `-i=<prompt>`, `--dangerously-skip-permissions`, `--output-format stream-json`, `--conversation <id>`, `--model gemini-3.8-flash-low --disable-slash-commands`), `~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl`.
- Produces: `AgyHarness(Harness)` registered as `HARNESSES["agy"]`; NDJSON streaming + `agy_meta` header + normalized JSONL mirroring; `extract_last_assistant_text` & `tool_error_rate` for `agy` transcripts; `watch_transcript` for interactive/terminal brain discovery.

- [ ] **Step 1: Write failing tests**

In `tests/test_backend.py` and `tests/test_harness_summary.py`:
- `AgyHarness.command_argv("fix bug", no_tty=True)` → `["agy-guard", "--dangerously-skip-permissions", "--output-format", "stream-json", "-p=fix bug"]` (asserting `argv[0] == "agy-guard"` and `-p=` precedes/attaches prompt safely even when `extra_args` are passed).
- `AgyHarness.command_argv("fix bug", no_tty=False)` → `["agy-guard", "-i=fix bug"]`; `command_argv("", no_tty=False)` → `["agy-guard"]`.
- `AgyHarness.judge_argv == ["--model", "gemini-3.8-flash-low", "--disable-slash-commands"]`.
- `AgyHarness.session_file_flag` & `resume_argv`: reads `conversation_id` from `agy_meta` line or `metadata` and returns `["--conversation", cid]` / `["agy-guard", "--conversation", cid]`; returns `[]` / `["agy-guard"]` when absent.
- `AgyHarness.launch(no_tty=True)` with mocked `Popen` emitting `init` + `step_update` + `result` NDJSON writes `session_file` with `agy_meta` + normalized lines, invokes `on_conversation_id`, returns 0 on `SUCCESS` and non-zero on failure, and kills process group on timeout.
- `harness_summary.last_assistant_text` and `tool_error_rate` parse `agy` normalized JSONL and native `PLANNER_RESPONSE` JSONL.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_backend.py tests/test_harness_summary.py -v`
Expected: FAIL (`AgyHarness` not yet implemented).

- [ ] **Step 3: Implement `AgyHarness` & `harness_summary` support**

- Implement `AgyHarness` (`binary = "agy-guard"`, `command_argv`, `launch`, `session_file_flag`, `resume_argv`, `bare_terminal_argv`, `watch_transcript`, `extract_last_assistant_text`, `tool_error_rate`).
- Register `"agy": AgyHarness()` in `backend.HARNESSES`.
- Extend `harness_summary.py` to handle `agy` normalized events and native `PLANNER_RESPONSE` / tool-error records cleanly alongside `omp`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_backend.py tests/test_harness_summary.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/backend.py src/workagent/harness_summary.py tests/test_backend.py tests/test_harness_summary.py
git commit -m "[113] Implement AgyHarness using agy-guard and normalized transcripts"
```

---

### Task 3: Eliminate All Direct `omp` Calls & Assumptions Outside `OmpHarness`

**Files:**
- Modify: `src/workagent/cli_harness.py`
- Modify: `src/workagent/suggest_icon.py`
- Modify: `src/workagent/cli_sync.py`
- Modify: `src/workagent/cli_release.py`, `src/workagent/cli_release_fix.py`
- Modify: `src/workagent/cli_flow.py`, `src/workagent/cli_flow_util.py`, `src/workagent/cli_flow_steps.py`
- Modify: `src/workagent/cli_start.py`, `src/workagent/cli_review.py`, `src/workagent/cli_adhoc.py`, `src/workagent/cli_action.py`, `src/workagent/cli_message.py`, `src/workagent/cli_prodtest_plan.py`, `src/workagent/cli_prodtest_run.py`, `src/workagent/cli_prodtest_checks.py`
- Modify: `src/workagent/web_runs.py`, `src/workagent/web_runs_routes.py`, `src/workagent/web_args.py`
- Test: `tests/test_backend.py`, `tests/test_cli_sync.py`, `tests/test_cli_flow.py`, `tests/test_cli_release.py`, `tests/test_suggest_icon.py`, `tests/test_webapp_runs.py`, `tests/test_webapp_data.py`

**Interfaces:**
- Consumes: `backend.get_harness`, `store.default_harness`, `Harness` abstract methods.
- Produces: harness-agnostic `cli_harness._run_harness`, `watch-and-exec --harness`, `suggest_icon.run_suggest_icon`, `sync --harness <name>`, `release fix-default --harness <name>`, `flow run --harness <name>`, `message flow run --harness <name>`, `web_runs._resume_shell_command(..., harness_name=...)`, `POST /api/worktrees/{ref}/terminal` (`harness` body param), `Run`/`RunDetail` `harness_name` & `resume_command` fields.

- [ ] **Step 1: Write failing tests**

- Add an AST/source test `test_no_omp_outside_omp_harness` in `tests/test_backend.py` scanning `src/workagent/*.py` (excluding `OmpHarness` class body) for `"omp"` command invocations / fallback strings.
- Add tests for `sync --harness agy` / `sync --harness omp`, `release fix-default --harness agy`, `flow run --harness agy`, `suggest_icon` with `agy`, `web_runs._resume_shell_command` for `agy` (`agy-guard --conversation ...`) vs `omp` (`omp --resume ...`), and `POST /api/worktrees/{ref}/terminal` with `{"mode": "harness", "harness": "agy"}`.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_backend.py tests/test_cli_sync.py tests/test_cli_flow.py tests/test_cli_release.py tests/test_suggest_icon.py tests/test_webapp_runs.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor all Python modules listed above**

- `cli_harness.py`: delegate `session_file_flag`, `last_assistant_text`, and `watch-and-exec --harness` watching to `Harness`; pass `session_file` and `on_conversation_id` callback into `backend.launch`; replace `"omp"` defaults with `store.default_harness()`.
- `suggest_icon.py`: default `harness: str | None = None` → `store.default_harness()`; support both `omp` minted files and `agy` written `judge_path`.
- `cli_sync.py`: change `harness` option to `Optional[str]`; validate via `backend.get_harness`; pass `harness_name` into `_run_harness`; update `cli_flow_util.SYNC_FLAGS` to `("harness", "--harness", "str")` and `web_args.py` `VAL_FLAGS["sync"]`.
- `cli_release.py` & `cli_release_fix.py`: add `--harness` to `fix_default` and `run_fix_default`; update `web_args.VAL_FLAGS["release"]`.
- `cli_flow.py` & `cli_flow_steps.py` & `cli_message.py`: add `--harness` to `flow run` & `message flow run`; pass top-level harness override into step runners; replace all `"omp"` fallbacks with `store.default_harness()`.
- `cli_review.py`: forward `--harness` to child `review` invocations in `review --all`.
- `web_runs.py`, `web_runs_routes.py`, `web_args.py`: remove all `"omp"` hardcodes; delegate resume command & bare terminal argv to `Harness`; populate `harness_name` and `resume_command` on `Run`/`RunDetail` API payloads; make `_session_file_for` harness-aware.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_backend.py tests/test_cli_sync.py tests/test_cli_flow.py tests/test_cli_release.py tests/test_suggest_icon.py tests/test_webapp_runs.py tests/test_webapp_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/ tests/
git commit -m "[113] Route all harness ops via Harness ABC; remove omp leaks"
```

---

### Task 4: Add `workagent config` CLI & `GET /api/config` Endpoint

**Files:**
- Create: `src/workagent/cli_config.py`
- Modify: `src/workagent/cli.py`, `src/workagent/web_args.py`, `src/workagent/webapp.py`
- Test: `tests/test_cli_config.py` (new), `tests/test_webapp_data.py`, `tests/test_webapp_core.py`

**Interfaces:**
- Consumes: `store.load_config`, `store.save_config`, `backend.HARNESSES`, `shutil.which`.
- Produces: `workagent config show [--json]`, `workagent config set default-harness <name> [--json]`, `GET /api/config`, `SPECS["config"]` (`confirm: false`).

- [ ] **Step 1: Write failing tests**

- CLI test for `workagent config show --json` and `workagent config set default-harness omp` (and rejecting unknown harness with exit 2).
- Webapp test for `GET /api/config` returning `default_harness`, `supported_harnesses`, and `harnesses` availability list, plus `POST /api/runs` with `{"command": "config", "args": ["set", "default-harness", "omp"]}` persisting to `config.json`.
- `test_specs_mirror_cli_flags` passes with `config` subcommands registered.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_cli_config.py tests/test_webapp_data.py tests/test_webapp_core.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `cli_config.py`, wire into `cli.py`, `web_args.py`, and `webapp.py`**

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_cli_config.py tests/test_webapp_data.py tests/test_webapp_core.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_config.py src/workagent/cli.py src/workagent/web_args.py src/workagent/webapp.py tests/test_cli_config.py tests/test_webapp_data.py tests/test_webapp_core.py
git commit -m "[113] Add config CLI subcommands and GET /api/config endpoint"
```

---

### Task 5: Web UI Shared `<HarnessSelect>`, `useConfig`, Launch Pages, & Run Resume/Copy

**Files:**
- Create: `web/src/components/HarnessSelect.tsx`
- Modify: `web/src/lib/api.ts`, `web/src/lib/queries.ts`, `web/src/lib/format.ts`, `web/src/lib/launchConfig.ts`, `web/src/lib/launchPrefs.ts`, `web/src/lib/messageLaunchConfig.ts`
- Modify: `web/src/components/StartForm.tsx`, `web/src/components/RunActions.tsx`, `web/src/pages/Launch.tsx`, `web/src/pages/MessageLaunch.tsx`, `web/src/pages/ProdtestLaunch.tsx`
- Test: `web/src/lib/launchConfig.test.ts`, `web/src/lib/messageLaunchConfig.test.ts`, `web/src/pages/Runs.test.tsx`, `web/src/components/StartForm.test.tsx` (new if needed)

**Interfaces:**
- Consumes: `GET /api/config`, `Run.harness_name`, `Run.resume_command`.
- Produces: `<HarnessSelect>` component, `useConfig()` query, `--harness <name>` arg emission in `buildStartArgs`, `buildReviewArgs`, `Launch` (`sync`, `adhoc`), `buildMessageArgs`, `ProdtestLaunch`, dynamic Launch warning banner, and harness-accurate `RunResumeActions` copy command.

- [ ] **Step 1: Write failing Vitest tests**

- `launchConfig.test.ts` / `StartForm`: `buildStartArgs` & `buildReviewArgs` include `--harness agy` / `--harness omp` when set, omit when `""`.
- `messageLaunchConfig.test.ts`: `buildMessageArgs` includes `--harness` across `adhoc`, `validate`, `draft-reply`, `action`, `flow`.
- `Runs.test.tsx`: `RunResumeActions` uses `run.resume_command` (showing `agy-guard --conversation ...` for `agy` runs rather than `omp --resume`).

- [ ] **Step 2: Run Vitest to verify failure**

Run: `cd web && npm test -- --run`
Expected: FAIL.

- [ ] **Step 3: Implement `<HarnessSelect>`, `useConfig`, Launch / MessageLaunch / ProdtestLaunch forms, and `RunActions` / `format.ts`**

- Add `ConfigInfo` + `api.config()` + `useConfig()`.
- Build `HarnessSelect.tsx` (`allowDefault`, `size`, availability labels from `useConfig()`).
- Add `harness` field to `LaunchForm`, `LaunchPrefs`, `StartFormFields` (shared with `CandidatesCard` StartDialog), `Launch.tsx` (`review`, `sync`, `adhoc`, plus dynamic warning banner), `MessageLaunch.tsx` (all 5 tabs), and `ProdtestLaunch.tsx`.
- Update `RunActions.tsx` & `format.ts` to prefer `run.resume_command` (or harness-aware `resumeCommand`).

- [ ] **Step 4: Run Vitest to verify pass**

Run: `cd web && npm test -- --run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/
git commit -m "[113] Add Web UI HarnessSelect to Launch, MessageLaunch, ProdtestLaunch, and RunActions"
```

---

### Task 6: Web UI Actions, Run Flow Modals/Cards, Release Fix-Default, Worktree Terminal, & Settings

**Files:**
- Modify: `web/src/lib/messageRunConfig.ts`
- Modify: `web/src/components/ActionForm.tsx`, `web/src/components/ImmediateRunForm.tsx`
- Modify: `web/src/components/ChainEditor.tsx`, `web/src/components/FlowWizard.tsx`
- Modify: `web/src/components/ReleaseCard.tsx`, `web/src/components/WorktreeDetail.tsx`
- Modify: `web/src/pages/Settings.tsx`
- Test: `web/src/lib/messageRunConfig.test.ts`, `web/src/components/ChainEditor.test.tsx`, `web/src/components/FlowWizard.test.tsx`

**Interfaces:**
- Consumes: `<HarnessSelect>`, `useConfig()`, `POST /api/worktrees/{ref}/terminal`, `workagent config set default-harness`.
- Produces: Harness chooser on `ActionFormCard` (`default_options.harness`), `ImmediateRunForm`/`ImmediateRunDialog` (both `kind="action"` and `kind="flow"`), `ChainEditor` (`StepConfigForm` `config.harness`), `FlowWizard` (top-level `--harness` override), `ReleaseCard` (`FixDefaultDialog` `--harness`), `WorktreeDetail` (bare terminal `Harness` selector), and `Settings` (`Default AI Harness` card).

- [ ] **Step 1: Write failing Vitest tests**

- `messageRunConfig.test.ts`: `buildActionUpsertArgs` preserves `default_options.harness`; `buildImmediateActionArgs`, `buildImmediateMessageActionArgs`, `buildImmediateWorktreeFlowArgs`, `buildImmediateFlowArgs` emit `["--harness", h]` when provided.
- `ChainEditor.test.tsx`: `StepConfigForm` renders Harness select on `start`/`review`/`fix_comments`/`sync`/`action`/`prodtest_plan` (and NOT on `cleanup`/`release`), updating `step.config.harness` and respecting template `hints`.
- `FlowWizard.test.tsx`: renders top-level Harness selector and forwards `["--harness", h]` into `flow run` args.

- [ ] **Step 2: Run Vitest to verify failure**

Run: `cd web && npm test -- --run`
Expected: FAIL.

- [ ] **Step 3: Implement all remaining Web UI surfaces**

- `ActionForm.tsx` + `messageRunConfig.ts`: add `harness` to `ActionUpsertForm` (`default_options.harness`) and read `actionHarnessOf(row)`.
- `ImmediateRunForm.tsx` + `messageRunConfig.ts`: add `<HarnessSelect allowDefault>` for BOTH `kind="action"` and `kind="flow"`.
- `ChainEditor.tsx`: add `<HarnessSelect allowDefault>` in `StepConfigForm` for AI-executing step kinds.
- `FlowWizard.tsx`: add top-level `<HarnessSelect allowDefault>` passing `["--harness", harness]` to `flow run` (alongside per-step selectors in `ChainEditor`).
- `ReleaseCard.tsx`: add `<HarnessSelect allowDefault>` to `FixDefaultDialog`, passing `["--harness", harness]`.
- `WorktreeDetail.tsx`: add compact `<HarnessSelect>` alongside the `Harness` terminal button, sending `{mode: "harness", harness}`.
- `Settings.tsx`: add `Default AI Harness` card backed by `useConfig()` + `config set default-harness <name>`.

- [ ] **Step 4: Run Vitest & frontend build**

Run: `cd web && npm test -- --run && npm run build`
Expected: PASS and clean TypeScript + Vite build (`web/dist` updated).

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "[113] Add Harness selector to Actions, Flow modals/cards, Release, WorktreeDetail, and Settings"
```

---

### Task 7: Documentation, Parity Audit, Full Test Suite, & Install

**Files:**
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/FEATURES-NOTES.md`, `CHANGELOG.md`, `AGENTS.md` (if needed for `agy`/`config` docs)

- [ ] **Step 1: Update `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/FEATURES-NOTES.md`, and `CHANGELOG.md`**

- Document `GET /api/config`, `POST /api/worktrees/{ref}/terminal` `harness` param, `Run.harness_name` & `Run.resume_command`, `config show|set`, updated `sync --harness <name>`, `release fix-default --harness <name>`, `flow run --harness <name>`, `agy` (`agy-guard`) headless/interactive/resume behavior.
- Add one-liner to `CHANGELOG.md` (`[113]`).

- [ ] **Step 2: Run full backend + frontend verification**

Run:
```bash
uv run pytest -v
cd web && npm test -- --run && npm run build
```
Expected: ALL backend and frontend tests pass with 0 failures.

- [ ] **Step 3: Re-install locally & verify `workagent doctor`**

Run:
```bash
./install.sh
workagent doctor
```
Expected: `doctor: ok` (`agy-guard: found`, `omp: found` if installed).

- [ ] **Step 4: Commit & push**

```bash
git add web/API_CONTRACT.md web/FEATURE_INVENTORY.md web/FEATURES-NOTES.md CHANGELOG.md
git commit -m "[113] Update API contract, feature inventory, and changelog"
```
