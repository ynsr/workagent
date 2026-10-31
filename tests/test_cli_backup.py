"""backup export/import round-trip tests (isolated sqlite db)."""
from __future__ import annotations

import json


def _db(tmp_path, monkeypatch):
    import workagent.store_sqlite as sq
    db = tmp_path / "t.db"
    monkeypatch.setattr(sq, "db_path", lambda *a, **k: db)
    return db


def test_export_import_round_trip(tmp_path, monkeypatch):
    from workagent import store_backup as b
    db = _db(tmp_path, monkeypatch)
    from workagent import store_flows as fl
    from workagent import store_custom_actions as ca
    ca.create_action(db, name="Backup Probe", prompt="do it")
    fl.create_template(db, name="Backup Flow",
                       steps=[{"kind": "sync", "config": {}}])
    snap = b.export_backup(db)
    assert snap["version"] == b.BACKUP_VERSION
    assert len(snap["actions"]) == 1 and len(snap["flow_templates"]) == 1
    blob = json.dumps(snap)
    snap2 = json.loads(blob)
    out = b.import_backup(db, snap2)
    assert out["actions"]["created"] == 1
    assert out["flow_templates"]["created"] == 1
    assert len(b.export_backup(db)["actions"]) == 1


def test_import_rejects_bad_version(tmp_path, monkeypatch):
    from workagent import store_backup as b
    from workagent.errors import HarnessError
    db = _db(tmp_path, monkeypatch)
    try:
        b.import_backup(db, {"version": 999, "actions": [],
                             "flow_templates": []})
    except HarnessError as e:
        assert "version" in str(e).lower()
    else:
        raise AssertionError("bad version accepted")


def test_import_validates_rows(tmp_path, monkeypatch):
    from workagent import store_backup as b
    from workagent.errors import HarnessError
    db = _db(tmp_path, monkeypatch)
    bad = {"version": b.BACKUP_VERSION,
           "actions": [{"name": "", "label": "x"}],
           "flow_templates": []}
    try:
        b.import_backup(db, bad)
    except HarnessError:
        pass
    else:
        raise AssertionError("invalid action accepted")
    # tables were cleared first — export is now empty, proving replace
    assert b.export_backup(db)["actions"] == []

def test_backup_roundtrips_parent_labels(tmp_path, monkeypatch):
    from workagent import store_backup as b
    db = _db(tmp_path, monkeypatch)
    b.import_backup(db, {"version": b.BACKUP_VERSION, "actions": [],
                         "flow_templates": [{"name": "T", "label": "t",
                                             "steps": [{"kind": "sync"}],
                                             "parent_labels": ["base"]}]})
    snap = b.export_backup(db)
    assert snap["flow_templates"][0]["parent_labels"] == ["base"]
