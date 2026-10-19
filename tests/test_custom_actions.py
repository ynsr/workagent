"""Custom actions store tests (Task 1 TDD)."""
from pathlib import Path


def _db(tmp_path):
    from workagent import store_sqlite as sq
    db = Path(str(tmp_path)) / "state.db"
    sq.init_db(db)
    return db


def test_create_normalizes_label(tmp_path):
    from workagent import store_custom_actions as ca
    db = _db(tmp_path)
    row = ca.create_action(db, name="Generate Post PRD", prompt="Write it.")
    assert row["label"] == "generate_post_prd"
    assert row["icon"] == "Zap"
    assert row["created_at"] and row["modified_at"]

def test_blank_content_rejected(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent.errors import HarnessError
    db = _db(tmp_path)
    try:
        ca.create_action(db, name="Empty")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")

def test_explicit_label_validation(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent.errors import HarnessError
    db = _db(tmp_path)
    row = ca.create_action(db, name="X", label="my_action", prompt="p")
    assert row["label"] == "my_action"
    for bad in ("Bad Label!", "UPPER ok".strip().upper().replace(" ", "-"), "has space"):
        try:
            ca.create_action(db, name="Y", label=bad, prompt="p")
        except (HarnessError, ValueError):
            continue
        raise AssertionError(f"expected rejection for {bad!r}")

def test_bad_json_rejected(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent.errors import HarnessError
    db = _db(tmp_path)
    try:
        ca.create_action(db, name="J", prompt="p",
                         default_options="{not json")
    except ValueError as e:
        assert "JSON" in str(e)
    else:
        raise AssertionError("expected ValueError")

def test_modified_at_bump_and_roundtrip(tmp_path):
    from workagent import store_custom_actions as ca
    db = _db(tmp_path)
    row = ca.create_action(db, name="Bump", prompt="p")
    updated = ca.update_action(db, row["label"], prompt="p2")
    assert updated["prompt"] == "p2"
    assert updated["modified_at"] >= row["modified_at"]
    assert ca.get_action(db, row["label"])["prompt"] == "p2"
    assert len(ca.list_actions(db)) == 1
    ca.delete_action(db, row["label"])
    assert ca.get_action(db, row["label"]) is None

def test_label_collision_names_holder(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent.errors import HarnessError
    db = _db(tmp_path)
    ca.create_action(db, name="Foo Bar", prompt="p")
    try:
        ca.create_action(db, name="Foo-Bar", prompt="p")
    except HarnessError as e:
        assert "foo_bar" in str(e)
    else:
        raise AssertionError("expected HarnessError")
