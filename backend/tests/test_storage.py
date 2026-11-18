"""Per-organization storage locations (#15) — acceptance criterion 7.

GET reflects env + org_settings; PUT validates subtree/traversal/slug rules
and rejects out-of-root paths; changing a sources root with existing records
requires explicit confirm; file-based sources are confined to the org's
sources root; non-install admins can only see/manage their own org while
install admins can manage any org.

The suite shares one session-scoped database, so every test that mutates the
default org's storage restores the slug defaults afterwards (and deletes any
rows it seeded).
"""
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from main import app
from models import Record, Source


def _default_org(db_session) -> tuple[int, str]:
    row = db_session.execute(
        text("SELECT id, slug FROM organizations ORDER BY created_at LIMIT 1")
    ).one()
    return row[0], row[1]


def _restore_defaults(admin_client, oid: int, slug: str) -> None:
    r = admin_client.put(f"/api/storage/org/{oid}", json={
        "inbox_root": f"/inbox/{slug}",
        "sources_root": f"/sources/{slug}",
        "backup_path": f"/backups/{slug}",
        "confirm_sources_change": True,
    })
    assert r.status_code == 200, r.text


def _delete_row(db_session, oid: int, model, row_id: int) -> None:
    from database import set_org_context

    set_org_context(db_session, "admin", 1, oid)
    row = db_session.get(model, row_id)
    if row is not None:
        db_session.delete(row)
        db_session.commit()


def test_get_storage_install_roots_and_org_defaults(admin_client, db_session):
    r = admin_client.get("/api/storage")
    assert r.status_code == 200, r.text
    body = r.json()
    inst = {i["location"]: i for i in body["install"]}
    assert set(inst) == {"inbox", "sources", "backups"}
    assert inst["sources"]["container_path"] == "/sources"
    assert inst["inbox"]["container_path"] == "/inbox"
    assert inst["backups"]["container_path"] == "/backups"

    oid, slug = _default_org(db_session)
    org = next(o for o in body["orgs"] if o["id"] == oid)
    assert org["slug"] == slug
    assert org["storage"] == {
        "inbox": f"/inbox/{slug}",
        "sources": f"/sources/{slug}",
        "backups": f"/backups/{slug}",
    }
    for health in org["health"].values():
        assert {"path", "exists", "readable", "writable"} <= set(health)


def test_put_storage_custom_paths_and_inbox_sync(admin_client, db_session):
    oid, slug = _default_org(db_session)
    try:
        r = admin_client.put(f"/api/storage/org/{oid}", json={
            "inbox_root": f"/inbox/{slug}/scans",
            "backup_path": f"/backups/{slug}/dumps",
        })
        assert r.status_code == 200, r.text
        assert r.json()["storage"]["inbox"] == f"/inbox/{slug}/scans"

        body = admin_client.get("/api/storage").json()
        org = next(o for o in body["orgs"] if o["id"] == oid)
        assert org["storage"]["backup_path"] == f"/backups/{slug}/dumps"

        # The built-in Scan Inbox source follows the org's inbox root
        srcs = admin_client.get("/api/sources").json()
        builtin = [s for s in srcs if (s.get("config") or {}).get("builtin_inbox")]
        assert builtin, "expected a built-in inbox source"
        assert builtin[0]["config"]["root"] == f"/inbox/{slug}/scans"
    finally:
        _restore_defaults(admin_client, oid, slug)


def test_put_storage_rejects_invalid_paths(admin_client, db_session):
    oid, slug = _default_org(db_session)
    for payload in (
        {"sources_root": "/inbox/elsewhere"},             # wrong mount
        {"inbox_root": "/sources/elsewhere"},             # wrong mount
        {"sources_root": f"/sources/{slug}/../../etc"},   # traversal
        {"backup_path": "relative/path"},                 # not absolute
    ):
        r = admin_client.put(f"/api/storage/org/{oid}", json=payload)
        assert r.status_code == 422, (payload, r.text)


def test_sources_change_requires_confirm(admin_client, org_db, db_session):
    oid, slug = _default_org(db_session)
    src = org_db.execute(select(Source).where(Source.org_id == oid)).scalars().first()
    rec = Record(org_id=oid, source_id=src.id, path="x.pdf", name="x.pdf")
    org_db.add(rec)
    org_db.commit()
    try:
        r = admin_client.put(f"/api/storage/org/{oid}", json={"sources_root": f"/sources/{slug}/newroot"})
        assert r.status_code == 422
        assert "confirm_sources_change" in r.json()["detail"]

        r = admin_client.put(f"/api/storage/org/{oid}", json={
            "sources_root": f"/sources/{slug}/newroot",
            "confirm_sources_change": True,
        })
        assert r.status_code == 200, r.text
        assert any("old paths" in w for w in r.json()["warnings"])
    finally:
        _restore_defaults(admin_client, oid, slug)
        _delete_row(db_session, oid, Record, rec.id)


def test_file_sources_confined_to_org_root(admin_client, db_session):
    oid, slug = _default_org(db_session)
    r = admin_client.post("/api/sources", json={
        "name": "Sub-root test", "kind": "local",
        "config": {"root": f"/sources/{slug}/sub"},
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    try:
        r = admin_client.post("/api/sources", json={
            "name": "Outside test", "kind": "local",
            "config": {"root": "/sources/elsewhere"},
        })
        assert r.status_code == 422
        assert slug in r.json()["detail"]

        # Moving an existing source outside the org root is rejected...
        r = admin_client.patch(f"/api/sources/{sid}", json={
            "name": "Sub-root test", "kind": "local",
            "config": {"root": "/sources/elsewhere"},
        })
        assert r.status_code == 422, r.text
        # ...while an unrelated change with the same root is fine.
        r = admin_client.patch(f"/api/sources/{sid}", json={
            "name": "Renamed", "kind": "local",
            "config": {"root": f"/sources/{slug}/sub"},
        })
        assert r.status_code == 200, r.text
    finally:
        _delete_row(db_session, oid, Source, sid)


def test_cross_org_storage_authz(admin_client, db_session):
    oid, _ = _default_org(db_session)
    r = admin_client.post("/api/admin/orgs", json={"name": "Storage Org B", "kind": "business"})
    assert r.status_code == 201, r.text
    org_b = r.json()["id"]
    r = admin_client.post("/api/admin/users", json={
        "username": "orgbadmin", "password": "orgbpass1", "full_name": "Org B Admin",
        "role": "user", "org_id": org_b, "org_role": "admin",
    })
    assert r.status_code == 200, r.text

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "orgbadmin", "password": "orgbpass1"})
        assert r.status_code == 200, r.text
        if r.json().get("needs_org_selection"):
            sel = c.post("/api/auth/select-org", json={"org_id": org_b})
            assert sel.status_code == 200, sel.text

        # A non-install admin sees only their own org...
        body = c.get("/api/storage").json()
        assert [o["id"] for o in body["orgs"]] == [org_b]
        # ...cannot change another org's storage...
        r = c.put(f"/api/storage/org/{oid}", json={"backup_path": "/backups/x"})
        assert r.status_code == 403, r.text
        # ...but can change their own.
        r = c.put(f"/api/storage/org/{org_b}", json={"backup_path": "/backups/org-b-dumps"})
        assert r.status_code == 200, r.text

    # The install admin can manage any org.
    r = admin_client.put(f"/api/storage/org/{org_b}", json={"backup_path": "/backups/install-admin-set"})
    assert r.status_code == 200, r.text


def test_new_org_starter_storage(admin_client):
    r = admin_client.post("/api/admin/orgs", json={"name": "Fresh Storage Org"})
    assert r.status_code == 201, r.text
    oid, slug = r.json()["id"], r.json()["slug"]
    body = admin_client.get("/api/storage").json()
    org = next(o for o in body["orgs"] if o["id"] == oid)
    assert org["storage"]["inbox"] == f"/inbox/{slug}"
    assert org["storage"]["sources"] == f"/sources/{slug}"
    assert org["storage"]["backups"] == f"/backups/{slug}"


def test_probe_endpoint_shape(admin_client):
    r = admin_client.post("/api/storage/test", json={"location": "inbox"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["ok"], bool)
    assert body["path"].startswith("/inbox/")
    r = admin_client.post("/api/storage/test", json={"location": "bogus"})
    assert r.status_code == 422
