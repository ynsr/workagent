# ACP Harness — Implementation Plan (issue #112)

Spec: `docs/superpowers/specs/2026-11-20-acp-harness-design.md`. TDD throughout (failing test, then code). Run `uv run pytest -v` and `cd web && npm test` after each step.

1. **Fake ACP agent fixture** → verify: `tests/fixtures/fake_acp_agent.py` speaks initialize/auth/session/new/prompt with chunks, a tool_call, an ask question; fixture payloads from recorded omp/agy `session/new` responses.
2. **`AcpClient` transport** (`acp_harness.py`) → verify: unit tests for id correlation, notifications, agent->client requests, timeout (`HarnessTimeout`), malformed lines, process exit.
3. **Yolo flags**: probe `omp acp --approval-mode=yolo`, `omp --approval-mode=yolo`, `agy-guard acp --dangerously-skip-permissions`; record `session/request_permission` payload → verify: flags accepted; auto-allow fixture added.
4. **`AcpHarness` base + `HarnessOptions`** → verify: lifecycle test (init, auth, session/new, set options, prompt turns, close) against the fake agent; default model/effort sends no `set_config_option`.
5. **`OmpAcpHarness`, `AgyAcpHarness`** (auth method, option parsing, question extraction) → verify: fixture-driven parser tests; omp plain-text question yields no `question` event.
6. **`OmpHarness` and `AgyHarness` implement `AcpHarness` (stubs), add yolo flags to ACP/non-TTY/TTY/resume/bare-terminal argv, registry entries `omp-acp`/`agy-acp`** → verify: existing harness tests unchanged and green; `get_harness` lists new names; default harness stays `agy`; tests assert every argv carries the yolo flag.
7. **Structured event emitter + ACP run loop** (`ACP:` JSON lines, stdin answer reader, unattended mode) → verify: run test through fake agent: events ordered, question blocks until stdin answer, `--no-tty` does not block.
8. **CLI flags `--model/--effort`** on all `--harness` commands; mirror in `web_args.py` (`BOOL_FLAGS/VAL_FLAGS`, `SPECS`), `API_CONTRACT.md`, `FEATURE_INVENTORY.md` → verify: `test_specs_mirror_cli_flags` and CLI tests green.
9. **`GET /api/harnesses/{name}/options`** with TTL cache and probe → verify: tests for cache hit/refresh, missing binary warning, always 200, timeout.
10. **`POST /api/runs/{id}/input`** + stdin plumbing in run registry → verify: 404/409/422/success tests; pending question cleared on child exit.
11. **Frontend data layer**: `useHarnessOptions`, api types, `ModelSelect`, `EffortSelect` → verify: vitest disabled/empty/loading states.
12. **Restore harness + model + effort inputs** in Launch action forms, FlowWizard, ChainEditor, ImmediateRunForm, ReleaseCard, StartForm, Prodtest/Message launch; default `agy` → verify: existing form tests plus new assertions that args include `--harness/--model/--effort` only when set.
13. **`AcpTranscript` + `QuestionCard` in `RunDetail`** (collapsible thoughts/tools, autoscroll, Structured/Raw toggle, answer submit) → verify: vitest rendering/collapse rules, answer posts `POST input`.
14. **Integration test** run page flow end to end with fake ACP agent (start run, stream, answer question, finish) → verify: pytest + manual browser check via `workagent serve`.
15. **Docs/changelog**: AGENTS.md architecture table, `CHANGELOG.md` line `[112]`, `web/API_CONTRACT.md` → verify: `git diff` CHANGELOG shows only `+` lines.
16. **Review + reinstall**: sub-agent diff review, full test suites, `./install.sh` → verify: all green, `workagent doctor` ok.
