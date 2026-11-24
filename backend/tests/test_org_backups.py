"""Org-level backup/restore (#15, PR B) — acceptance criteria 3 + 7.

An org backup is an org-pure export: restoring it into a fresh org
reproduces the data (fresh ids, remapped FKs) and touches nothing else on
the install. Full-DB backup/restore is unchanged (test_lifecycle.py).
"""
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import pytest

from main import app
from models import Box, Project, Record, Source
from services.org_backup import TABLE_ORDER


@pytest.fixture(autouse=True)
def backup_tmpdir(tmp_path, monkeypatch):
    """Org zips land in a per-test dir (CI has no /backups mount)."""
    d = tmp_path / "backups"
    monkeypatch.setenv("NESTBOX_BACKUP_PATH", str(d))
    return d


def _default_org(db_session) -> tuple[int, str]:
    row = db_session.execute(
        text("SELECT id, slug FROM organizations ORDER BY created_at LIMIT 1")
    ).one()
    return row[0], row[1]


def _counts(db_session, oid: int) -> dict:
    from database import set_org_context

    set_org_context(db_session, "admin", 1, oid)
    return {
        t: db_session.execute(
            text(f"SELECT count(*) FROM {t} WHERE org_id = :o"), {"o": oid}
        ).scalar()
        for t in TABLE_ORDER
    }


def test_org_backup_roundtrip_is_org_pure(admin_client, org_db, db_session):
    oid, _slug = _default_org(db_session)
    src = org_db.execute(select(Source).where(Source.org_id == oid)).scalars().first()
    p = Project(org_id=oid, code="P-RT-B1", name="Roundtrip Project")
    box = Box(org_id=oid, box_number="B-RT-1")
    org_db.add_all([p, box])
    org_db.flush()
    rec = Record(org_id=oid, source_id=src.id, path="rt.pdf", name="rt.pdf",
                 project_id=p.id, title="Roundtrip Doc")
    org_db.add(rec)
    org_db.commit()

    before = _counts(db_session, oid)

    r = admin_client.post(f"/api/storage/org/{oid}/backups", json={"notes": "roundtrip"})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert r.json()["sha256"] and r.json()["size_bytes"] > 0

    # The backup is listed for this org
    lst = admin_client.get(f"/api/storage/org/{oid}/backups").json()
    assert any(b["id"] == bid for b in lst)

    # Fresh target org (starter defaults only: record types + built-in inbox)
    r = admin_client.post("/api/admin/orgs", json={"name": "Restore Target Org"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    r = admin_client.post(f"/api/storage/org/{oid}/backups/{bid}/restore",
                          json={"target_org_id": tid, "confirm": True})
    assert r.status_code == 200, r.text

    after_target = _counts(db_session, tid)
    # Org-pure: document data + projects match the source org exactly...
    for t in ("records", "boxes", "duplicate_groups", "folder_rules",
              "scan_runs", "file_events", "alerts", "projects"):
        assert after_target[t] == before[t], (t, before[t], after_target[t])
    # ...sources 1:1 (the export's built-in inbox maps onto the target's own)...
    assert after_target["sources"] == before["sources"]
    # ...and record types merged with the target's starter rows.
    assert after_target["record_types"] >= max(before["record_types"], 7)

    # Content check: the seeded project + record arrived, FKs remapped.
    from database import set_org_context

    set_org_context(db_session, "admin", 1, tid)
    projs = db_session.execute(select(Project).where(Project.code == "P-RT-B1")).scalars().all()
    assert len(projs) == 1 and projs[0].org_id == tid
    recs = db_session.execute(select(Record).where(Record.name == "rt.pdf")).scalars().all()
    assert len(recs) == 1
    assert recs[0].org_id == tid and recs[0].project_id == projs[0].id

    # Source org untouched.
    assert _counts(db_session, oid) == before


def test_restore_requires_confirm_and_empty_target(admin_client, org_db, db_session):
    oid, _slug = _default_org(db_session)
    r = admin_client.post(f"/api/storage/org/{oid}/backups", json={})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]

    # confirm is required
    r = admin_client.post(f"/api/storage/org/{oid}/backups/{bid}/restore", json={})
    assert r.status_code == 422 and "confirm" in r.json()["detail"]

    # The default org holds document data → same-org restore refused (v1)
    src = org_db.execute(select(Source).where(Source.org_id == oid)).scalars().first()
    rec = Record(org_id=oid, source_id=src.id, path="empty-check.pdf", name="empty-check.pdf")
    org_db.add(rec)
    org_db.commit()
    r = admin_client.post(f"/api/storage/org/{oid}/backups/{bid}/restore", json={"confirm": True})
    assert r.status_code == 422 and "not empty" in r.json()["detail"].lower()

    # A full-DB backup row is not restorable through the org endpoint
    full = db_session.execute(
        text("SELECT id FROM backups WHERE org_id IS NULL LIMIT 1")
    ).scalar_one_or_none()
    if full is not None:
        r = admin_client.post(f"/api/storage/org/{oid}/backups/{full}/restore", json={"confirm": True})
        assert r.status_code == 404, r.text


def test_org_backup_authz(admin_client, db_session):
    oid, _ = _default_org(db_session)
    r = admin_client.post("/api/admin/orgs", json={"name": "Backup Org C"})
    assert r.status_code == 201, r.text
    org_c = r.json()["id"]
    r = admin_client.post("/api/admin/users", json={
        "username": "orgcadmin", "password": "orgcpass1", "full_name": "Org C Admin",
        "role": "user", "org_id": org_c, "org_role": "admin",
    })
    assert r.status_code == 200, r.text

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "orgcadmin", "password": "orgcpass1"})
        assert r.status_code == 200, r.text
        if r.json().get("needs_org_selection"):
            sel = c.post("/api/auth/select-org", json={"org_id": org_c})
            assert sel.status_code == 200, sel.text

        # Own org: list (empty) + create work...
        assert c.get(f"/api/storage/org/{org_c}/backups").json() == []
        r = c.post(f"/api/storage/org/{org_c}/backups", json={})
        assert r.status_code == 200, r.text
        # ...another org's backups are out of reach.
        assert c.get(f"/api/storage/org/{oid}/backups").status_code == 403
        assert c.post(f"/api/storage/org/{oid}/backups", json={}).status_code == 403


def test_replace_restore_wipes_target_document_data(admin_client, org_db, db_session):
    """replace=true wipes the target's existing document data + projects,
    then fills from the backup; without it the non-empty target is refused."""
    from database import set_org_context

    oid, _slug = _default_org(db_session)
    src = org_db.execute(select(Source).where(Source.org_id == oid)).scalars().first()
    p = Project(org_id=oid, code="P-REP-1", name="Replace Project")
    org_db.add(p)
    org_db.flush()
    rec = Record(org_id=oid, source_id=src.id, path="rep.pdf", name="rep.pdf", project_id=p.id)
    org_db.add(rec)
    org_db.commit()

    r = admin_client.post(f"/api/storage/org/{oid}/backups", json={})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]

    # Target org seeded with junk document data (what replace must wipe)
    r = admin_client.post("/api/admin/orgs", json={"name": "Replace Target Org"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    set_org_context(db_session, "admin", 1, tid)
    tsrc = db_session.execute(select(Source).where(Source.org_id == tid)).scalars().first()
    jp = Project(org_id=tid, code="J-1", name="Junk Project")
    jb = Box(org_id=tid, box_number="JUNK-1")
    db_session.add_all([jp, jb])
    db_session.flush()
    db_session.add(Record(org_id=tid, source_id=tsrc.id, path="junk.pdf", name="junk.pdf",
                          project_id=jp.id, box_id=jb.id))
    db_session.commit()

    # Without replace: refused (non-empty target) — the v1 guardrail holds
    r = admin_client.post(f"/api/storage/org/{oid}/backups/{bid}/restore",
                          json={"target_org_id": tid, "confirm": True})
    assert r.status_code == 422 and "not empty" in r.json()["detail"].lower()

    # With replace: junk wiped, backup data filled in with remapped FKs
    r = admin_client.post(f"/api/storage/org/{oid}/backups/{bid}/restore",
                          json={"target_org_id": tid, "confirm": True, "replace": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replaced"] is True and body["deleted_counts"].get("records") == 1

    # db_session is RLS-scoped to the target org: only its rows are visible.
    codes = {p.code for p in db_session.execute(select(Project)).scalars().all()}
    names = {r.name for r in db_session.execute(select(Record)).scalars().all()}
    assert "J-1" not in codes and "P-REP-1" in codes
    assert "junk.pdf" not in names and "rep.pdf" in names
    # The junk box must be gone (the wipe worked). We can't assert an absolute
    # count of 0 because the session-scoped database means the backup may
    # legitimately contain boxes created by earlier tests.
    box_numbers = {b.box_number for b in db_session.execute(select(Box)).scalars().all()}
    assert "JUNK-1" not in box_numbers


def test_install_level_restore_rejects_org_backup(admin_client, org_db, db_session):
    """An org zip must never reach the install-level pg_restore path — that
    would drop the live schema over a user error."""
    from database import set_org_context

    oid, _slug = _default_org(db_session)
    r = admin_client.post(f"/api/storage/org/{oid}/backups", json={})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]

    def _record_count() -> int:
        set_org_context(db_session, "admin", 1, oid)
        return db_session.execute(
            text("SELECT count(*) FROM records WHERE org_id = :o"), {"o": oid}
        ).scalar()

    before = _record_count()

    r = admin_client.post(f"/api/admin/backups/{bid}/restore", json={"confirm": "RESTORE"})
    assert r.status_code >= 400, r.text
    assert "organization backup" in r.json()["detail"].lower()

    # The live database is untouched — no schema drop may have happened.
    db_session.execute(text("SELECT 1 FROM organizations LIMIT 1")).scalar_one()
    assert _record_count() == before
