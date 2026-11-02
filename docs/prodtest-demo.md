# Prodtest demo (fake repo, stub harness)

No real model, no real tracker, no prod access. `docs/prodtest-demo/stub_harness.py`
prints a canned `PRODTEST_RUN_RESULT:` line (the same contract a real harness
emits) and writes `results.yaml` into the run dir; the report below is rendered
by the vendored `render_report.py`.

Run it yourself:

```bash
D=$(mktemp -d)
python3 docs/prodtest-demo/stub_harness.py --mode pass --run-dir $D/pass
PYTHONPATH=src python3 src/workagent/prodtest_files/render_report.py $D/pass/results.yaml
python3 docs/prodtest-demo/stub_harness.py --mode fail --run-dir $D/fail
PYTHONPATH=src python3 src/workagent/prodtest_files/render_report.py $D/fail/results.yaml
```

Or run the automated version: `pytest tests/test_prodtest_demo.py -v`.

## PASS run

Stub stdout (first line is log noise; the parser takes the last `PRODTEST_RUN_RESULT:` line):

```text
stub harness (pass): running canned checks...
PRODTEST_RUN_RESULT: {"overall": "PASS", "results_yaml": "issue: DEMO-1\nrun_id: demo-pass-1\n..."}
```

Rendered report:

```markdown
<!-- workagent:prodtest-results v1 run=demo-pass-1 -->
## Production test ✅ **PASS** — DEMO-1
Run `demo-pass-1` · release `deadbee` · plan rev 1 · 2026-11-01 10:00:00+00:00 → 2026-11-01 10:04:00+00:00

**Version gate:** passed (observed `1.2.3`, waited 0s)

**Preconditions:** smoke-login ✅

| Case | Title | Verdict | Notes |
|---|---|---|---|
| C1 | Guest checkout totals include tax | ✅ PASS |  |
| C2 | Refund webhook retries once | ✅ PASS |  |

**Follow-up:** decision `none`
```

## FAIL run

Stub stdout:

```text
stub harness (fail): running canned checks...
PRODTEST_RUN_RESULT: {"overall": "FAIL", "results_yaml": "issue: DEMO-2\nrun_id: demo-fail-1\n..."}
```

Rendered report (FAIL case gets a triage section and a linked follow-up issue):

```markdown
<!-- workagent:prodtest-results v1 run=demo-fail-1 -->
## Production test ❌ **FAIL** — DEMO-2
Run `demo-fail-1` · release `deadbee` · plan rev 1 · 2026-11-01 10:00:00+00:00 → 2026-11-01 10:09:00+00:00

**Version gate:** passed (observed `1.2.3`, waited 0s)

**Preconditions:** smoke-login ✅

| Case | Title | Verdict | Notes |
|---|---|---|---|
| C1 | Guest checkout totals include tax | ✅ PASS |  |
| C2 | Refund webhook retries once | ❌ FAIL |  |

### Failures

#### C2 — Refund webhook retries once
- Verdict: **FAIL** · severity **high** · suspected **code** (confidence high)
- Reasoning: retry header missing on second attempt
- Step S1: expected `second POST returns 200` — actual `second POST returns 429`
  - evidence: run.log:429

**Follow-up:** decision `issue` · issue https://tracker.example/DEMO-2-fix
```

In the real flow, the FAIL report is posted as a (redacted) issue comment and
a follow-up issue/MR is filed per the plan manifest's `fix_policy`.
