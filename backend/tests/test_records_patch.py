"""PATCH /api/records/{id}: omitted fields are unchanged; explicit null/'' clears."""
import uuid

import pytest
from sqlalchemy import select


@pytest.fixture()
def rec(db_session, admin_client):
    from database import set_actor
    from models import Box, Project, Record, RecordType, Source

    s = uuid.uuid4().hex[:8]
    # One transaction (with the audit actor set) so every insert is attributed.
    set_actor(db_session, "pytest")
    source = Source(name=f"patch-{s}", kind="local", config={"root": "/tmp"})
    proj = Project(code=f"PJ{s}", name="Proj")
    rtype = RecordType(code=f"RT{s}", name="Type", retention_years=7)
    box = Box(box_number=f"BX{s}", status="open")
    db_session.add_all([source, proj, rtype, box])
    db_session.flush()  # populate ids within this transaction
    record = Record(
        source_id=source.id, path=f"p-{s}.pdf", name=f"p-{s}.pdf",
        title="Original Title", project_id=proj.id, record_type_id=rtype.id,
        lot_number="LOT1", part_number="PN1", tags=["a", "b"], notes="some notes",
        status="active",
    )
    db_session.add(record)
    db_session.commit()
    return {"db": db_session, "client": admin_client, "id": record.id,
            "proj": proj.id, "rtype": rtype.id, "box": box.id}


def fetch(db, rid):
    from models import Record

    db.expire_all()  # force a re-read; the HTTP call committed in another session
    return db.execute(select(Record).where(Record.id == rid)).scalar_one()


def test_omitted_fields_unchanged(rec):
    r = fetch(rec["db"], rec["id"])
    resp = rec["client"].patch(f"/api/records/{r.id}", json={"title": "New Title"})
    assert resp.status_code == 200, resp.text
    r = fetch(rec["db"], rec["id"])
    assert r.title == "New Title"
    # everything else untouched
    assert r.project_id == rec["proj"]
    assert r.record_type_id == rec["rtype"]
    assert r.lot_number == "LOT1"
    assert r.part_number == "PN1"
    assert r.notes == "some notes"
    assert list(r.tags) == ["a", "b"]


def test_clear_fks_and_strings(rec):
    resp = rec["client"].patch(f"/api/records/{rec['id']}", json={
        "project_id": None, "record_type_id": None, "lot_number": "", "notes": None,
    })
    assert resp.status_code == 200, resp.text
    r = fetch(rec["db"], rec["id"])
    assert r.project_id is None
    assert r.record_type_id is None
    assert r.lot_number == ""
    assert r.notes == ""
    # an omitted field stays as-is
    assert r.part_number == "PN1"


def test_clear_tags(rec):
    resp = rec["client"].patch(f"/api/records/{rec['id']}", json={"tags": []})
    assert resp.status_code == 200, resp.text
    r = fetch(rec["db"], rec["id"])
    assert list(r.tags) == []


def test_file_then_unfile(rec):
    client = rec["client"]
    resp = client.patch(f"/api/records/{rec['id']}", json={"box_id": rec["box"]})
    assert resp.status_code == 200, resp.text
    r = fetch(rec["db"], rec["id"])
    assert r.box_id == rec["box"]

    resp = client.patch(f"/api/records/{rec['id']}", json={"box_id": None})
    assert resp.status_code == 200, resp.text
    r = fetch(rec["db"], rec["id"])
    assert r.box_id is None
