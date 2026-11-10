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


def test_rename_ok(tmp_path):
    from workagent import store_custom_actions as ca
    db = _db(tmp_path)
    ca.create_action(db, name="Foo", label="foo", prompt="p")
    renamed = ca.update_action(db, "foo", new_label="bar")
    assert renamed["label"] == "bar"
    assert ca.get_action(db, "foo") is None
    assert ca.get_action(db, "bar")["name"] == "Foo"


def test_rename_collision_rejected(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent.errors import HarnessError
    db = _db(tmp_path)
    ca.create_action(db, name="Foo", label="foo", prompt="p")
    ca.create_action(db, name="Bar", label="bar", prompt="p")
    try:
        ca.update_action(db, "foo", new_label="bar")
    except HarnessError as e:
        assert e.exit_code == 2
        assert "bar" in str(e)
    else:
        raise AssertionError("expected HarnessError")


def test_rename_invalid_label_rejected(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent.errors import HarnessError
    db = _db(tmp_path)
    ca.create_action(db, name="Foo", label="foo", prompt="p")
    for bad in ("Bad Label!", "HAS SPACE", ""):
        try:
            ca.update_action(db, "foo", new_label=bad)
        except (HarnessError, ValueError) as e:
            assert getattr(e, "exit_code", 2) == 2
        else:
            raise AssertionError(f"expected rejection for {bad!r}")


def test_action_display_columns(tmp_path):
    from workagent import store_custom_actions as ca
    db = _db(tmp_path)
    row = ca.create_action(db, name="Lint", prompt="lint it", protected=True,
                           sort_order=2, dedicated_top_bar=True,
                           dedicated_overlay=True)
    assert row["protected"] is True
    assert row["sort_order"] == 2
    assert row["dedicated_top_bar"] is True
    assert row["dedicated_overlay"] is True
    assert ca.get_action(db, row["label"])["protected"] is True


def test_actions_ordered_by_sort_order(tmp_path):
    from workagent import store_custom_actions as ca
    db = _db(tmp_path)
    ca.create_action(db, name="Second", prompt="p", sort_order=2)
    ca.create_action(db, name="First", prompt="p", sort_order=1)
    assert [r["label"] for r in ca.list_actions(db)] == ["first", "second"]


def test_action_category_round_trip(tmp_path):
    from workagent import store_custom_actions as ca
    db = _db(tmp_path)
    row = ca.create_action(db, name="Msg triage", prompt="p", category="message")
    assert row["category"] == "message"
    assert ca.get_action(db, row["label"])["category"] == "message"


def test_action_category_defaults_worktree(tmp_path):
    from workagent import store_custom_actions as ca
    db = _db(tmp_path)
    row = ca.create_action(db, name="Legacy", prompt="p")
    assert row["category"] == "worktree"


def test_action_category_rejects_bad_value(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent.errors import HarnessError
    db = _db(tmp_path)
    try:
        ca.create_action(db, name="X", prompt="p", category="sms")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")
