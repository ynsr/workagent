def test_validate_smoke_plan(tmp_path):
    from workagent import prodtest_scripts as ps
    plan = tmp_path / "p.yaml"
    man = tmp_path / "m.yaml"
    plan.write_text("plan_version: 1\nissue: IPG-1\nsummary: s\nrisk: {level: low}\nchange_kinds: [refactor]\nbudget: {max_cases: 3, max_minutes: 15}\nnot_covered: []\nsmoke: []\ncases: []\n")
    out = ps.run_validate(str(plan), None)
    assert out["valid"] is False  # no cases and no smoke refs
    assert any("neither cases" in e for e in out["errors"])
