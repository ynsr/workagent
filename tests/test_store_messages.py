"""tests/test_store_messages.py — messages CRUD (Task 1 TDD)."""
def test_create_and_get_round_trip(tmp_path):
    from workagent import store_sqlite as sq
    from workagent import store_messages as sm
    db = tmp_path / "s.db"
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                 ("r1", "/tmp/r1", "r1"))
    conn.commit()
    row = sm.create_message(db, message_id="p1", app_name="mattermost",
                            sender_username="u", content="hello", ts="2026-11-10T00:00:00+00:00",
                            repos=["r1"])
    assert row["message_id"] == "p1"
    assert row["repos"] == ["r1"]
    assert sm.get_message(db, "p1")["content"] == "hello"


def test_create_requires_known_repo(tmp_path):
    from workagent import store_sqlite as sq
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    sq.init_db(db)
    try:
        sm.create_message(db, message_id="p9", repos=["nope"])
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")


def _seed_repo(db):
    from workagent import store_sqlite as sq
    sq.init_db(db)
    conn = sq.connect(db)
    conn.execute("INSERT OR IGNORE INTO repos (key_ref, path, name) VALUES (?, ?, ?)",
                 ("r1", "/tmp/r1", "r1"))
    conn.commit()


def test_duplicate_message_id_rejected(tmp_path):
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    _seed_repo(db)
    sm.create_message(db, message_id="p1", repos=["r1"])
    try:
        sm.create_message(db, message_id="p1", repos=["r1"])
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")


def test_bad_app_name_rejected(tmp_path):
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    _seed_repo(db)
    try:
        sm.create_message(db, message_id="p1", app_name="slack", repos=["r1"])
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")


def test_unknown_update_field_rejected(tmp_path):
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    _seed_repo(db)
    sm.create_message(db, message_id="p1", repos=["r1"])
    try:
        sm.update_message(db, "p1", bogus=1)
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")


def test_thread_listing_and_ambiguity(tmp_path):
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    _seed_repo(db)
    sm.create_message(db, message_id="p1", thread_id="t1", content="alpha hello", repos=["r1"])
    sm.create_message(db, message_id="p2", thread_id="t1", content="alpha world", repos=["r1"])
    sm.create_message(db, message_id="p3", thread_id="t2", content="other", repos=["r1"])
    assert len(sm.list_messages(db, "t1")) == 2
    assert len(sm.list_messages(db)) == 3
    try:
        sm.resolve_message(db, "alpha")
    except HarnessError as e:
        assert e.exit_code == 2 and "ambiguous" in str(e).lower()
    else:
        raise AssertionError("expected HarnessError")
    try:
        sm.resolve_message(db, "zzz-nope")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")


def test_create_rejects_empty_repos_and_blank_id(tmp_path):
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    _seed_repo(db)
    for bad in ([], ["nope"]):
        try:
            sm.create_message(db, message_id="px", repos=bad)
        except HarnessError as e:
            assert e.exit_code == 2
        else:
            raise AssertionError(f"expected HarnessError for {bad!r}")
    for blank in ("", "   "):
        try:
            sm.create_message(db, message_id=blank, repos=["r1"])
        except HarnessError as e:
            assert e.exit_code == 2
        else:
            raise AssertionError("expected HarnessError for blank id")


def test_update_rename_guards(tmp_path):
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    _seed_repo(db)
    sm.create_message(db, message_id="p1", repos=["r1"])
    sm.create_message(db, message_id="p2", repos=["r1"])
    for bad in ("", "   "):
        try:
            sm.update_message(db, "p1", new_message_id=bad)
        except HarnessError as e:
            assert e.exit_code == 2
        else:
            raise AssertionError("expected HarnessError for blank rename")
    try:
        sm.update_message(db, "p1", new_message_id="p2")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError for duplicate rename")
    try:
        sm.update_message(db, "p1", app_name="slack")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError for bad app")
    try:
        sm.update_message(db, "p1", repos=["nope"])
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError for bad repos")
    assert sm.update_message(db, "p1", new_message_id="  p3  ")["message_id"] == "p3"


def test_delete_unknown_and_round_trip(tmp_path):
    from workagent import store_messages as sm
    from workagent.errors import HarnessError
    db = tmp_path / "s.db"
    _seed_repo(db)
    sm.create_message(db, message_id="p1", repos=["r1"])
    try:
        sm.delete_message(db, "nope")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")
    sm.delete_message(db, "p1")
    assert sm.get_message(db, "p1") is None
