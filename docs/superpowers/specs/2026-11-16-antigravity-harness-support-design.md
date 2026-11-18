# Add Antigravity AI Harness Support — Design Spec

> Issue: [#113](https://github.com/ynsr/workagent/issues/113) — Add Antigravity AI Harness Support.
> Mode: plan only (no implementation). Branch: `feat/113--add-antigravity-ai-harness-support`.
> Decisions (user-approved 2026-11-16):
> - **Architecture:** Thin `Harness(abc.ABC)` in `backend.py`, dedicated `agy_harness.py` for Antigravity, normalized `.jsonl` mirror under `~/.config/workagent/runs/agy/…`, and `OmpHarness` owning 100% of `omp` logic.
> - **Harness identity & default:** Canonical name **`"agy"`**, always invoked via **`agy-guard`** (never raw `agy`); `DEFAULT_HARNESS` switches from `"omp"` to **`"agy"`**.
> - **Invocation flags:** Headless `agy-guard --dangerously-skip-permissions --output-format stream-json [extra] -p=<prompt>`; Interactive/Preview `agy-guard [extra] -i=<prompt>` (bare `agy-guard` when no prompt); Judge/suggest-icon `judge_argv = ["--model", "gemini-3.8-flash-low", "--disable-slash-commands", "--mode", "plan"]` executed in `TemporaryDirectory`.
> - **Transcripts, continuity & resume:** Capture `conversation_id` via `stream-json` `init` (headless) or `~/.gemini/antigravity-cli/conversation_summaries.db` `workspace_uris` + `last_modified_time` query with `brain/` mtime fallback (interactive / `--terminal` / `POST /api/runs/{id}/start-terminal` via `watch-and-exec` / lazy `_resolve_session_file`). Mirror `brain/<id>/.system_generated/logs/transcript.jsonl` + `agy_meta` header into `runs/agy/…`, store `conversation_id` in run metadata, and resume with `agy-guard --conversation <id>`.
> - **CLI parity:** `sync --harness` becomes `Optional[str]` (breaking legacy bool flag); `release fix-default`, `flow run`, and `message flow run` add `--harness`; `flow run --harness` overrides per-step `config.harness` (`cli_harness or step_cfg.get("harness") or default_harness()`); `review --all` forwards `--harness` to child processes; `workagent config show` and `workagent config set default-harness <name>` persist `default_harness`.
> - **Web UI Harness chooser:** `SegmentedControl` (`[Default (<cfg>)] | [agy] | [omp]`, or `[agy] | [omp]` on Settings) on Launch tabs, Candidates StartDialog, MessageLaunch, ProdtestLaunch, ActionFormCard, ImmediateRunForm/Dialog (actions & flows), FlowWizard header, Dashboard bulk confirms (`Sync`/`Review`/`Fix PR comments`), FixDefaultDialog, and Settings; compact `<select>` in `ChainEditor` step configs and `WorktreeDetail` footer.

## 1. Problem

1. **Single-harness lock-in:** Workagent currently supports only `omp`. While `backend.py` declares `Harness` and `OmpHarness`, `omp`-specific CLI flags (`--session-dir`, `--resume`, `-p --auto-approve`), `omp`-specific JSONL transcript shapes (`{message: {role, content}}`), and literal `"omp"` strings leak throughout `backend.Harness`, `cli_harness.py`, `cli_sync.py`, `cli_flow_steps.py`, `cli_flow_util.py`, `cli_review.py`, `cli_validate.py`, `suggest_icon.py`, `web_args.py`, `web_runs.py`, `web_runs_routes.py`, `doctor.py`, and the Web UI (`Launch.tsx`, `RunActions.tsx`, `format.ts`).
2. **No Antigravity (`agy`) harness:** Users cannot execute Workagent workflows via Antigravity CLI (`agy-guard`), which differs materially from `omp`: `-p`/`-i` are string-valued flags requiring `=` attachment (`-p=<prompt>`, `-i=<prompt>`), headless structured output uses `--output-format stream-json`, and sessions are persisted by `conversation_id` under `~/.gemini/antigravity-cli/` (`conversation_summaries.db` + `brain/<id>/.system_generated/logs/transcript.jsonl`) and resumed via `--conversation <id>` rather than `--session-dir`.
3. **Missing Harness chooser in Web UI:** Web UI action forms, launch tabs, flow modals/cards, and dashboard bulk dialogs omit `--harness` selection, preventing users from choosing `agy` vs `omp` per run, per template/step, or as the global default.

## 2. Goals & Non-Goals

### Goals
- Implement `AgyHarness(Harness)` in `src/workagent/agy_harness.py`, strictly invoking `agy-guard` in every execution path (headless, interactive, terminal, bare terminal, resume, judge, `suggest-icon`).
- Convert `Harness` in `src/workagent/backend.py` into a strict `abc.ABC` and eliminate **every** direct `omp` call, `omp`-specific CLI flag, and hardcoded `"omp"` fallback outside `OmpHarness`.
- Switch `DEFAULT_HARNESS` to `"agy"` while keeping `"omp"` fully functional and selectable.
- Support `agy` headless streaming, normalized transcript mirroring under `~/.config/workagent/runs/agy/…`, validation (`cli_validate` + `harness_summary`), multi-worktree-safe `conversation_summaries.db` discovery (including Preview → `start-terminal` and manual copy-paste previews), and `agy-guard --conversation <id>` resume.
- Add `workagent config show` / `workagent config set default-harness <name>` and `GET /api/config`.
- Restore the Harness choosing input across all Web UI action forms, Launch pages, Run Flow modals/cards, Dashboard bulk confirms, Release Fix-Default, Worktree bare terminal launch, and Settings.

### Non-Goals
- Modifying Antigravity CLI (`agy` / `agy-guard`) itself.
- Migrating historical `omp` transcript files or rewriting historical `runs` rows.
- Supporting `agy-guard acp` daemon mode (stateless subprocess/exec parity stays the contract).

---

## 3. Architecture & Backend Design

### 3.1 `Harness` Abstract Interface (`src/workagent/backend.py`)

Convert `Harness` to `abc.ABC` with zero `omp`-specific logic in the base class:

| Member | Kind | Responsibility |
|---|---|---|
| `name: str` | class attr | Registry key (`"agy"`, `"omp"`). |
| `binary: str` | class attr | Executable name (`"agy-guard"`, `"omp"`). |
| `judge_argv: list[str]` | class attr | Argv flags for bare judge / `suggest-icon` runs. |
| `command_argv(prompt, no_tty, extra_args=None)` | `@abstractmethod` | Full argv list (`[binary, ...]`) for launch and preview. |
| `launch(prompt, workdir, no_tty, extra_args=None, env_file=None, timeout_s=None, session_file=None, on_conversation_id=None)` | `@abstractmethod` | Execute headless (`no_tty=True`, returning exit code and enforcing `timeout_s`) or TTY (`os.execvp`). |
| `session_file_flag(path, metadata=None)` | `@abstractmethod` | Argv fragment routing session continuity (`--resume`/`--session-dir` for `omp`; `--conversation <id>` for `agy`). |
| `resume_argv(session_file, metadata=None)` | `@abstractmethod` | Argv list for resuming (or starting fresh when unready) in a terminal or copyable command. |
| `bare_terminal_argv(scope_dir="")` | `@abstractmethod` | Argv list for launching a bare interactive terminal with no initial prompt. |
| `watch_transcript(workdir, scope_dir, before, run_id, db_path, result=None, session_file="", launched_at=0.0, stop_event=None)` | `@abstractmethod` | Watch/adopt the harness-minted transcript or conversation ID and persist `file_path` (+ `metadata`) on the `runs` row. |
| `extract_last_assistant_text(transcript_path, max_chars=4000)` | `@abstractmethod` | Extract the final assistant response from this harness's transcript. |
| `tool_error_rate(transcript_path, since_line=0)` | `@abstractmethod` | Score `(errors, total)` over tool/step executions in the transcript. |

Shared process utilities (`_check_binary`, `_run_blocking_with_timeout`, `_exec_tty`) live in `backend.py` so `OmpHarness` and `AgyHarness` share `PATH` checks, `env_file` overlay application, `start_new_session` + `SIGTERM`→`SIGKILL` timeout handling, and `cd_worktree` + `os.execvp` without duplicating subprocess plumbing.

Register both harnesses in `backend.HARNESSES`:
```python
HARNESSES: dict[str, Harness] = {
    "agy": AgyHarness(),
    "omp": OmpHarness(),
}
```
Update `get_harness(name)` error text to list `', '.join(sorted(HARNESSES))` without `"v1 supports:"`.

### 3.2 `OmpHarness` (`src/workagent/backend.py`)

Owns 100% of `omp`-specific code:
- `name = "omp"`, `binary = "omp"`.
- `judge_argv = ["--smol", "--no-skills", "--no-tools", "--no-rules", "--no-context-files", "--no-lsp", "--no-extensions", "--no-pty"]`.
- `command_argv(prompt, no_tty, extra_args=None)`:
  - `no_tty=True`: `["omp", "-p", "--auto-approve", *(extra_args or []), prompt]`
  - `no_tty=False`: `["omp", *(extra_args or []), *([prompt] if prompt else [])]`
- `session_file_flag(path, metadata=None)`: existing non-empty file → `["--resume", path]`, else `["--session-dir", str(Path(path).parent)]` (when `path` non-empty).
- `resume_argv(session_file, metadata=None)`: non-empty file → `["omp", "--resume", session_file]`, else `["omp", "--session-dir", str(Path(session_file).parent)]`.
- `bare_terminal_argv(scope_dir="")`: `["omp", "--session-dir", scope_dir]` if `scope_dir` else `["omp"]`.
- `watch_transcript(...)`: polls `scope_dir` (`store_sqlite.newest_transcript`) for a new `.jsonl` file != `before` and calls `set_run_file_path`.
- `extract_last_assistant_text` / `tool_error_rate`: delegates to `harness_summary`'s `omp` JSONL functions.

### 3.3 `AgyHarness` (`src/workagent/agy_harness.py`)

Verified against `agy-guard --help`, `agy-guard --gh`, `agy-guard -p=... --output-format stream-json`, and `~/.gemini/antigravity-cli/`:

- `name = "agy"`, `binary = "agy-guard"` (never invoke `agy` directly).
- `judge_argv = ["--model", "gemini-3.8-flash-low", "--disable-slash-commands", "--mode", "plan"]`.
- **Argv construction (`command_argv`):**
  Because `agy` treats `-p`/`--print` and `-i`/`--prompt-interactive` as string-valued options (passing `-p --output-format ...` consumes `--output-format` as the prompt), `AgyHarness` always places flags first and attaches the prompt with `=`:
  - `no_tty=True`:
    `["agy-guard", "--dangerously-skip-permissions", "--output-format", "stream-json", *(extra_args or []), f"-p={prompt}"]`
  - `no_tty=False`:
    `["agy-guard", *(extra_args or []), *([f"-i={prompt}"] if prompt else [])]`
- **Session continuity (`session_file_flag` & `resume_argv`):**
  - Helper `resolve_conversation_id(path, metadata=None)` checks:
    1. `metadata.get("conversation_id")` if supplied;
    2. `{"type": "agy_meta", "conversation_id": "..."}` (or any `"conversation_id"` line) inside `path` when present;
    3. `store_sqlite.run_by_file_path` metadata fallback when needed.
  - `session_file_flag(path, metadata=None)`: returns `["--conversation", cid]` when `cid` is non-empty, else `[]`.
  - `resume_argv(session_file, metadata=None)`: returns `["agy-guard", "--conversation", cid]` when `cid` resolves, else `["agy-guard"]`.
  - `bare_terminal_argv(scope_dir="")`: returns `["agy-guard"]`.
- **Headless execution (`launch(no_tty=True)`) & transcript mirroring:**
  - Spawns `agy-guard` with `stdout=subprocess.PIPE`, `text=True`, `bufsize=1` (in `start_new_session=True` when `timeout_s` is set).
  - Streams NDJSON events line-by-line:
    - On `"init"`: extracts `conversation_id`, calls `on_conversation_id(cid)` immediately (persisting `{"conversation_id": cid}` into `runs.metadata`), and creates/writes `{"type": "agy_meta", "conversation_id": cid}` into `session_file` under `~/.config/workagent/runs/agy/…`.
    - Streams `step_update` and `result` lines into `session_file` and echoes human-readable assistant output to stdout/log.
    - On process exit, if `~/.gemini/antigravity-cli/brain/<cid>/.system_generated/logs/transcript.jsonl` exists, mirrors its `PLANNER_RESPONSE` / `GENERIC` (`DONE`/`ERROR`) records into `session_file` (preserving the `agy_meta` header) so validation sees authoritative step outcomes and assistant text.
    - Returns non-zero on non-zero exit code OR when `result.status` is non-success; kills the process group (`SIGTERM` → 10s → `SIGKILL`) and raises `HarnessTimeout` on timeout.
- **Interactive, Terminal, Preview-`start-terminal`, and Lazy Preview Discovery:**
  - Antigravity home resolves from `ANTIGRAVITY_HOME` (default `~/.gemini/antigravity-cli`), containing:
    - `conversation_summaries.db` (`conversation_id`, `workspace_uris` JSON array e.g. `["file:///path/to/worktree"]`, `last_modified_time`), and
    - `brain/<conversation_id>/.system_generated/logs/transcript.jsonl`.
  - `watch_transcript` snapshots pre-existing `conversation_id`s for that `workdir` before launch, then polls `conversation_summaries.db` (read-only `?mode=ro`) for a new/updated row whose `workspace_uris` contains `"file://" + Path(workdir).resolve().as_posix()` and `last_modified_time >= launched_at` (with `brain/` directory mtime fallback when `conversation_summaries.db` is unavailable).
  - When found, `watch_transcript` mirrors `brain/<cid>/.system_generated/logs/transcript.jsonl` (prepended with `{"type": "agy_meta", "conversation_id": cid}`) to `session_file`, updates `runs.file_path` via `set_run_file_path`, and stores `{"conversation_id": cid}` via `set_run_metadata`.
  - **All interactive/terminal paths wire through `watch_transcript`:**
    1. CLI TTY `watch-and-exec` accepts `--harness`, `--workdir`, and `--session-file`, delegating polling to `harness.watch_transcript(...)`.
    2. Web `--terminal` (`_open_terminal_watch` in `web_runs_routes.py`) passes `--harness`, `--workdir`, and `--session-file` through `watch-and-exec`.
    3. Preview → **Start in terminal** (`POST /api/runs/{run_id}/start-terminal`) wraps the logged `harness command:` through `watch-and-exec` (with the run's `--harness`, `--workdir`, and `--session-file`) whenever `run_id`, `worktree`, and `session_file` exist, capturing `conversation_id` / minted transcripts automatically.
    4. Lazy resolution (`_resolve_session_file` in `web_runs_routes.py` / `adopt_terminal_transcript` in `cli_flow_util.py`): if a user ran a copied Preview command manually in an external shell, `AgyHarness.adopt_from_workspace(worktree, created_ts, session_file)` queries `conversation_summaries.db` by `workspace_uris` + `created_ts` and populates the `runs/agy/…` mirror on demand so Resume/Copy still succeed.
- **Transcript Parsing (`extract_last_assistant_text` & `tool_error_rate`):**
  - `extract_last_assistant_text`: returns the last non-empty `content` from `PLANNER_RESPONSE` lines (or `result.response` / `step_update.text_delta` from mirrored `stream-json` lines).
  - `tool_error_rate`: scans mirrored `brain/.../transcript.jsonl` `GENERIC` lines after `since_line` (`status == "ERROR"` counts as error, `status in ("DONE", "ERROR")` counts toward total; returns `None` when zero tool/generic steps exist).

### 3.4 Audit & Removal of All `omp` Leaks Outside `OmpHarness`

| Module | Current Leak | Refactor |
|---|---|---|
| `backend.py` | `Harness.session_file_flag` implements `omp` `--resume`/`--session-dir`; `launch()` comments reference `omp`. | Abstract `Harness` ABC; `OmpHarness` owns `--resume`/`--session-dir`. |
| `store.py` | `DEFAULT_HARNESS = "omp"`, `SUPPORTED_HARNESSES = ("omp",)`. | `DEFAULT_HARNESS = "agy"`, `SUPPORTED_HARNESSES = ("agy", "omp")`, helper `default_harness()`. |
| `doctor.py` | Checks `("git-wt", "omp", "gh", "glab", "jira")`. | Checks `("git-wt", "agy-guard", "omp", "gh", "glab", "jira")`. |
| `cli_harness.py` | `_launch_in_worktree` uses `"omp"` fallback; `_run_harness` assumes `newest_transcript` + `--session-dir`; `run_judge` passes `worktree` as `cwd` and skips `Harness` abstraction; `watch-and-exec` hardcodes `scope_dir` polling; `_publish_headless_summary` calls `harness_summary` directly. | Use `store.default_harness()`; pass `session_file` and `on_conversation_id` callback into `backend.launch`; run `run_judge` in its `TemporaryDirectory` (`directory`); update `watch-and-exec` (`--harness`, `--workdir`, `--session-file`) to call `harness.watch_transcript`; extract text via `harness.extract_last_assistant_text`. |
| `cli_validate.py` & `harness_summary.py` | `_tool_error_rate` only scores `omp`'s `toolResult` lines. | Accept optional `harness` (or delegate to `Harness.tool_error_rate`, with `harness_summary` supporting both `omp` and `agy` normalized records). |
| `suggest_icon.py` | `harness: str = "omp"` default; assumes `newest_transcript(Path(directory))` file minting. | Default `harness: str | None = None` → `store.default_harness()`; pass `session_file=judge_path` to `backend.launch`, read via `harness_obj.extract_last_assistant_text` (checking `judge_path` first, then `newest_transcript(Path(directory))` for `omp`). |
| `cli_sync.py` | Hardcodes `_cli._run_harness("omp", ...)` twice; `--harness` is `bool`. | Change `--harness` to `Optional[str]` (validated via `backend.get_harness`); `--harness <name>` auto-launches that harness on conflict; `--yes` without `--harness` uses `store.default_harness()`. |
| `cli_release.py` & `cli_release_fix.py` | No `--harness` option on `fix-default`; hardcodes `"omp"` fallback. | Add `--harness: Optional[str]` to `release fix-default` and `run_fix_default`; use `harness or store.default_harness()`. |
| `cli_flow.py`, `cli_flow_run.py`, `cli_flow_steps.py`, `cli_flow_util.py` | Hardcoded `"omp"` defaults; `SYNC_FLAGS` uses `("harness", "--harness", "bool")`; `flow run` & `message flow run` lack `--harness`; `run_sync_step` & `persist_terminal_pause` drop `harness_name`; `TERMINAL_CAPABLE` omits `"prodtest"`. | Add `--harness: Optional[str]` override on `flow run` & `message flow run` (`cli_harness or step_cfg.get("harness") or default_harness()`); `SYNC_FLAGS` uses `"str"`; include `"prodtest"` in `TERMINAL_CAPABLE`; `run_sync_step` & `persist_terminal_pause` preserve `harness_name`; replace all `"omp"` defaults with `store.default_harness()`. |
| `cli_review.py` | `"omp"` defaults/help text; `review --all` `_child_argv` drops `--harness`. | Use `store.default_harness()`; harness-agnostic help text; forward `if harness: argv += ["--harness", harness]` in `_child_argv`. |
| `cli_start.py`, `cli_adhoc.py`, `cli_action.py`, `cli_message.py`, `cli_prodtest_plan.py`, `cli_prodtest_run.py`, `cli_prodtest_checks.py` | `"omp"` defaults and `(v1: omp)` / `omp --resume` help strings. | Use `store.default_harness()` and harness-agnostic help text. |
| `web_args.py` | `BOOL_FLAGS["sync"]` has `--harness`; `VAL_FLAGS` lacks `--harness` on `sync`, `release`, `flow run`, `message flow run`; `_session_file_for` hardcodes `"omp"`. | Move `--harness` from `BOOL_FLAGS["sync"]` to `VAL_FLAGS["sync"]`; add `--harness` to `VAL_FLAGS` for `release`, `flow run`, `message flow run`; `_session_file_for` resolves `--harness` from `args` or `store.default_harness()`; add `config show` / `config set` specs. |
| `web_runs.py` & `web_runs_routes.py` | `_resume_shell_command` hardcodes `omp --session-dir`/`omp --resume`; `_claim_terminal_lock` hardcodes `"omp"`; `_open_terminal_watch` hardcodes `f"{harness_name} --session-dir ..."`; `start-terminal` bypasses `watch-and-exec`; `_resolve_session_file` hardcodes `omp` layout. | Delegate `_resume_shell_command` to `Harness.resume_argv(session_file, metadata)` and `_open_terminal_watch` to `Harness.bare_terminal_argv(scope)` + `watch-and-exec --harness`; wrap `start-terminal` in `watch-and-exec`; `_claim_terminal_lock` takes `harness_name`; `POST /api/worktrees/{ref}/terminal` accepts `harness`; `_resolve_session_file` delegates lazy `agy` workspace lookup; `Run`/`RunDetail` payloads include `harness_name` and `resume_command`. |

---

## 4. Config CLI & API

### 4.1 `workagent config` (`src/workagent/cli_config.py`)

Mounted in `cli.py` via `app.add_typer(config_app, name="config")`:
- `workagent config show [--json]`: outputs `{"default_harness": ..., "supported_harnesses": ["agy", "omp"]}`.
- `workagent config set <key> <value> [--json]`: supports `default-harness` / `default_harness`. Validates `<value>` against `backend.HARNESSES` (exit `2` on unsupported harness or unknown key) and writes to `config.json` via `store.save_config`.

### 4.2 `GET /api/config` (`src/workagent/webapp.py`)

Read-only endpoint returning:
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
Settings updates `default_harness` through `POST /api/runs` (`{"command": "config", "args": ["set", "default-harness", value], "confirm": false}`), preserving single-source-of-truth CLI-as-backend parity (`SPECS["config"]`, `confirm: false`).

---

## 5. Web UI Design — Returning Harness Chooser Everywhere

### 5.1 Shared `<HarnessSelect>` & `useConfig`

- **`useConfig()` (`web/src/lib/queries.ts` + `api.config()` in `web/src/lib/api.ts`)**: fetches `GET /api/config` and invalidates when a `config` run completes.
- **`<HarnessSelect>` (`web/src/components/HarnessSelect.tsx`)**:
  - `variant="segmented"` (default — matching `SegmentedControl` / `RunModeSelect`): renders `[Default (<cfg>)] | [agy] | [omp]` (when `allowDefault`) or `[agy] | [omp]` (Settings), showing a subtle availability note/tooltip when `available === false`.
  - `variant="select"`: compact `<select>` matching `ChainEditor` and `WorktreeDetail` card footer constraints.
- **`launchPrefs` (`web/src/lib/launchPrefs.ts`)**: stores per-tab `harness` (`""` = configured default, or `"agy"` / `"omp"`).

### 5.2 Complete Web UI Surface Map

| Surface | Component / File(s) | Control | Behavior / CLI Args |
|---|---|---|---|
| **Launch — Start tab & Candidates StartDialog** | `StartForm.tsx`, `launchConfig.ts`, `CandidatesCard.tsx` | `SegmentedControl` (`allowDefault`) | Appends `["--harness", h]` when non-empty; `startFlagList` includes `--harness`. |
| **Launch — Review / Fix comments tab** | `Launch.tsx`, `StartForm.tsx`, `launchConfig.ts` | `SegmentedControl` (`allowDefault`) | `buildReviewArgs` appends `["--harness", h]` when non-empty. |
| **Launch — Sync tab** | `Launch.tsx`, `launchConfig.ts` | `SegmentedControl` (`allowDefault`) | Appends `["--harness", h]` when non-empty. |
| **Launch — Adhoc Prompt tab** | `Launch.tsx`, `launchConfig.ts` | `SegmentedControl` (`allowDefault`) | Appends `["--harness", h]` when non-empty. |
| **Launch — Warning Banner** | `Launch.tsx` | Text readout | Computes `effective = form.harness || config.default_harness` and renders `agy-guard --dangerously-skip-permissions -p=…` vs `omp -p --auto-approve`. |
| **Message Launch (all 5 tabs)** | `MessageLaunch.tsx`, `messageLaunchConfig.ts` | `SegmentedControl` (`allowDefault`) | Shown on `adhoc`, `validate`, `draft-reply`, `action`, `flow`; `buildMessageArgs` appends `["--harness", h]` when non-empty. |
| **Prodtest Launch (`plan` & `run`)** | `ProdtestLaunch.tsx` | `SegmentedControl` (`allowDefault`) | Appends `["--harness", h]` when non-empty. |
| **Actions Page — Add/Update Form** | `ActionForm.tsx`, `messageRunConfig.ts` | `SegmentedControl` (`allowDefault`) | Saves `default_options.harness` (omitted when default); passes `--harness` to `SuggestIconButton` when set. |
| **Immediate Run Form & Modal (Actions & Flows)** | `ImmediateRunForm.tsx`, `messageRunConfig.ts`, `Actions.tsx`, `FlowTemplates.tsx`, `ActionsMenu.tsx`, `StatusActions.tsx`, `MessageRowActions.tsx` | `SegmentedControl` (`allowDefault`) | Shown for **both** `kind="action"` and `kind="flow"`; emits `["--harness", h]` in `buildImmediateActionArgs`, `buildImmediateWorktreeFlowArgs`, `buildImmediateFlowArgs`, and `buildMessageArgs`. |
| **Flow Template & Run Flow Step Config** | `ChainEditor.tsx` (`StepConfigForm`), `FlowTemplateForm.tsx` | Compact `<select>` (`allowDefault`) | Shown on `start`, `review`, `fix_comments`, `sync`, `action`, `prodtest_plan` (hidden on `cleanup`, `release`); stores `step.config.harness` (delta-aware with `hints`). |
| **Run Flow Modal (`FlowWizard`)** | `FlowWizard.tsx` | `SegmentedControl` (`allowDefault`: `"Use template / default"`) | Top-level flow override emitting `["--harness", h]` to `flow run` (overrides step configs when explicitly chosen), alongside per-step `<select>` controls in `ChainEditor`. |
| **Dashboard Bulk Confirm Dialogs** | `Dashboard.tsx` | `SegmentedControl` (`allowDefault`) | Included in `extras` of `Sync`, `Review`, and `Fix PR comments` bulk confirm dialogs; emits `["--harness", h]` when selected. |
| **Release Card — Fix Default Dialog** | `ReleaseCard.tsx` | `SegmentedControl` (`allowDefault`) | Emits `["--harness", h]` to `release fix-default`. |
| **Worktree Detail — Bare Terminal Launch** | `WorktreeDetail.tsx` | Compact `<select>` next to `Harness` button | Sends `{"mode": "harness", "harness": h}` to `POST /api/worktrees/{ref}/terminal`. |
| **Runs Table & RunDetail Copy/Resume** | `RunActions.tsx`, `format.ts`, `api.ts` | Copy / Resume buttons | Uses `run.resume_command` (falling back to harness-aware `resumeCommand`) — never hardcodes `omp --resume`. |
| **Settings Page** | `Settings.tsx` | `SegmentedControl` (`[agy] \| [omp]`) | Reads `useConfig()`, runs `config set default-harness <name>`, invalidates config query. |

---

## 6. Testing, Parity & Rollout

- **Parity enforcement (`tests/test_webapp_data.py::test_specs_mirror_cli_flags`):** Validates `SPECS`, `BOOL_FLAGS`, and `VAL_FLAGS` against the live Typer app (`sync`, `release`, `flow run`, `message flow run`, `config show`, `config set`).
- **Static isolation guard (`tests/test_backend.py::test_no_omp_outside_omp_harness`):** AST/source check asserting zero direct `omp` CLI/subprocess argv strings (`"omp"`, `--session-dir`, `--auto-approve`) or `"omp"` fallback defaults remain in `src/workagent/` outside `OmpHarness` (with `backend.HARNESSES` registration and `store.SUPPORTED_HARNESSES` explicitly allowlisted).
- **Backend test coverage:**
  - `AgyHarness` argv construction (`-p=` / `-i=` with flags first, `agy-guard` binary, `--mode plan` judge), NDJSON streaming + `brain/` mirroring + `last_assistant_text` + `tool_error_rate`, timeout process-group kill, `conversation_summaries.db` workspace matching (concurrent worktree disambiguation), `session_file_flag` / `resume_argv` (`--conversation <id>`), and `start-terminal` watch capture.
  - CLI tests: `config show|set`, `sync --harness agy|omp`, `release fix-default --harness`, `flow run --harness` precedence over step config, `review --all --harness` child forwarding, `suggest-icon` with `agy`, `doctor` checking `agy-guard`.
  - Webapp tests: `GET /api/config`, `POST /api/worktrees/{ref}/terminal` with `harness`, `Run.harness_name` & `Run.resume_command`, `start-terminal` `watch-and-exec` wrapping.
- **Frontend Vitest coverage:** `HarnessSelect`, `launchConfig`, `messageLaunchConfig`, `messageRunConfig`, `ChainEditor`, `FlowWizard`, `Dashboard` bulk confirms, `Runs`, `Settings`.
- **Docs & Inventory:** Update `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/FEATURES-NOTES.md`, `CHANGELOG.md`.
- **Risks & Mitigations:**
  1. *`agy` `-p` / `-i` flag token consumption:* Always emit flags before `f"-p={prompt}"` / `f"-i={prompt}"`; covered by unit tests.
  2. *`conversation_summaries.db` locked or unavailable during watch:* Open SQLite read-only (`?mode=ro` with timeout) and fall back to `~/.gemini/antigravity-cli/brain/` directory mtime scan.
  3. *Breaking `sync --harness` boolean callers:* Search all repo callers/tests (`test_sync.py`, `test_cli_sync.py`, `cli_flow_util.py`, `web_args.py`) and update cleanly.
