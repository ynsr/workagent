# ACP Harness Fixes & Spec Alignment — Design (issue #128)

## Goal
Fix all runtime bugs and spec-vs-implementation drifts introduced in PR #123 (commit `ff8b1ab`, issue #112) against [`docs/superpowers/specs/2026-11-20-acp-harness-design.md`](2026-11-20-acp-harness-design.md), so ACP execution, interactive question answering, post-run validation, `env.sh` enforcement, web endpoints, and the Run page / UI forms all work end-to-end as specified.

## Decisions (from user)
- **Full scope:** Cover all 6 issues listed in #128 **plus** all 6 additional bug/drift groups uncovered by auditing `ff8b1ab` (unreachable `launch_acp`, CLI/flow/release/action/message/prodtest ACP callers, `_read_answer` & SIGTERM orphan leaks, `pending_question` clearing & `rehydrate()` race, frontend UI drifts, and `env.sh` enforcement).
- **Markdown rendering:** Add `react-markdown` and `remark-gfm` to `web/package.json` for safe React AST Markdown rendering of assistant messages in `AcpTranscript.tsx`.
- **Dual-mode `--acp` dispatch:**
  - `no_tty=True` (web `serve` / CLI `--no-tty --acp`): append `VERDICT_SUFFIX`, run through `cli_validate.validate_headless(...)` calling `harness.launch_acp(..., no_tty=True, input_stream=None)`, tail `WORKAGENT_RUN_DIR/input.jsonl` when set, or emit `waiting: true` + `WAITING_USER_RESPONSE` and exit `2` when unattended.
  - `no_tty=False` (direct CLI TTY `--acp`): run `harness.launch_acp(..., no_tty=False, input_stream=sys.stdin)` directly (no `VERDICT_SUFFIX`, no validation retry loop; answers read from `sys.stdin`).
- **Strict `env.sh` restoration:** Restore the original spec behavior — `env.launch_env(None)`, `env.merged_env(None)`, and all terminal/gate entry points call `env.ensure_env_file(None)` (auto-creating `<config_dir>/env.sh` when missing), and wire all 6 missing subprocess/terminal call sites.
- **Strict `POST /api/runs/{run_id}/input` validation:** Keep `text` as the canonical request field (matching `QuestionCard.tsx` and `web/API_CONTRACT.md`), accept `answer`/`choice` as fallback aliases, require an active `pending_question` (`409 no_pending_question` if none), and require `qid` to match `pending_question["qid"]` (`409 qid_mismatch` if missing or mismatched when `pending_question["qid"]` is non-empty).

## Documented Intentional Drift
- **`POST /api/runs/{id}/input` canonical field name (`text` vs `answer`/`choice`):** The original #112 spec (line 78) sketched `{qid, answer, choice}`, while PR #123 shipped `{"text", "qid"}` across `web_runs_routes.py`, `QuestionCard.tsx`, and `web/API_CONTRACT.md`. Keeping `text` canonical while accepting `answer`/`choice` as fallback aliases (and writing `{"qid", "text", "answer"}` to `input.jsonl`) preserves compatibility with the existing frontend and API contract while honoring the original spec's field names.

---

## Architecture & Detailed Changes

### 1. ACP Transport, Lifecycle & Supervised IPC
- **`src/workagent/run_supervised.py::run_supervised`:**
  - Export `WORKAGENT_RUN_DIR=str(run_dir)` in the child process environment: `env={**os.environ, "WORKAGENT_RUN_DIR": str(run_dir)}` passed to `subprocess.Popen(child_argv, ...)`.
- **`src/workagent/acp_harness.py::AcpClient`:**
  - Spawn `<bin> acp` with `stderr=subprocess.PIPE` (replacing `stderr=subprocess.DEVNULL`).
  - Start a daemon thread `_stderr_loop` draining `self._proc.stderr` into a thread-safe `collections.deque(maxlen=40)` ring buffer (truncating individual lines to 500 chars; never logging environment variables or secrets).
  - Add `stderr_tail(self, max_lines: int = 20) -> str` returning the last `max_lines` captured lines joined by `\n`.
- **`src/workagent/acp_harness.py::AcpHarness.launch_acp`:**
  - Replace the undefined `self._stderr_tail()` at line 464 with `client.stderr_tail()`, and include `client.stderr_tail()` (when non-empty) in `initialize`, `authenticate`, and `session/new` `HarnessError` messages.
  - Install a temporary `SIGTERM` signal handler (only when called on the main thread; restored in `finally`) that sets `self._answer_stop = True`, calls `client.close()` (killing the `<bin> acp` process group via `os.killpg`), and raises `SystemExit(143)`. Reset `self._answer_stop = False` at the start of each `launch_acp` call.
- **`src/workagent/acp_harness.py::AcpHarness._read_answer`:**
  - Fix the inverted `no_tty` logic and clean up channel precedence:
    1. If `os.environ.get("WORKAGENT_RUN_DIR")` is non-empty: tail `Path(run_dir) / "input.jsonl"` (polling at 0.2s, exiting early with `None` if `self._answer_stop` is set).
    2. Else if `input_stream` is provided (and has `.readline`): read one line, parse JSON (`text`/`answer`/`choice`) or use raw text; raise `HarnessError` if stream is closed/empty.
    3. Else if `not no_tty`: read one line from `sys.stdin` (same parsing as step 2).
    4. Else (`no_tty=True`, no `WORKAGENT_RUN_DIR`, no `input_stream`): return `None`, causing `_answer_question` to emit `{"t": "turn_end", "id": session_id, "waiting": True}`, print `WAITING_USER_RESPONSE`, and return exit code `2`.

### 2. ACP Transcript Summary & Post-Run Validation
- **`src/workagent/harness_summary.py::last_assistant_text`:**
  - Recognize mirrored ACP JSONL lines where `data.get("t")` is a string:
    - `t == "message"`: append `str(data.get("text") or "")` to `stream_deltas`.
    - `t == "turn_end"`: if `stream_deltas` is non-empty, set `last = "".join(stream_deltas).strip()` and clear `stream_deltas`.
    - `t == "answer"`: clear `stream_deltas` (new user turn begins, so subsequent assistant chunks represent the final turn).
    - Other ACP event types (`thought`, `tool_call`, `usage`, `question`) are ignored for assistant text extraction.
- **`src/workagent/harness_summary.py::tool_error_rate`:**
  - Recognize mirrored ACP JSONL lines with `t == "tool_call"`:
    - Key by `str(data.get("id") or "")` (falling back to a unique synthetic index when `id` is empty) so multiple `tool_call` / `tool_call_update` lines for the same `toolCallId` collapse to a single counted tool call using the latest status/output.
    - Terminal statuses: `completed`, `done`, `success`, `ok` (non-error unless output matches `_ERR_RE` or `isError`/`is_error` is true) and `error`, `failed`, `failure` (error). Non-terminal statuses (`pending`, `running`, `in_progress`, `""`) are not counted unless followed by a terminal update.
    - Count collapsed terminal ACP tool calls into `(errors, total)` alongside existing transcript formats.
- **`src/workagent/cli_harness.py::_run_harness`:**
  - Fix the unreachable ACP branch:
    - Define `is_exec = bool(launch or mode == "acp")`.
    - Change `if not launch:` (preview branch, line 363) to `if not is_exec:`.
    - Change `if no_tty and launch and not terminal:` (line 268, `VERDICT_SUFFIX`) to `if no_tty and is_exec and not terminal:`.
    - Change `if run_key and launch:` (line 385, atomic `store.record_harness_run`) to `if run_key and is_exec:`.
    - Reject non-ACP harnesses when `mode == "acp"` via `_fail(f"harness {harness_name!r} does not support --acp", EXIT_USAGE)` (exit `2`) before claiming locks or inserting runs.
    - In `no_tty=True`: unify `mode == "headless"` and `mode == "acp"` through `cli_validate.validate_headless(...)`:
      - Inside `run_once(attempt_prompt)`: when `mode == "acp"`, call `harness.launch_acp(attempt_prompt, worktree or fallback_dir, model=model, effort=effort, session_file=current_path or None, env_file=str(env_path) if env_path else None, timeout_s=timeout_s, input_stream=None, no_tty=True)` (passing `input_stream=None` instead of a `Path` object); update `result["session_file"]` and DB `file_path`; extract `text = harness.extract_last_assistant_text(current_path, max_chars=None)`.
      - `run_judge` continues to use `backend.launch(..., harness.judge_argv, ...)` (bare non-ACP judge).
      - DB attempt rows, `finish_run`, `_publish_headless_summary`, and exit code `0`/`1`/`2` handling work identically for headless and ACP.
    - In `no_tty=False`: when `mode == "acp"`, call `harness.launch_acp(prompt, worktree or fallback_dir, model=model, effort=effort, session_file=session_path or None, env_file=str(env_path) if env_path else None, timeout_s=timeout_s, input_stream=sys.stdin, no_tty=False)` directly (no `validate_headless` retry loop), update `file_path`, `finish_run`, and raise `HarnessError(exit_code=code)` on non-zero exit.

### 3. CLI Commands, Flow Forwarding & `web_args.py`
- **Shared executing-mode helper (`cli_harness.py::is_exec_mode(launch, acp, run_mode)`):**
  - Returns `True` when `launch`, `acp`, or `(run_mode or "").strip().lower() in ("headless", "acp")`.
- **Call sites updated to support ACP cleanly (no `launch=True` + `acp=True` collisions, no dropped flags):**
  - `src/workagent/cli_flow_steps.py`: in `run_start_step`, `run_review_step`, `run_action_step`, `run_prodtest_plan_step`, pass `launch=not bool(config.get("acp"))` so ACP flow steps do not trip `--acp cannot be used with --launch`. In `run_release_step`, remove `"acp"`, `"model"`, `"effort"` from the `pick(...)` kwargs passed to `cli_release.run_release(...)` (which is a pure git/MR merge operation, preventing `TypeError`).
  - `src/workagent/cli_adhoc.py`: set `use_launch = not terminal and not acp and (run_mode or "").lower() not in ("preview", "terminal", "acp")`.
  - `src/workagent/cli_sync.py`: in `_handle_merge_conflict`, pass `launch=not acp and (run_mode or "").lower() != "acp"`.
  - `src/workagent/cli_start.py`: forward `acp`, `run_mode`, `model`, `effort` in the existing-worktree reuse call to `_launch_in_worktree(...)`; gate `_guard_harness` and `_start_post_status` on `is_exec_mode(launch, acp, run_mode)` instead of bare `launch`.
  - `src/workagent/cli_review.py`: forward `--acp`, `--run-mode`, `--model`, `--effort` in `_child_argv` (`review --all`) and `_chain_fix_command` / `_run_then_fix` (`--then-fix-comments`); gate `review --all` spawn, `pull_branch`, `_guard_harness`, `_mark_reviewed`, `_clear_reviewed`, `_review_post_fix`, and `_review_verdict_settle` on `is_exec_mode(launch, acp, run_mode)`.
  - `src/workagent/cli_action.py`: extend `_resolve_mode(action, launch, terminal, acp=False, run_mode=None)` to accept `"acp"` (both CLI `--acp` / `--run-mode acp` and `default_options.run_mode == "acp"`); only set `WORKAGENT_PREVIEW=1` for `"preview"`, run `_guard_harness` and `post_execution` for `"headless"` and `"acp"`.
  - `src/workagent/cli_message.py`: in `_run_prompt`, compute `mode` honoring `acp` and `run_mode` (running `_guard_harness` and setting `no_tty=True` for `"acp"`).
  - `src/workagent/cli_prodtest_plan.py`, `cli_prodtest_run.py`, `cli_prodtest_checks.py`: gate `_finish_plan`, `_run_all`, `"running"` status, `timeout_s`, and `_finish_run` on `is_exec_mode(launch, acp, run_mode)`.
  - `src/workagent/cli_flow_run.py`: re-run `normalize_config()` after applying top-level `--run-mode` overrides so `--run-mode preview|terminal|acp|headless` normalizes uniformly.
  - `src/workagent/cli_release.py` & `src/workagent/cli_release_fix.py`: add `--acp`, `--model`, `--effort` options to `release` CLI; add `"acp"` to `cli_release_fix.RUN_MODES`; forward `acp`, `run_mode`, `model`, `effort` through `run_fix_default` into `_run_harness`; reject `--acp`, `--model`, `--effort` on plain `release REPO` with `EXIT_USAGE` (matching `--action` and `--run-mode`).
  - `src/workagent/web_args.py`: add `"--acp"` to `BOOL_FLAGS["release"]` and `"--model"`, `"--effort"` to `VAL_FLAGS["release"]`; in `_validate_args`, check `-L` alongside `--launch`/`--terminal` for `--acp` mutual exclusion, and reject conflicting `--acp` + `--run-mode preview|headless|terminal`.

### 4. Strict `env.sh` Enforcement Everywhere
- **`src/workagent/env.py`:** `launch_env(None)` and `merged_env(None)` call `ensure_env_file(None)` instead of `resolve_env_file(None)`.
- **Switch `resolve_env_file` → `ensure_env_file`:**
  - `src/workagent/web_runs_routes.py::_open_terminal_watch`
  - `src/workagent/web_runs.py::_resume_shell_command`
  - `src/workagent/release_gates.py::_run_group` (`run_repo_command`)
- **Wire the 6 missing call sites:**
  1. `src/workagent/cli_harness.py:380` (interactive preview shell): use `os.execvpe(shell, [shell], _env.merged_env(env_path))`.
  2. `src/workagent/cli_flow_util.py::spawn_terminal_step`: wrap `cmd` with `env.wrap_command(..., env.ensure_env_file())`.
  3. `src/workagent/web_runs_routes.py` `/api/runs/{id}/start-terminal`: if `cmd` does not already begin with `source `, wrap with `env.wrap_command(cmd, env.ensure_env_file())`.
  4. `src/workagent/release_gates.py::_ci_sha_status`: pass `env=_env.merged_env()` to `subprocess.run` for `git`, `glab`, and `gh`.
  5. `src/workagent/cli_prodtest_gate.py::current_prodtest_diff`: pass `env=_env.merged_env()` to `subprocess.run`.
  6. `src/workagent/prodtest_scripts.py::_env`: initialize from `env.merged_env()` instead of `dict(os.environ)`.

### 5. Web Backend API Fixes
- **`GET /api/harnesses/{name}/options?refresh=0|1` (`src/workagent/web_runs_routes.py`):**
  - Add `refresh: int = 0` query parameter to `harness_options` and `refresh: bool = False` to `_cached_harness_options`.
  - When `refresh` is truthy (`refresh=1`), skip the `_OPTIONS_CACHE` lookup and run a fresh `harness.discover_options()`, updating the cache on success.
- **`POST /api/runs/{run_id}/input` (`src/workagent/web_runs_routes.py`):**
  - Extract answer text from `body.get("text")`, falling back to `body.get("answer")` or `body.get("choice")`; return `422 bad_arg` if blank.
  - Extract `qid = str(body.get("qid") or "").strip()`.
  - Under `run.lock`:
    - If `run.state != "running"`: raise `ApiError("not_running", ..., 409)`.
    - Compute `pending = _pending_question_from_texts([txt for _, txt in run.lines])`.
    - If `pending is None`: raise `ApiError("no_pending_question", f"run {run_id} has no pending question", 409)`.
    - Expected `pqid = str(pending.get("qid") or "")`: if `pqid` is non-empty and `qid != pqid`, raise `ApiError("qid_mismatch", f"qid {qid!r} does not match pending question {pqid!r}", 409)`.
  - Append `json.dumps({"qid": qid or pqid, "text": text, "answer": text}) + "\n"` to `Path(run_dir) / "input.jsonl"`.
- **`pending_question` clearing & synchronous `rehydrate()` (`src/workagent/web_runs.py`):**
  - In `_summary(run, lines)`: set `out["pending_question"] = _pending_question_from_texts(src) if run.state == "running" else None`.
  - In `_persisted_summary(row)`: set `"pending_question": _pending_question_from_texts(...) if state == "running" else None`.
  - In `rehydrate(registry)`: perform the initial `output.log` backfill into `run.lines` synchronously before spawning `_start_tailer(..., backfill=False)` so `pending_question` is immediately available on the first `GET /api/runs/{id}` after restart.
- Update `web/API_CONTRACT.md` to document `?refresh=0|1`, `409 no_pending_question` / `qid_mismatch`, and `text` (with `answer`/`choice` aliases).

### 6. Frontend `AcpTranscript`, `RunDetail` & UI Drifts
- **`web/package.json`:** Add `react-markdown` and `remark-gfm`.
- **`web/src/lib/harness.ts` (`parseAcpBlocks`):**
  - Extend `AcpBlock` with `id?: string`, `args?: unknown`, `output?: string`, `usage?: Record<string, unknown>`.
  - Coalesce consecutive `message` events into a single `message` block (concatenating `text`), and consecutive `thought` events into a single `thought` block (concatenating `text`).
  - Merge `tool_call` events sharing the same non-empty `id` (`toolCallId`) in place at their first-seen position: update `name`, `toolKind`, `status`, `args` (when non-empty), and `output` (appending/replacing when non-empty).
  - Format `usage` events into readable text (e.g. `tokens: in 120 · out 45 · total 165` when numeric fields are present, falling back to concise key=value pairs — never raw `JSON.stringify(obj)`).
  - Normalize `question.options`: extract `o.label` when `o` is an object with a `label` property, else `String(o)`.
- **`web/src/components/AcpTranscript.tsx`:**
  - `thought` block: render inside `<details className="...">` (collapsed by default) with `<summary>Thinking</summary>` and muted monospace/italic body.
  - `tool_call` block: render inside `<details open={isExpanded}>` where `isExpanded` is `true` for running/pending/in_progress/failed/error statuses and `false` for completed/done/success/ok; `<summary>` shows an icon by `toolKind`, tool `name`, and status `Badge`; body renders formatted `args` and `output` in monospace.
  - `message` block: always open, rendered via `<ReactMarkdown remarkPlugins={[remarkGfm]}>` with readable prose/code styling.
  - Sticky-bottom autoscroll pausing on manual scroll-up (>48px from bottom) with `requestAnimationFrame`-batched scroll updates.
- **`web/src/pages/RunDetail.tsx`:**
  - Detect whether `lines` contain structured ACP events (`hasAcpStructuredEvents(lines)`).
  - When present, render a **`Structured / Raw`** segmented toggle (defaulting to `Structured`): `Structured` renders `<AcpTranscript />`, `Raw` renders `<LogViewer />` (no longer stacking both simultaneously). Non-ACP runs render `<LogViewer />` directly.
- **UI drift fixes:**
  - `web/src/components/CandidatesCard.tsx`: handle `runMode === "acp"` in `StartDialog` mode description (distinct from `"Preview only"`).
  - `web/src/components/ChainEditor.tsx`: add `ModelSelect` and `EffortSelect` to `StepConfigForm` (wired to step config `model`/`effort`, cleared on harness change).
  - `web/src/components/MessageRowActions.tsx`: forward `runMode`, `harness`, `model`, `effort` on the inline `Validate` button and in `buildImmediateMessageActionArgs` (honoring action's `default_options` runMode/harness/model/effort).
  - `web/src/pages/Dashboard.tsx`: in `BulkConfirmExtras`, call `setModel("")` and `setEffort("")` alongside resetting refs when harness changes.
  - `web/src/pages/Launch.tsx` & `web/src/components/StartForm.tsx`: hydrate saved `model` and `effort` from launch prefs in `prefsForMode` and `useStartForm`.

---

## Testing Strategy
- **Backend (`uv run pytest -v`):**
  - Supervised web ACP end-to-end test: spawn a supervised run via `POST /api/runs` with fake ACP agent emitting `ask_question`, verify `WORKAGENT_RUN_DIR` is set, verify `pending_question` surfaces on `GET /api/runs/{id}`, answer via `POST /api/runs/{id}/input`, verify follow-up turn completes and `pending_question` clears.
  - `AcpClient` stderr ring buffer + `stderr_tail()` on init/auth failure (no `AttributeError`) + SIGTERM handler killing `<bin> acp` with no orphans.
  - `_read_answer` precedence tests: `WORKAGENT_RUN_DIR`, `input_stream`, TTY `sys.stdin` (`no_tty=False`), unattended `no_tty=True` returning `None` → `WAITING_USER_RESPONSE` exit `2`.
  - `harness_summary.last_assistant_text` & `tool_error_rate` on ACP-mirrored JSONL (chunk accumulation, multi-turn `answer` reset, `toolCallId` deduplication).
  - `_run_harness` dual-mode ACP tests: `no_tty=True` invokes `validate_headless` with `VERDICT_SUFFIX` and `launch_acp`; `no_tty=False` invokes `launch_acp(..., input_stream=sys.stdin)` directly; non-ACP harness exits `2`.
  - CLI & flow tests: `flow` steps with `acp=True`, `run_release_step` without `TypeError`, `release fix-default --run-mode acp --model --effort`, `adhoc --acp`, `sync --acp`, `action run --acp`, `message` ACP, `review --all` / `--then-fix-comments` forwarding, `start` reuse forwarding, `test_specs_mirror_cli_flags` + `-L`/`--run-mode` conflict tests.
  - `env.sh` tests: `launch_env(None)`/`merged_env(None)` auto-create `env.sh`, plus unit tests for all 6 newly wired call sites.
  - Web API tests: `GET /api/harnesses/{name}/options?refresh=1` bypasses cache; `POST /api/runs/{id}/input` returns `409` on no pending question or `qid` mismatch, accepts `text`/`answer`/`choice`; `pending_question` is `None` after exit/cancel; `rehydrate()` surfaces `pending_question` immediately.
- **Frontend (`cd web && npm test`, `npm run typecheck`, `npm run build`):**
  - `parseAcpBlocks` tests: message/thought coalescing, `toolCallId` merging, formatted `usage`, `{label}` option normalization, malformed first-line skip.
  - `AcpTranscript` & `RunDetail` tests: collapsed `Thinking` `<details>`, tool call `<details>` open/closed by status, Markdown rendering (headings/code/tables), `Structured / Raw` toggle switching between `AcpTranscript` and `LogViewer`.
  - Form tests for `CandidatesCard`, `ChainEditor`, `MessageRowActions`, `Dashboard`, and `Launch`/`StartForm`.
- **Isolation:** All backend tests use `isolated_config` / `tmp_path` and never touch real `~/.config/workagent` or `~/.gemini/antigravity-cli`.
