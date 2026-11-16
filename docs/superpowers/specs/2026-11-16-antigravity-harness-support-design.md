# Add Antigravity AI Harness Support — Design Spec

> Issue: [#113](https://github.com/ynsr/workagent/issues/113) — Add Antigravity AI Harness Support.
> Mode: plan only (no implementation). Branch: `feat/113--add-antigravity-ai-harness-support`.
> Decisions (user-approved 2026-11-16):
> - Canonical harness name: **`agy`**, always invoked via **`agy-guard`** (never raw `agy`).
> - Switch `DEFAULT_HARNESS` from `"omp"` to **`"agy"`**.
> - Transcripts & resume: store `conversation_id` in run metadata + mirror/write a normalized `.jsonl` transcript under `~/.config/workagent/runs/agy/…` so GC, validation, and watch work uniformly; resume with `agy-guard --conversation <id>`.
> - Bare judge / `suggest-icon` flags: `--model gemini-3.8-flash-low --disable-slash-commands`.
> - Headless flags: `agy-guard --dangerously-skip-permissions --output-format stream-json [extra] -p=<prompt>`; Interactive/Preview: `agy-guard [extra] -i=<prompt>` (or bare `agy-guard` when no prompt).
> - `sync --harness` becomes `Optional[str]` (breaking the legacy boolean `--harness` syntax) for parity with every other command.
> - Settings persistence: `workagent config show` / `workagent config set default-harness <name>` CLI + `GET /api/config` so the Settings page persists `default_harness` in `config.json`.
> - Web UI Harness chooser: returned/added everywhere (Launch tabs, Candidates StartDialog, MessageLaunch, ProdtestLaunch, ActionFormCard, ImmediateRunForm/Dialog, ChainEditor step config, FlowWizard top-level override, ReleaseCard FixDefaultDialog, WorktreeDetail bare terminal button, Settings default harness).

## 1. Problem

1. **Single-harness lock-in:** Workagent currently supports only `omp`. While `backend.py` defines a `Harness` class and `OmpHarness`, `omp`-specific CLI flags (`--session-dir`, `--resume`, `-p --auto-approve`), `omp`-specific JSONL transcript parsing (`{message: {role, content}}`), and literal `"omp"` strings leak throughout `backend.Harness`, `cli_harness.py`, `cli_sync.py`, `cli_flow_steps.py`, `cli_flow_util.py`, `cli_validate.py`, `suggest_icon.py`, `web_args.py`, `web_runs.py`, `web_runs_routes.py`, `doctor.py`, and the Web UI (`Launch.tsx`, `RunActions.tsx`, `format.ts`).
2. **No Antigravity (`agy`) harness:** Users cannot run `workagent` tasks through Antigravity CLI (`agy-guard`), which has fundamentally different invocation and session semantics (`-p=<prompt>` string flag, `--dangerously-skip-permissions`, `--output-format stream-json`, `-i=<prompt>` interactive prompt, and `--conversation <uuid>` persistence under `~/.gemini/antigravity-cli/` rather than `--session-dir`).
3. **Missing Harness chooser in Web UI:** Web UI forms and modals omit `--harness` selection, preventing users from choosing between `agy` and `omp` per run, per action template, per flow step, per flow run, or as the global default.

## 2. Goals & Non-Goals

### Goals
- Introduce `AgyHarness(Harness)` registered under `"agy"`, strictly invoking `agy-guard` (fail-closed proxy launcher) in every mode (headless, interactive, terminal, bare terminal, resume, judge, `suggest-icon`).
- Refactor `Harness` in `src/workagent/backend.py` into a true abstract base class (`abc.ABC`) and remove **every** direct `omp` call, `omp`-specific flag, and hardcoded `"omp"` fallback outside `OmpHarness`.
- Switch `DEFAULT_HARNESS` to `"agy"` while preserving `"omp"` as a first-class selectable harness.
- Support `agy` headless output streaming, normalized transcript mirroring under `runs/agy/`, validation (`cli_validate` + `harness_summary`), interactive/terminal `conversation_id` discovery, and `agy-guard --conversation <id>` resume.
- Add `workagent config show` / `workagent config set default-harness <name>` and `GET /api/config` for runtime/UI configuration.
- Return/add the Harness choosing input across all Web UI action forms, Launch pages, Run Flow modals/cards, Action templates, Release Fix-Default, Worktree bare terminal launch, and Settings.

### Non-Goals
- Modifying Antigravity CLI (`agy` / `agy-guard`) itself.
- Migrating historical `omp` transcripts or rewriting old `runs` rows.
- Removing `OmpHarness` — `omp` remains fully supported.

---

## 3. Architecture & Backend Design

### 3.1 `Harness` Abstract Interface (`src/workagent/backend.py`)

Convert `Harness` to `abc.ABC` with zero `omp`-specific behavior in the base class:

| Member | Kind | Responsibility |
|---|---|---|
| `name: str` | class attr | Registry key (`"agy"`, `"omp"`). |
| `binary: str` | class attr | Executable name (`"agy-guard"`, `"omp"`). |
| `judge_argv: list[str]` | class attr | Argv flags for bare judge / `suggest-icon` runs. |
| `command_argv(prompt, no_tty, extra_args=None)` | `@abstractmethod` | Full argv list (`[binary, ...]`) for launch and preview. |
| `launch(prompt, workdir, no_tty, extra_args=None, env_file=None, timeout_s=None, session_file=None, on_conversation_id=None)` | `@abstractmethod` | Execute headless (`no_tty=True`, returning exit code and handling `timeout_s`) or TTY (`os.execvp`). |
| `session_file_flag(path, metadata=None)` | `@abstractmethod` | Argv fragment routing transcript/session continuity (`--resume`/`--session-dir` for `omp`; `--conversation <id>` for `agy`). |
| `resume_argv(session_file, metadata=None)` | `@abstractmethod` | Argv list for resuming (or starting fresh when unready) in a terminal / copyable shell command. |
| `bare_terminal_argv(scope_dir="")` | `@abstractmethod` | Argv list for launching a bare interactive terminal with no initial prompt. |
| `watch_transcript(scope_dir, before, run_id, db_path, result, stop_event, ...)` | `@abstractmethod` (or default + override) | Background/post-launch discovery of newly created transcript/conversation ID, updating `runs.file_path` and `runs.metadata`. |
| `extract_last_assistant_text(transcript_path, max_chars=4000)` | method | Parse the harness's transcript format and return the last assistant message. |
| `tool_error_rate(transcript_path, since_line=0)` | method | Score `(errors, total)` over tool results in the transcript. |

Shared process-execution helpers (e.g. `shutil.which` binary check, `Popen` + `start_new_session` + `SIGTERM`/`SIGKILL` timeout handling, `cd_worktree` + `os.execvp`) live as shared helpers in `backend.py` so `OmpHarness` and `AgyHarness` do not duplicate subprocess/timeout plumbing.

Update `HARNESSES`:
```python
HARNESSES: dict[str, Harness] = {
    "agy": AgyHarness(),
    "omp": OmpHarness(),
}
```
Update `get_harness` error message to remove `"v1 supports:"` and list `sorted(HARNESSES)`.

### 3.2 `OmpHarness` (`src/workagent/backend.py`)

Moves all `omp`-specific behavior verbatim into `OmpHarness`:
- `name = "omp"`, `binary = "omp"`.
- `judge_argv = ["--smol", "--no-skills", "--no-tools", "--no-rules", "--no-context-files", "--no-lsp", "--no-extensions", "--no-pty"]`.
- `command_argv(prompt, no_tty, extra_args=None)`:
  - `no_tty=True`: `["omp", "-p", "--auto-approve", *(extra_args or []), prompt]`
  - `no_tty=False`: `["omp", *(extra_args or []), *([prompt] if prompt else [])]`
- `session_file_flag(path, metadata=None)`: existing non-empty file → `["--resume", path]`, else `["--session-dir", str(Path(path).parent)]` (when `path` is non-empty).
- `resume_argv(session_file, metadata=None)`: non-empty file → `["omp", "--resume", session_file]`, else `["omp", "--session-dir", str(Path(session_file).parent)]`.
- `bare_terminal_argv(scope_dir="")`: `["omp", "--session-dir", scope_dir]` if `scope_dir` else `["omp"]`.
- `extract_last_assistant_text` / `tool_error_rate`: delegates to `harness_summary` `omp` JSONL parser.

### 3.3 `AgyHarness` (`src/workagent/backend.py` + transcript/watcher helpers)

Verified against `agy-guard --help`, `agy-guard --gh`, and live `agy-guard -p=... --output-format stream-json`:

- `name = "agy"`, `binary = "agy-guard"`.
- `judge_argv = ["--model", "gemini-3.8-flash-low", "--disable-slash-commands"]`.
- **Argv construction (`command_argv`)**:
  - **Important `agy` CLI quirk (verified):** `-p`/`--print` and `-i`/`--prompt-interactive` are string-valued flags; passing `-p --output-format ...` causes `-p` to consume `--output-format` as the prompt. Therefore, `AgyHarness` **must** attach the prompt using `=` (`f"-p={prompt}"`, `f"-i={prompt}"`) and place all flags before the prompt flag:
  - `no_tty=True`:
    `["agy-guard", "--dangerously-skip-permissions", "--output-format", "stream-json", *(extra_args or []), f"-p={prompt}"]`
  - `no_tty=False`:
    `["agy-guard", *(extra_args or []), *([f"-i={prompt}"] if prompt else [])]`
- **Session continuity (`session_file_flag` & `resume_argv`)**:
  - `agy` has no `--session-dir`; it resumes via `--conversation <conversation_id>`.
  - Helper `_resolve_agy_conversation_id(path, metadata=None)` checks:
    1. `metadata.get("conversation_id")` if provided;
    2. First line(s) of `path` (if `path` exists) for `{"type": "agy_meta", "conversation_id": "..."}` or `"conversation_id"` in any JSONL line;
    3. `store_sqlite.run_by_file_path` metadata fallback when needed.
  - `session_file_flag(path, metadata=None)`: returns `["--conversation", conv_id]` when `conv_id` is non-empty, else `[]`.
  - `resume_argv(session_file, metadata=None)`: returns `["agy-guard", "--conversation", conv_id]` when `conv_id` is non-empty, else `["agy-guard"]`.
  - `bare_terminal_argv(scope_dir="")`: returns `["agy-guard"]`.
- **Headless execution & normalized transcript mirroring (`AgyHarness.launch(no_tty=True)`)**:
  - Launches `agy-guard` with `stdout=subprocess.PIPE`, `text=True`, `bufsize=1` (and process group `start_new_session=True` when `timeout_s` is set).
  - Streams NDJSON lines from `stdout`:
    - Parses each line as JSON (`init`, `step_update`, `result`; non-JSON lines are forwarded to stderr/captured).
    - On `"init"`: extracts `conversation_id`, calls `on_conversation_id(conversation_id)` (persisting `{"conversation_id": conversation_id}` on the `runs` row metadata immediately), and writes the `{"type": "agy_meta", "conversation_id": conversation_id}` header to `session_file` (when `session_file` is provided).
    - Appends each structured event (and/or normalized `step_update` / `result` event) into `session_file` as JSONL, flushing incrementally so live watchers see updates immediately.
    - Prints human-readable agent response text (from `result.response` or streamed `step_update.text_delta`) to stdout/log so web run logs and CLI stdout remain informative.
    - On `TimeoutExpired`: kills the entire process group (`SIGTERM` → 10s → `SIGKILL`) and raises `HarnessTimeout`.
    - Returns `proc.returncode` (or `1` if `result.status` is non-success on exit 0).
- **Interactive / Terminal `conversation_id` discovery**:
  - `agy` writes conversations into `~/.gemini/antigravity-cli/brain/<conversation_id>/.system_generated/logs/transcript.jsonl` (verified structure: `{"step_index": N, "source": "...", "type": "USER_INPUT"|"PLANNER_RESPONSE"|..., "status": "DONE", "content": "..."}`).
  - Before launching an interactive (`watch-and-exec`) or terminal (`_open_terminal_watch` / `--terminal`) session, `AgyHarness` snapshots existing `<uuid>` dirs under `~/.gemini/antigravity-cli/brain/` (respecting `ANTIGRAVITY_HOME` / `~/.gemini/antigravity-cli`).
  - The watcher thread polls `~/.gemini/antigravity-cli/brain/` for a new `<conversation_id>` dir (or updated `.system_generated/logs/transcript.jsonl` newer than launch timestamp whose initial workspace/cwd matches when discernible), writes the normalized transcript (with `agy_meta` header + `conversation_id`) into the target `runs/agy/<branch>/<action>/<id>.jsonl` file, and updates `runs.file_path` + `runs.metadata.conversation_id` in SQLite.
  - Because `runs/agy/...jsonl` lives inside `~/.config/workagent/runs/`, all existing transcript GC (`cli_cleanup_gc.py`), `runs` deletion, and `transcript_ready` checks work identically without ever touching `~/.gemini/antigravity-cli/`!
- **Transcript parsing (`harness_summary.py` & `AgyHarness`)**:
  - `harness_summary.last_assistant_text` and `tool_error_rate` support both `omp` format (`{"message": {"role": "assistant"|"toolResult", ...}}`) and `agy` normalized / native formats:
    - `stream-json` result/step_update: `event == "result"` (`result.response`) or `event == "step_update"` (`step_update.step_type == "agent_response"`, `text_delta`);
    - `agy` native `transcript.jsonl` lines: `type == "PLANNER_RESPONSE"` (`content`), and tool call/result error status (`status == "ERROR"` or error payload).
  - `cli_validate.py` works out-of-the-box for `agy` headless validation loops (Phase-1 dirty gate, Phase-2 deterministic gates + `tool_error_rate`, LLM judge via `AgyHarness.judge_argv`).

### 3.4 Audit & Removal of All `omp` Calls Outside `OmpHarness`

Every direct `omp` invocation, hardcoded `"omp"` default, or `omp`-only flag outside `OmpHarness` must be refactored:

| Module | Current Leak | Refactor |
|---|---|---|
| `backend.py` | `Harness.session_file_flag` implements `omp` `--resume`/`--session-dir`; `launch()` docstrings & shim comments reference `omp`. | Abstract `Harness` methods; `OmpHarness` owns `--resume`/`--session-dir`. |
| `store.py` | `DEFAULT_HARNESS = "omp"`, `SUPPORTED_HARNESSES = ("omp",)`. | `DEFAULT_HARNESS = "agy"`, `SUPPORTED_HARNESSES = ("agy", "omp")` (or sourced from `backend.HARNESSES`), helper `default_harness()`. |
| `cli_harness.py` | `_launch_in_worktree` uses `"omp"` fallback; `_run_harness` assumes `newest_transcript(scope_dir)` and `--session-dir` minting; `watch-and-exec` polls `scope_dir` directly; `run_judge` & `_publish_headless_summary` call `harness_summary` directly without harness context. | Use `store.default_harness()`; pass `session_file` to `harness.launch(...)` and `run_id` metadata callback; `watch-and-exec` accepts `--harness` and delegates discovery to `harness.watch_transcript(...)`; delegate text extraction through `harness.extract_last_assistant_text(...)`. |
| `suggest_icon.py` | `harness: str = "omp"` default; assumes `newest_transcript(Path(directory))` minting via `--session-dir`. | Default `harness: str | None = None` → `store.default_harness()`; pass `session_file=judge_path` to `backend.launch(...)` and extract text via `harness_obj.extract_last_assistant_text(...)` (supporting both `omp` minted file in `directory` and `agy` written `judge_path`). |
| `cli_sync.py` | `_handle_merge_conflict` hardcodes `_cli._run_harness("omp", ...)` twice; `--harness` is `bool`. | `--harness: Optional[str] = typer.Option(None, ...)` (validated via `backend.get_harness`); when `--harness <name>` is given, auto-launches that harness on conflict; when `--yes` is given without `--harness`, uses `store.default_harness()`. |
| `cli_release.py` & `cli_release_fix.py` | `fix_default` has no `--harness` option; hardcodes `store.load_config().get("default_harness", "omp")`. | Add `--harness: Optional[str]` to `release fix-default` and pass through to `run_fix_default` using `harness or store.default_harness()`. |
| `cli_flow.py`, `cli_flow_util.py`, `cli_flow_steps.py` | Multiple `"omp"` defaults; `SYNC_FLAGS` has `("harness", "--harness", "bool")`; `flow run` & `message flow run` lack top-level `--harness` override. | Replace all `"omp"` defaults with `store.default_harness()`; `SYNC_FLAGS` uses `"str"`; add `--harness: Optional[str]` to `flow run` and `message flow run` (propagating as fallback/override into step execution, with per-step `config["harness"]` taking precedence or top-level `--harness` overriding when explicitly passed — specifically: `step_harness = explicit_cli_harness or step_config.get("harness") or default_harness()`). |
| `cli_start.py`, `cli_review.py`, `cli_adhoc.py`, `cli_action.py`, `cli_message.py`, `cli_prodtest_plan.py`, `cli_prodtest_run.py`, `cli_prodtest_checks.py` | Hardcoded `"omp"` fallback strings and `--harness` / `--session-file` help text mentioning `omp` (`(v1: omp)` / `omp --resume`). | Use `store.default_harness()`; update help texts to be harness-agnostic (`(default: configured)` / `Transcript path: resumed when existing, otherwise scopes a fresh session`); forward `--harness` in `review --all` child spawns. |
| `doctor.py` | `tools` checks `("git-wt", "omp", "gh", "glab", "jira")`. | Check `("git-wt", "agy-guard", "omp", "gh", "glab", "jira")`. |
| `web_args.py` | `BOOL_FLAGS["sync"]` has `--harness`; `VAL_FLAGS` lacks `--harness` on `sync`, `release`, `flow run`, `message flow run`; `_session_file_for` hardcodes `"omp"`. | Move `--harness` to `VAL_FLAGS["sync"]`; add `--harness` to `VAL_FLAGS` for `release`, `flow run`, `message flow run`; `_session_file_for` extracts `--harness` from `args` or falls back to `store.default_harness()`; add `config show` / `config set` specs. |
| `web_runs.py` | `_resume_shell_command` hardcodes `omp --session-dir` and `omp --resume`. | Accept `harness_name` (and optional `metadata`/`conversation_id`), delegate to `backend.get_harness(harness_name).resume_argv(session_file, metadata)`! Also expose `harness_name` and `resume_command` on serialized `Run` / `RunDetail` API payloads. |
| `web_runs_routes.py` | `_claim_terminal_lock` hardcodes `"omp"`; `_open_terminal_watch` hardcodes `f"{harness_name} --session-dir ..."`; terminal route hardcodes `default_harness` with no body override; `_resolve_session_file` comments/logic assume `omp`. | `_claim_terminal_lock` takes `harness_name`; `_open_terminal_watch` calls `harness.bare_terminal_argv(scope)` and uses harness-aware watch; `POST /api/worktrees/{ref}/terminal` accepts optional `harness` in JSON body (`validated against backend.HARNESSES`); `_resolve_session_file` resolves `harness_name` from the run row and delegates transcript readiness/discovery cleanly. |

### 3.5 Config CLI & API (`config show` / `config set` + `GET /api/config`)

- **CLI (`src/workagent/cli_config.py` mounted as `app.add_typer(config_app, name="config")`)**:
  - `workagent config show [--json]`: prints `default_harness` (and `supported_harnesses`, `repos` count, etc.).
  - `workagent config set <key> <value> [--json]`: supports `default-harness` / `default_harness` (validated against `backend.HARNESSES`; exit 2 on unsupported harness or unknown key). Persists via `store.load_config` + `store.save_config`.
- **Web API (`webapp.py`)**:
  - `GET /api/config`: returns:
    ```json
    {
      "default_harness": "agy",
      "supported_harnesses": ["agy", "omp"],
      "harnesses": [
        {"name": "agy", "label": "Antigravity (agy-guard)", "binary": "agy-guard", "available": true},
        {"name": "omp", "label": "OMP (omp)", "binary": "omp", "available": true}
      ]
    }
    ```
  - Settings page updates `default_harness` via `POST /api/runs` (`{"command": "config", "args": ["set", "default-harness", value], "confirm": false}`), preserving the single-source-of-truth CLI-as-backend pattern (`SPECS["config"]`, `confirm: false` because it is non-destructive config).

---

## 4. Web UI Design — Returning Harness Selector Everywhere

### 4.1 Shared Component & Hook

- **`useConfig()` hook (`web/src/lib/queries.ts` + `api.config()` in `web/src/lib/api.ts`)**: fetches `GET /api/config` (cached, invalidated after `config set` runs).
- **`<HarnessSelect>` component (`web/src/components/HarnessSelect.tsx`)**:
  - Renders a clean `<select>` (matching `ChainEditor` / `ImmediateRunForm` shadcn/Tailwind styling) or `SegmentedControl` with `<FieldHelp label="Harness" flag="--harness" ... />`.
  - Props: `idPrefix`, `value: string` (`""` means default/inherit), `onChange: (v: string) => void`, `allowDefault?: boolean`, `defaultLabel?: string` (e.g. `"Default (agy)"`), `size?: "default" | "sm"`.
  - Options come from `useConfig()`: shows `"agy"` and `"omp"` (with `(missing)` hint when `available === false` so users immediately know if a binary isn't on PATH).
- **Preferences (`web/src/lib/launchPrefs.ts`)**:
  - Store `harness` per Launch tab (`start`, `review`, `sync`, `adhoc`, `prodtest`) as `""` (configured default) or explicit `"agy"` / `"omp"`.

### 4.2 Complete Surface Inventory

| Surface | File(s) | Behavior |
|---|---|---|
| **Launch — Start tab & Candidates StartDialog** | `StartForm.tsx`, `launchConfig.ts`, `CandidatesCard.tsx` | `<HarnessSelect>` in `StartFormFields`; `buildStartArgs` appends `["--harness", form.harness]` when non-empty; `startFlagList` displays `--harness <h>`. |
| **Launch — Review / Fix comments tab** | `Launch.tsx`, `StartForm.tsx`, `launchConfig.ts` | `<HarnessSelect>` in Review mode; `buildReviewArgs` appends `["--harness", form.harness]` when non-empty. |
| **Launch — Sync tab** | `Launch.tsx`, `launchConfig.ts` | `<HarnessSelect allowDefault>` (`"Default"` = use `default_harness` on conflict; explicit `"agy"` / `"omp"` passes `["--harness", form.harness]`). |
| **Launch — Adhoc Prompt tab** | `Launch.tsx`, `launchConfig.ts` | `<HarnessSelect>` in Adhoc mode; `buildArgs` appends `["--harness", form.harness]` when non-empty. |
| **Launch — Dynamic Warning Banner** | `Launch.tsx` | Resolve effective harness (`form.harness || config.default_harness`); show `agy-guard -p=… --dangerously-skip-permissions` when `agy`, or `omp -p --auto-approve` when `omp` (never hardcoded `omp`). |
| **Message Launch (all 5 tabs)** | `MessageLaunch.tsx`, `messageLaunchConfig.ts` | `<HarnessSelect>` on `adhoc`, `validate`, `draft-reply`, `action`, `flow`; `buildMessageArgs` appends `["--harness", form.harness]` when non-empty. |
| **Prodtest Launch (`plan` & `run`)** | `ProdtestLaunch.tsx` | `<HarnessSelect>` alongside `RunModeSelect` / `ProdtestActionSelect`; `buildArgs` appends `["--harness", form.harness]` when non-empty. |
| **Actions Page — Add/Update Form** | `ActionForm.tsx`, `messageRunConfig.ts` | `<HarnessSelect allowDefault>` setting `default_options.harness` (omitted when `"Default"`, persisted when `"agy"` / `"omp"`). |
| **Immediate Run Form & Modal (Actions & Flows)** | `ImmediateRunForm.tsx`, `messageRunConfig.ts`, `Actions.tsx`, `FlowTemplates.tsx`, `ActionsMenu.tsx`, `StatusActions.tsx`, `MessageRowActions.tsx` | `<HarnessSelect allowDefault>` shown for BOTH `kind="action"` and `kind="flow"`! Appends `["--harness", harness]` to `buildImmediateActionArgs`, `buildImmediateMessageActionArgs`, `buildImmediateWorktreeFlowArgs`, `buildImmediateFlowArgs`, and `buildMessageArgs` when non-empty. |
| **Flow Template Form & ChainEditor** | `ChainEditor.tsx`, `FlowTemplateForm.tsx` | `<HarnessSelect allowDefault>` in `StepConfigForm` for `start`, `review`, `fix_comments`, `sync`, `action`, `prodtest_plan` steps (hidden on `cleanup`, `release`). Persisted in `step.config.harness` (delta-aware when `hints` present). |
| **Run Flow Modal (`FlowWizard`)** | `FlowWizard.tsx` | Top-level `<HarnessSelect allowDefault>` (`Flow harness override`) in the modal header/controls, passing `["--harness", harness]` to `flow run`, PLUS per-step `<HarnessSelect>` inside `ChainEditor`! |
| **Release Card — Fix Default Dialog** | `ReleaseCard.tsx` | `<HarnessSelect>` in `FixDefaultDialog`; appends `["--harness", harness]` when non-empty. |
| **Worktree Detail — Bare Terminal Harness Button** | `WorktreeDetail.tsx` | Compact `<HarnessSelect>` next to the `Harness` terminal button (or default-seeded selector), passing `{mode: "harness", harness}` in `POST /api/worktrees/{ref}/terminal`. |
| **Runs Table & RunDetail Copy/Resume** | `RunActions.tsx`, `format.ts`, `api.ts` | `Run` / `RunDetail` API returns `harness_name` and `resume_command`; `RunResumeActions` uses `run.resume_command` (falling back to harness-aware `resumeCommand`) instead of hardcoded `omp --resume`. |
| **Settings Page** | `Settings.tsx` | New `Default AI Harness` card using `useConfig()` and running `workagent config set default-harness <name>` on change. |

---

## 5. Testing, Parity & Rollout

- **Parity enforcement (`tests/test_webapp_data.py`)**: `test_specs_mirror_cli_flags` continues to pass against all updated `SPECS`, `BOOL_FLAGS`, and `VAL_FLAGS` (`sync`, `release`, `flow run`, `message flow run`, `config show`, `config set`).
- **Contract & inventory docs**: update `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/FEATURES-NOTES.md`.
- **Static/AST guard test (`tests/test_backend.py`)**: verify no direct `"omp"` subprocess/command string or hardcoded `"omp"` default exists anywhere under `src/workagent/` outside `OmpHarness` (and explicit registry/migration/docstrings where applicable).
- **`AgyHarness` unit tests**:
  - `command_argv` formatting (`-p=<prompt>` with `=` and flags first; `-i=<prompt>` for interactive; bare `agy-guard` when empty; never `agy`).
  - `launch(no_tty=True)` with mocked `agy-guard` NDJSON stream: captures `conversation_id`, writes normalized `.jsonl` transcript with `agy_meta`, extracts `last_assistant_text`, scores `tool_error_rate`, handles timeout process-group kill, handles non-zero / failed status.
  - `session_file_flag` & `resume_argv` returning `["--conversation", id]` vs `[]` / `["agy-guard"]`.
  - Interactive `watch_transcript` discovering newly minted `~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl` and mirroring to `runs/agy/...`.
- **CLI & Webapp tests**:
  - `config show` / `config set default-harness`, `GET /api/config`, `POST /api/worktrees/{ref}/terminal` with `harness`, `sync --harness <name>`, `release fix-default --harness <name>`, `flow run --harness <name>`, `doctor` checking `agy-guard`.
- **Frontend tests (Vitest + Testing Library)**:
  - `StartForm` / `Launch` (`agy` vs `omp` arg generation + dynamic warning banner), `FlowWizard` (top-level + step harness), `ChainEditor`, `ActionForm`, `ImmediateRunForm`, `Settings`, `RunActions`.
- **Risks & Mitigations**:
  1. *`agy` CLI `-p` flag argument parsing*: `-p` consumes the next token if not passed as `-p=<prompt>`. Mitigated by always using `f"-p={prompt}"` / `f"-i={prompt}"` and putting flags before it.
  2. *Interactive `agy` brain directory collision under concurrent launches*: Two simultaneous interactive launches could race on `~/.gemini/antigravity-cli/brain/`. Mitigated by snapshotting pre-existing UUID dirs before spawn and matching newest created dir + mtime >= spawn timestamp (and storing `conversation_id` immediately in headless mode from the `init` JSON event).
  3. *Breaking `sync --harness` boolean callers*: Any internal callers/tests passing `sync --harness` as a flag without a value must be updated to `--harness <name>` or `--yes`.
