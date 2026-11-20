# ACP Harness — Design (issue #112)

## Goal
Add a generic ACP (Agent Client Protocol) harness layer (`AcpHarness`, `OmpAcpHarness`, `AgyAcpHarness`) so workagent can drive AI harnesses (`omp acp`, `agy-guard acp`) over stdio JSON-RPC. Runs in ACP mode stream structured output as collapsible blocks on the Run page, users can answer agent questions inline from the Run page, model and effort selection is available across all execution modes, and `env.sh` + yolo/auto-approve apply uniformly everywhere.

## Decisions (from user)
- **Harness selection (UI & config):** Keep `agy` (`agy-guard`) as the default harness. The AI harness split button contains **only `OMP` (`omp`) and `AGY` (`agy`)** (plus `Default (agy)` where `allowDefault` is enabled).
- **Execution mode selection:** The execution mode split button contains **`preview`, `headless`, `acp`, and `terminal`**.
- **Class hierarchy:** Keep legacy `Harness`, `OmpHarness`, `AgyHarness`. Add abstract `AcpHarness(Harness)` and concrete `OmpAcpHarness(AcpHarness)` / `AgyAcpHarness(AcpHarness)`. Both `OmpHarness` and `AgyHarness` implement `AcpHarness` (with stubs for unsupported ACP methods) for backward compatibility. Execution mode `acp` dispatches `omp` → `OmpAcpHarness` and `agy` → `AgyAcpHarness`; `preview`/`headless`/`terminal` use `OmpHarness` / `AgyHarness`.
- **Model & effort selection across all modes:** `--model` and `--effort` are available for **all** execution modes (`preview`, `headless`, `acp`, `terminal`). Options are discovered via `GET /api/harnesses/{name}/options` (server-side TTL cache probing ACP `session/new`); an input is disabled when ACP returns no values. Selecting none uses the harness default.
- **YOLO / auto-approve everywhere:** Every harness run (ACP, non-TTY/headless, preview, interactive terminal, resume, bare terminal) runs in yolo/auto-approve mode. Verified locally (2026-11-20): `omp acp --approval-mode=yolo` and `agy-guard acp --dangerously-skip-permissions` both initialize cleanly. `omp` uses `--approval-mode=yolo` (and `--auto-approve` in `-p` headless); `agy-guard` uses `--dangerously-skip-permissions`. `AcpClient` auto-approves `session/request_permission` defensively; no permission UI.
- **`env.sh` everywhere:** `<config_dir>/env.sh` (or `--env-file` override) is sourced before **every** harness execution mode (`preview`, `headless`, `acp`, `terminal`, plus ACP options probes), **every** harness (`omp`, `agy`), and **every** terminal command spawned by workagent (start-terminal, resume, bare harness, prodtest terminal, pre/post scripts, repo commands).
- **Interactive Run answers:** `POST /api/runs/{id}/input` → CLI child stdin → next ACP turn.
- **Rendering:** Frontend parses structured `ACP:` lines into collapsible React blocks; Raw Log view toggle stays available.
- **Transport:** Pure-stdlib JSON-RPC client inside the existing CLI child process model (zero new Python dependencies).

## Verified protocol facts (probed locally 2026-11-20)
| Aspect | `omp acp` | `agy-guard acp` |
|---|---|---|
| initialize | `protocolVersion:1` ok | ok |
| yolo flag | `omp acp --approval-mode=yolo` ok (`--auto-approve` also ok) | `agy-guard acp --dangerously-skip-permissions` ok |
| CLI model/effort flags | `--model=<val>`, `--thinking=<val>` | `--model <val>`, `--effort <val>` |
| authenticate | `methodId:"agent"` returns `{}` | `agent` rejected (-32602); `oauth-*`/api key; `session/new` works with existing local auth |
| `session/new` `configOptions` | `mode`, `model` (category `model`), `thinking` (category `thought_level`: off..xhigh) | `model`, `thought_level` (low..xhigh), `mode` |
| `models` field | absent | `availableModels[{modelId,name}]` + `currentModelId` |
| streaming | `session/update`: `agent_message_chunk`, `agent_thought_chunk`, `usage_update`, `available_commands_update`, `session_info_update` | same plus `tool_call` / `tool_call_update` (`name`, `args`, `status`, `kind`) |
| ask | no ask tool in ACP mode; agent asks in plain text, turn ends `end_turn` | `ask_question` tool_call with `rawInput.questions[{question,options,is_multi_select}]`; turn ends `end_turn` |
| set option | `session/set_config_option {sessionId, configId, value}` | same |

Consequence: an agent question ends the current turn. The user's answer is sent as the next `session/prompt` in the same ACP session (process stays alive while the child waits on stdin).

## Architecture

### Backend classes (`src/workagent/acp_harness.py`, new; `backend.py`, `agy_harness.py`)
- `AcpClient`: stdio JSON-RPC 2.0 transport. Always spawns with `env.merged_env(env_file)` (sourcing `env.sh`). Line-delimited JSON, id correlation, reader thread, notification callback, defensive auto-allow for agent→client `session/request_permission` (fs/terminal capabilities not advertised). Errors → `HarnessError`; timeout → `HarnessTimeout`.
- `AcpHarness(Harness, abc.ABC)`: common flow `initialize -> authenticate (per-harness) -> session/new|load -> apply model/effort -> prompt turns -> close`. Abstract/hook methods: `acp_argv()`, `authenticate_method()`, `parse_options(session_new_result) -> HarnessOptions`, `extract_question(update) -> Question|None`, `model_config_id()`, `effort_config_id()`, `model_effort_cli_args(model, effort) -> list[str]`.
- `OmpAcpHarness(AcpHarness)`: `acp_argv() -> ["omp", "acp", "--approval-mode=yolo"]`, `authenticate_method() -> "agent"`, maps `model` and `thinking` configOptions.
- `AgyAcpHarness(AcpHarness)`: `acp_argv() -> ["agy-guard", "acp", "--dangerously-skip-permissions"]`, `authenticate_method() -> None`, maps `model` and `thought_level` configOptions, extracts `ask_question` tool_call payloads.
- `OmpHarness(AcpHarness)` & `AgyHarness(AcpHarness)`:
  - Keep existing CLI/TTY/non-TTY behavior and implement `AcpHarness` with stubs for ACP-only methods (`supports_acp=False`).
  - Add yolo/auto-approve flags to **all** argv builders (`command_argv`, `resume_argv`, `bare_terminal_argv`): `--approval-mode=yolo` (plus `--auto-approve` in `-p`) for `omp`; `--dangerously-skip-permissions` for `agy-guard`.
  - Append model/effort CLI flags when provided: `omp` → `--model=<val>`, `--thinking=<val>`; `agy-guard` → `--model <val>`, `--effort <val>`.
- Dispatch: `HARNESSES` exposes `omp` and `agy` publicly. A helper `get_acp_harness(name)` maps `"omp"` → `OmpAcpHarness` and `"agy"` → `AgyAcpHarness` for `mode == "acp"` execution and for `GET /api/harnesses/{name}/options` discovery.
- `HarnessOptions {models:[{id,name}], efforts:[{id,name}], current_model, current_effort}`; empty list ⇒ UI disables that input. When user selects no model/effort, omit the flag / skip `set_config_option` so the harness default applies.

### `env.sh` enforcement across all execution & terminal paths
- Centralize `env.ensure_env_file(env_file)` + sourcing at every execution boundary:
  - **ACP runs & options probes:** `AcpClient` spawns subprocesses with `env.merged_env(env_file)`.
  - **Headless runs:** `run_blocking_cmd` and `AgyHarness.launch` use `env.merged_env(env_file)` (always ensuring `env.sh` even when `--env-file` is omitted).
  - **Interactive TTY & OS terminal launches:** `exec_tty` updates `os.environ` via `env.launch_env(env_file)`; every terminal emulator spawn (`terminal.py`, `web_runs_routes.py` `/terminal`, `/start-terminal`, `/resume`, bare harness, prodtest terminal, and any shell command run in a terminal) wraps the command with `env.wrap_shell(cmd, env_file)` (`source <env.sh> && <cmd>`).
  - **Preview mode:** printed/copied `harness command:` includes `source <env.sh> && ...` so copy-paste and "Start in terminal" both source `env.sh`.

### Run execution & CLI flags
- CLI flags on every command that accepts `--harness` (`start`, `review`, `action run`, `flow run`, `adhoc`, `message *`, `prodtest *`, `release`):
  - `--acp` (boolean flag alongside `--launch`/`--no-tty` and `--terminal`) and `--run-mode preview|headless|acp|terminal` where `--run-mode` is used.
  - `--model <value>` and `--effort <value>` (forwarded to preview, headless, ACP, and terminal).
- Mirror all new/updated flags in `web_args.py` (`BOOL_FLAGS`, `VAL_FLAGS`, `SPECS`, `_validate_args`), `web/API_CONTRACT.md`, and `web/FEATURE_INVENTORY.md`.
- ACP run child emits structured events as one JSON object per stdout line prefixed `ACP:` (e.g. `ACP:{"t":"thought","id":..,"text":..}`) alongside normal log lines so existing log storage, SSE, and DB persistence work unchanged. Event types: `message`, `thought`, `tool_call` (id, name, kind, status, args, output), `usage`, `question` (qid, text, options, multi), `turn_end`.
- Question handling: when an agent emits a `question`, the child emits `ACP:{"t":"question",...}` and blocks reading stdin; `POST /api/runs/{id}/input {qid, answer|choice}` writes one JSON line to the child's stdin. Unattended `--no-tty` mode does not block on questions (emits `question`, treats as `INCOMPLETE`/`WAITING_USER_RESPONSE`).
- Persistence: lines are persisted per run; `GET /api/runs/{id}` replays them so the structured view rebuilds from history.

### Web API
- `GET /api/config`: `harnesses` list contains only `omp` and `agy`; `default_harness` remains `agy`.
- `GET /api/harnesses/{name}/options?refresh=0|1` → `{supported, models, efforts, current_model, current_effort, warning}`. Probes the ACP harness (`OmpAcpHarness` for `omp`, `AgyAcpHarness` for `agy`) by spawning `<bin> acp` with `env.sh` sourced, running `initialize` + `session/new` in a temp cwd, reading `configOptions`, and closing. Server-side TTL cache (10 min), 5s timeout, always 200 (surfaces errors in `warning`).
- `POST /api/runs/{id}/input` → 404 unknown run, 409 not running or no pending question, 422 invalid body; validates `qid`. Subject to existing Host/Origin/Content-Type guard.

### Frontend
- **Harness selector (`HarnessSelect`):** Contains **only `OMP` and `AGY`** (plus `Default (agy)` when `allowDefault` is set). Present across all action/launch/flow forms (`ActionForm`, `FlowWizard`, `ChainEditor`, `ImmediateRunForm`, `ReleaseCard`, `StartForm`, `Launch`, `ProdtestLaunch`, `MessageLaunch`, `Dashboard`, `WorktreeDetail`, `Settings`).
- **Execution mode selector (`RunModeSelect` / `RUN_MODE_OPTIONS`):** Split button with **`preview`, `headless`, `acp`, `terminal`** everywhere execution mode is chosen.
- **Model & Effort selectors (`ModelSelect`, `EffortSelect`):** Driven by `useHarnessOptions(effectiveHarness)`. Shown and enabled for **all** execution modes (`preview`, `headless`, `acp`, `terminal`); disabled with tooltip only when ACP returns no options for that input or discovery failed. Empty selection = "Default". Emit `--model` / `--effort` only when non-empty.
- **`RunDetail` (`AcpTranscript` + `QuestionCard`):**
  - Parses `ACP:` lines into structured blocks; falls back to raw log lines for non-ACP runs; Structured/Raw toggle.
  - Thoughts: grouped in a collapsed `<details>` ("Thinking"), muted style.
  - Tool calls: one `<details>` each; summary shows icon by `kind`, title, status; running/pending/failed expanded, completed collapsed; monospace body.
  - Assistant messages: markdown, always open.
  - Sticky-bottom autoscroll pausing on manual scroll-up; batch updates with `requestAnimationFrame`.
  - `QuestionCard`: pinned above the log when a question is pending; option buttons (radio/checkbox for multi) plus free-text input; submits `POST /api/runs/{id}/input`; disabled once answered.

## Error handling
- Missing binary → `HarnessError` exit 2 (`check_binary`).
- ACP init/auth failure → `HarnessError` with agent stderr tail; no secrets logged.
- Child dies mid-turn → run `failed`, pending question cleared.
- Unknown `--model`/`--effort` → validate against cached options when available, otherwise pass through with a stderr warning.

## Testing
- Unit: `AcpClient` against a fake ACP agent script (id correlation, chunks, defensive `request_permission` auto-allow, timeouts, malformed lines, `env.sh` env propagation); per-harness option parsers using recorded `omp`/`agy` `session/new` payloads; question extraction (`agy` `ask_question`, `omp` plain text → none); argv builders asserting yolo flags, `--model`/`--effort` flags, and `env.sh` wrapping across all modes (preview, headless, ACP, terminal, resume, bare terminal).
- Integration: `GET /api/harnesses/{name}/options` (cache, refresh, warning, 200 always); `POST /api/runs/{id}/input` (404/409/422/success); `test_specs_mirror_cli_flags`; end-to-end ACP run with fake agent.
- Frontend (vitest): `HarnessSelect` (only `omp` and `agy`), `RunModeSelect` (`preview`, `headless`, `acp`, `terminal`), `ModelSelect`/`EffortSelect` available in all 4 modes and disabled when empty, `AcpTranscript` collapse rules, `QuestionCard` submit.
- Isolation: all tests use `isolated_config` / `tmp_path` and never touch real `~/.config/workagent` or `~/.gemini/antigravity-cli`.

## Open items / risks
1. **`agy-guard` auth:** Relies on existing local credentials; unauthenticated runs must surface a clear `HarnessError`.
2. **`agy-guard` effort mapping:** `thought_level` is exposed both as a `configOption` and baked into `modelId` suffixes; effort select uses `thought_level` when present.
3. **`omp` model IDs:** Long provider paths (`bifrost/...`); UI displays `name` and submits `value`.
4. **ACP session resume (`session/load`):** Out of scope for this PR; can replace transcript-file resume later.
