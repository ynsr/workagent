# ACP Harness — Design (issue #112)

## Goal
Add a generic ACP (Agent Client Protocol) harness type so workagent drives AI harnesses (`omp acp`, `agy-guard acp`) over stdio JSON-RPC. Runs stream structured output to the Run page (collapsible blocks), users pick harness/model/effort from ACP-discovered values, and users answer agent questions from the Run page.

## Decisions (from user)
- Keep legacy `Harness`, `OmpHarness`, `AgyHarness`. Default harness stays `agy` (agy-guard); Web UI harness select defaults to the configured default (`agy`).
- Both `OmpHarness` and `AgyHarness` implement `AcpHarness` (stubs where unsupported).
- YOLO everywhere: every harness run (ACP, non-TTY, interactive terminal) auto-approves. ACP: `omp acp` with `--approval-mode=yolo` (verify flag applies to `acp`), `agy-guard acp --dangerously-skip-permissions`; non-TTY keeps `omp -p --auto-approve` / `agy-guard --dangerously-skip-permissions`; TTY/terminal/resume argv gain `--approval-mode=yolo` (omp) and `--dangerously-skip-permissions` (agy). Because all runs auto-approve, `session/request_permission` is answered automatically with the allow option; no permission UI.
- New ACP harnesses are selectable in UI/actions: `OmpAcpHarness`, `AgyAcpHarness`. `AcpHarness` is the abstract base.
- `OmpHarness` additionally implements `AcpHarness` (stubs for unsupported methods) for backward compatibility.
- Run answers: `POST /api/runs/{id}/input` -> CLI child stdin -> ACP.
- Model/effort discovery: `GET /api/harnesses/{name}/options` with server-side TTL cache; input disabled when ACP returns no values.
- Rendering: frontend parses structured events into collapsible React blocks; raw log view stays available.
- Approach: pure-stdlib JSON-RPC client in a CLI child process (no new dependency, same process model as other web runs).

## Verified protocol facts (probed locally 2026-11-20)
| Aspect | `omp acp` | `agy-guard acp` |
|---|---|---|
| initialize | `protocolVersion:1` ok | ok |
| authenticate | required-ish; `methodId:"agent"` returns `{}` | `agent` rejected (-32602); methods are `oauth-*`/api key; `session/new` works without it |
| session/new `configOptions` | `mode`, `model` (category `model`), `thinking` (category `thought_level`: off..xhigh) | `model`, `thought_level` (low..xhigh), `mode` |
| `models` field | absent | `availableModels[{modelId,name}]` + `currentModelId`; effort is baked into ids (`gemini-3.8-flash-high`) |
| streaming | `session/update`: `agent_message_chunk`, `agent_thought_chunk`, `usage_update`, `available_commands_update`, `session_info_update` | same plus `tool_call` / `tool_call_update` (`name`, `args`, `status`, `kind`) |
| ask | no ask tool in ACP mode; agent asks in plain text, turn ends `end_turn` | `ask_question` tool_call with `rawInput.questions[{question,options,is_multi_select}]`; turn then ends `end_turn` |
| set option | `session/set_config_option {sessionId, configId, value}` (spec) | same |

Consequence: an agent question ends the current turn. The answer is sent as the next `session/prompt` in the same session (session stays alive while the child waits on stdin).

## Architecture
### Backend classes (`src/workagent/acp_harness.py`, new)
- `AcpClient`: stdio JSON-RPC transport. Line-delimited JSON, id correlation, reader thread, notification callback, handles agent->client requests (`session/request_permission`; fs/terminal capabilities not advertised). Errors -> `HarnessError`; timeout -> `HarnessTimeout`.
- `AcpHarness(Harness, abc.ABC)`: common flow `initialize -> authenticate (per-harness) -> session/new|load -> apply model/effort -> prompt turns -> close`. Abstract hooks: `acp_argv()`, `authenticate_method()`, `parse_options(session_new_result) -> HarnessOptions`, `extract_question(update) -> Question|None`, `set_model/set_effort` mapping (id of config option, or model-id suffix for agy).
- `OmpAcpHarness` (`name="omp-acp"`), `AgyAcpHarness` (`name="agy-acp"`): fine-tuning only.
- `OmpHarness(AcpHarness)` keeps TTY/`omp -p` behavior; ACP-only methods return "unsupported" (empty options, `supports_acp=False`). `AgyHarness` unchanged.
- `HarnessOptions {models:[{id,name}], efforts:[{id,name}], current_model, current_effort}`; empty list => UI disables that input.
- Default model/effort: when the user selects none, send no `set_config_option`; harness default applies.
- Registry: `HARNESSES["omp-acp"]`, `HARNESSES["agy-acp"]`; `config.default_harness` unchanged (`agy`). "Replace OmpHarness with OmpAcpHarness everywhere" is satisfied by routing web runs through ACP only when an ACP harness is selected (see Open items).

### Run execution
- CLI: `--harness omp-acp|agy-acp`, `--model`, `--effort` on every command that has `--harness` today (start, review, action run, flow run, adhoc, message *, prodtest *, release). Mirror in `web_args.py` `BOOL_FLAGS/VAL_FLAGS`, `SPECS`, `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`.
- ACP run child writes structured events as one JSON object per output line prefixed `ACP:` (e.g. `ACP:{"t":"thought","id":..,"text":..}`) alongside normal log lines so existing log storage/SSE/persistence work unchanged and raw view still works. Event types: `message`, `thought`, `tool_call` (id,name,kind,status,args,output), `usage`, `question` (qid, text, options, multi), `permission` (qid, options), `turn_end`.
- Question/permission: child emits `question`, then blocks reading stdin. `POST /api/runs/{id}/input {qid, answer|choice}` writes one JSON line to the child's stdin. Non-TTY/`--no-tty` unattended mode keeps current behavior (auto-approve permissions, no questions awaited: emit question, treat as `INCOMPLETE`/`WAITING_USER_RESPONSE`).
- Persistence: lines are already persisted per run; `GET /api/runs/{id}` replays them, so the structured view rebuilds from history.

### Web API
- `GET /api/harnesses/{name}/options?refresh=0|1` -> `{supported, models, efforts, current_model, current_effort, warning}`; probe = spawn `<bin> acp`, initialize, session/new in a temp cwd, read options, close; TTL cache (10 min), never raises (200 + `warning`), 5s timeout.
- `POST /api/runs/{id}/input` -> 404 unknown run, 409 not running or no pending question, 422 invalid body; validates `qid`.
- Host/Origin/Content-Type guard applies as for other mutating endpoints.

### Frontend
- Restore `HarnessSelect` in Launch page action forms and Run Flow modals/cards (ActionForm, FlowWizard, ChainEditor, ImmediateRunForm, ReleaseCard, StartForm, Prodtest/Message launch). Default `agy` (current default).
- New `ModelSelect`, `EffortSelect` driven by `useHarnessOptions(harness)`; disabled with tooltip when empty/unsupported/loading-failed; empty value = "Default".
- `RunDetail`: `AcpTranscript` parses `ACP:` lines into blocks; falls back to raw log lines for non-ACP runs; toggle Structured/Raw.
  - Thoughts: grouped in one collapsed `<details>` ("Thinking"), muted style.
  - Tool calls: one `<details>` each, summary shows icon by `kind`, title, status; running/pending/failed expanded, completed collapsed; stdout in monospace.
  - Assistant messages: markdown, always open.
  - Sticky-bottom autoscroll that pauses when the user scrolls up (reuse existing follow logic); batch updates via `requestAnimationFrame`.
- `QuestionCard`: shown pinned above the log when a question is pending; renders option buttons (radio/checkbox for multi) plus free-text box; submit -> `POST input`; permission requests render allow/deny options expanded and highlighted. Disabled once answered.

## Error handling
- Missing binary -> `HarnessError` exit 2 (existing `check_binary`).
- ACP init/auth failure -> `HarnessError` with agent stderr tail; no secrets logged.
- Child dies mid-turn -> run `failed`, pending question cleared.
- Unknown `--model/--effort` for the harness -> validate against options when available, otherwise pass through; warn on stderr.

## Testing
- Unit: `AcpClient` against a fake ACP agent script (stdin/stdout) covering id correlation, chunks, agent->client requests, timeouts, malformed lines; per-harness option parsers using the recorded omp/agy `session/new` payloads as fixtures; question extraction (agy tool_call, omp plain text -> none).
- Integration: web `options` endpoint (cache, warning, 200 always); `input` endpoint (404/409/422/success to a stub child); CLI flag ↔ `SPECS` drift test; run with a fake ACP harness end to end (`omp-acp` pointed at the fake binary via env).
- Frontend (vitest): `AcpTranscript` block rendering/collapse rules, `QuestionCard`, `ModelSelect/EffortSelect` disabled states, harness select present in each form.
- Isolation: tests use `isolated_config`/`tmp_path`; never touch real `~/.config/workagent` or `~/.gemini/antigravity-cli`.

## Open items / risks
1. "Replace OmpHarness with OmpAcpHarness everywhere" conflicts with "keep defaults / keep OmpHarness". Interpreted as: OmpHarness inherits AcpHarness and ACP harnesses are selectable; default stays `agy`. Resolved by user 2026-11-20.
2. Confirm yolo flags per harness/mode (`omp acp --approval-mode`, `omp` TTY, `agy-guard acp`); `request_permission` auto-allow still handled defensively.
3. agy auth: `authenticate` method list is OAuth/API-key; relying on existing local credentials. Unauthenticated agy -> clear error.
4. agy effort is both a `thought_level` option and baked into `modelId` suffixes; effort select uses `thought_level` when present, else none.
5. Model ids for omp are long provider paths; show `name`, send `value`.
6. ACP session resume (`session/load`) can replace transcript-file resume later; out of scope now.
