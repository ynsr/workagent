#!/usr/bin/env python3
"""Stub harness for the prodtest demo: emits canned PRODTEST_RUN_RESULT lines.

Instead of calling a real model, prints a canned result line plus writes
results.yaml into --run-dir. --mode pass renders an all-green report;
--mode fail renders one FAIL case with triage + fix follow-up.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PASS_YAML = """\
issue: DEMO-1
run_id: demo-pass-1
release_sha: deadbee
plan_revision: 1
overall: PASS
started_at: 2026-11-01T10:00:00Z
finished_at: 2026-11-01T10:04:00Z
version_gate: {status: passed, observed: "1.2.3", waited_s: 0}
preconditions: [{id: smoke-login, verdict: PASS}]
cases:
  - {id: C1, title: Guest checkout totals include tax, verdict: PASS, steps: []}
  - {id: C2, title: Refund webhook retries once, verdict: PASS, steps: []}
not_covered: []
fix: {decision: none}
"""

FAIL_YAML = """\
issue: DEMO-2
run_id: demo-fail-1
release_sha: deadbee
plan_revision: 1
overall: FAIL
started_at: 2026-11-01T10:00:00Z
finished_at: 2026-11-01T10:09:00Z
version_gate: {status: passed, observed: "1.2.3", waited_s: 0}
preconditions: [{id: smoke-login, verdict: PASS}]
cases:
  - {id: C1, title: Guest checkout totals include tax, verdict: PASS, steps: []}
  - {id: C2, title: Refund webhook retries once, verdict: FAIL,
     steps: [{id: S1, verdict: FAIL, expected: "second POST returns 200",
              actual: "second POST returns 429", evidence: "run.log:429"}],
     triage: {severity: high, suspect: code, confidence: high,
              reasoning: "retry header missing on second attempt"}}
not_covered: []
fix: {decision: issue, issue_url: "https://tracker.example/DEMO-2-fix"}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("pass", "fail"), required=True)
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    yaml_text = PASS_YAML if args.mode == "pass" else FAIL_YAML
    (run_dir / "results.yaml").write_text(yaml_text, encoding="utf-8")
    payload = {
        "overall": "PASS" if args.mode == "pass" else "FAIL",
        "results_yaml": yaml_text,
    }
    print(f"stub harness ({args.mode}): running canned checks...")
    print("PRODTEST_RUN_RESULT: " + json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
