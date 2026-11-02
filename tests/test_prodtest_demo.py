"""Demo: stub harness emitting canned PRODTEST_RUN_RESULT lines renders reports.

Uses docs/prodtest-demo/stub_harness.py (a fake harness that prints a
canned PRODTEST_RUN_RESULT line instead of calling a real model).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent / "docs" / "prodtest-demo"


def _run_stub(mode: str, run_dir: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(DEMO_DIR / "stub_harness.py"), "--mode", mode,
         "--run-dir", str(run_dir)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_demo_runs_pass_and_fail(tmp_path):
    from workagent import prodtest_scripts as ps
    from workagent.prodtest_result import parse_result_line

    pass_dir = tmp_path / "pass"
    pass_dir.mkdir()
    stdout = _run_stub("pass", pass_dir)
    result = parse_result_line(stdout, str(pass_dir), "PRODTEST_RUN")
    assert result["overall"] == "PASS"
    results_yaml = pass_dir / "results.yaml"
    results_yaml.write_text(result["results_yaml"], encoding="utf-8")
    report = ps.render(str(results_yaml))
    assert "✅" in report and "PASS" in report

    fail_dir = tmp_path / "fail"
    fail_dir.mkdir()
    stdout = _run_stub("fail", fail_dir)
    result = parse_result_line(stdout, str(fail_dir), "PRODTEST_RUN")
    assert result["overall"] == "FAIL"
    results_yaml = fail_dir / "results.yaml"
    results_yaml.write_text(result["results_yaml"], encoding="utf-8")
    report = ps.render(str(results_yaml))
    assert "❌" in report and "FAIL" in report
    assert "Follow-up" in report
