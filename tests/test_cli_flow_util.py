"""F1/F5 helper tests: run_mode + extra-flags translation, banned scanner."""
from __future__ import annotations


def test_normalize_run_mode_preview_and_terminal():
    from workagent import cli_flow_util as u
    assert u.normalize_config({"run_mode": "preview"}) == {"preview": True}
    assert u.normalize_config({"run_mode": "terminal"}) == {"terminal": True}
    assert u.normalize_config({"run_mode": "dry_run"}) == {"dry_run": True}
    # headless strips manual flags inherited from the template
    assert u.normalize_config({"run_mode": "headless", "preview": True}) == {}
    # no run_mode: passthrough
    assert u.normalize_config({"preview": True}) == {"preview": True}


def test_normalize_extra_flags_parses_kv_and_boolean():
    from workagent import cli_flow_util as u
    out = u.normalize_config({"extra-flags": "--depth 7 --merge"})
    assert out == {"depth": 7, "merge": True}
    out = u.normalize_config({"extra-flags": "--repo=main"})
    assert out == {"repo": "main"}
    # dashed keys normalize to the underscore names _pick allowlists
    out = u.normalize_config({"extra-flags": "--no-tty --related-repo proj --extra-prompt=hi"})
    assert out == {"no_tty": True, "related_repo": "proj", "extra_prompt": "hi"}
    # underscore variant accepted too
    out = u.normalize_config({"extra_flags": "--depth 7"})
    assert out == {"depth": 7}
    # combined with run_mode in one override
    out = u.normalize_config({"run_mode": "preview", "extra-flags": "--depth 7"})
    assert out == {"preview": True, "depth": 7}


def test_has_banned_scans_keys_and_values_without_blob_false_positives():
    from workagent import cli_flow_util as u
    from workagent import store_flows as sf
    # key hit
    assert u.has_banned({"then_fix_comments": True}, sf._BANNED_STEP_FLAGS)
    # nested value hit
    assert u.has_banned({"flags": "--then-fix-comments"}, sf._BANNED_STEP_FLAGS)
    # innocent prose mentioning extra_prompt must NOT trip
    assert u.has_banned({"extra_prompt": "then fix comments later"}, sf._BANNED_STEP_FLAGS) is None
    assert u.has_banned({"extra_prompt": "mention then-fix-comments"}, sf._BANNED_STEP_FLAGS) == "then-fix-comments"
    # cleanup flags
    assert u.has_banned({"hard_delete": True}, sf._BANNED_CLEANUP_FLAGS)
    assert u.has_banned({}, sf._BANNED_CLEANUP_FLAGS) is None
