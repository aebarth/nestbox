"""POST /api/records/bulk-patch: apply the same patch to many records."""
import uuid

import pytest
from sqlalchemy import text


@pytest.fixture()
def bulk_setup(org_db, default_org_id, admin_client):
    """Create a source, 3 inbox records, and a box for filing."""
    from models import Box, Record, Source

    s = uuid.uuid4().hex[:8]
    source = Source(org_id=default_org_id, name=f"bulk-{s}", kind="local", config={"root": "/tmp"})
    box = Box(org_id=default_org_id, box_number=f"BULK-{s}", status="open")
    org_db.add_all([source, box])
    org_db.flush()

    records = []
    for i in range(3):
        r = Record(
            org_id=default_org_id, source_id=source.id,
            path=f"bulk-{s}-{i}.pdf", name=f"bulk-{s}-{i}.pdf",
            title=f"Bulk doc {i}", status="inbox",
        )
        org_db.add(r)
        records.append(r)
    org_db.flush()
    org_db.commit()
    return {"db": org_db, "client": admin_client, "ids": [r.id for r in records],
            "box": box.id, "source": source.id}


def test_bulk_file_into_box(bulk_setup):
    """Select 3 inbox records → bulk-patch with box_id → all become active."""
    client = bulk_setup["client"]
    ids = bulk_setup["ids"]
    box = bulk_setup["box"]

    r = client.post("/api/records/bulk-patch", json={"ids": ids, "patch": {"box_id": box}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["updated"] == 3
    assert data["errors"] == []

    # Verify via API: all records are now active with the box assigned
    for rid in ids:
        rec = client.get(f"/api/records/{rid}").json()
        assert rec["status"] == "active"
        assert rec["box_id"] == box


def test_bulk_set_category(bulk_setup):
    """Bulk-patch with project/type sets categorization without changing status."""
    client = bulk_setup["client"]

    # Create a project and type via API (org-scoped)
    proj = client.post("/api/lookups/projects", json={"code": "BULK-P", "name": "Bulk Proj"}).json()
    rtype = client.post("/api/lookups/record-types", json={"code": "BULK-T", "name": "Bulk Type"}).json()

    r = client.post("/api/records/bulk-patch", json={
        "ids": bulk_setup["ids"],
        "patch": {"project_id": proj["id"], "record_type_id": rtype["id"]},
    })
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 3

    # Records still in inbox (no box assigned) but now categorized
    rec = client.get(f"/api/records/{bulk_setup['ids'][0]}").json()
    assert rec["status"] == "inbox"
    assert rec["project_id"] == proj["id"]
    assert rec["record_type_id"] == rtype["id"]


def test_bulk_partial_failure(bulk_setup):
    """Non-existent IDs are reported in errors; valid ones still update."""
    client = bulk_setup["client"]
    ids = bulk_setup["ids"]
    # Mix valid IDs with a non-existent one
    r = client.post("/api/records/bulk-patch", json={
        "ids": ids + [999999],
        "patch": {"title": "Bulk renamed"},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["updated"] == 3
    assert len(data["errors"]) == 1
    assert data["errors"][0]["id"] == 999999


def test_bulk_audit_rows_written(bulk_setup, org_db):
    """Each record in a bulk-patch gets its own audit_log row (via trigger)."""
    client = bulk_setup["client"]
    ids = bulk_setup["ids"]

    client.post("/api/records/bulk-patch", json={
        "ids": ids, "patch": {"title": "Audited bulk"},
    })

    # Count audit rows for the UPDATE action on these specific records
    org_db.expire_all()
    count = org_db.execute(text(
        "SELECT count(*) FROM audit_log WHERE table_name='records' AND action='UPDATE' "
        "AND row_pk = ANY(:ids)"
    ), {"ids": ids}).scalar()
    assert count >= 3  # at least one per record (could be more if multiple columns changed)
