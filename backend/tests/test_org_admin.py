"""Organization management API tests (#10): org CRUD + member management.

organizations / org_members are install-level tables (no RLS) and outside the
trigger-based audit loop, so these tests exercise the admin API directly and
verify per-org starter data through a session scoped to the new org.
"""
import pytest
from sqlalchemy import select


def _create_org(client, name: str, kind: str = "personal") -> dict:
    r = client.post("/api/admin/orgs", json={"name": name, "kind": kind})
    assert r.status_code == 201, r.text
    return r.json()


def test_list_orgs_includes_default(admin_client, default_org_id):
    orgs = admin_client.get("/api/admin/orgs").json()
    match = [o for o in orgs if o["id"] == default_org_id]
    assert len(match) == 1
    assert match[0]["member_count"] >= 1
    assert match[0]["is_active"] is True


def test_create_org_creates_starter_defaults(admin_client, db_session):
    from database import set_org_context
    from models import RecordType, Source

    org = _create_org(admin_client, name="Defaults Org")
    assert org["slug"] == "defaults-org"

    # Read the new org's starter data through an RLS-scoped session.
    set_org_context(db_session, "admin", 1, org["id"])
    db_session.expire_all()
    types = db_session.execute(select(RecordType)).scalars().all()
    assert {t.code for t in types} == {"DHR", "DMR", "TRN", "CAL", "SUP", "CAPA", "GEN"}
    inbox = db_session.execute(
        select(Source).where(Source.name == "Scan Inbox")
    ).scalars().all()
    assert len(inbox) == 1
    assert inbox[0].config.get("builtin_inbox") is True


def test_create_org_slug_unique(admin_client):
    a = _create_org(admin_client, name="Acme Corp")
    b = _create_org(admin_client, name="Acme Corp")
    assert a["slug"] == "acme-corp"
    assert b["slug"] != a["slug"]


def test_get_org_members(admin_client, default_org_id):
    detail = admin_client.get(f"/api/admin/orgs/{default_org_id}").json()
    assert detail["id"] == default_org_id
    roles = {m["username"]: m["role"] for m in detail["members"]}
    assert roles.get("admin") == "owner"


def test_update_org_and_last_active_guard(admin_client, default_org_id):
    org = _create_org(admin_client, name="Guard Org")

    # rename + kind change
    r = admin_client.patch(f"/api/admin/orgs/{org['id']}", json={"name": "Guard Org 2", "kind": "business"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Guard Org 2"
    assert r.json()["kind"] == "business"
    # slug is stable across renames (it identifies the org's file subtree)
    assert r.json()["slug"] == org["slug"]

    # deactivating the new org is fine — the default stays active
    r = admin_client.patch(f"/api/admin/orgs/{org['id']}", json={"is_active": False})
    assert r.status_code == 200, r.text
    try:
        # deactivating the LAST active org is refused
        r = admin_client.patch(f"/api/admin/orgs/{default_org_id}", json={"is_active": False})
        assert r.status_code == 422, r.text
    finally:
        # restore state for the rest of the suite regardless of outcome
        admin_client.patch(f"/api/admin/orgs/{default_org_id}", json={"is_active": True})
        admin_client.patch(f"/api/admin/orgs/{org['id']}", json={"is_active": True})


def test_member_lifecycle(admin_client, default_org_id):
    org = _create_org(admin_client, name="Members Org")
    oid = org["id"]

    # a user with no org membership (cannot log in until added somewhere)
    r = admin_client.post("/api/admin/users", json={
        "username": "member-carol", "password": "carolpass123",
        "full_name": "Carol", "role": "user",
    })
    assert r.status_code == 200, r.text
    carol_id = r.json()["id"]

    # add by username
    r = admin_client.post(f"/api/admin/orgs/{oid}/members", json={"username": "member-carol", "role": "member"})
    assert r.status_code == 201, r.text
    # duplicate → 409
    r = admin_client.post(f"/api/admin/orgs/{oid}/members", json={"user_id": carol_id})
    assert r.status_code == 409, r.text
    # unknown user → 404
    r = admin_client.post(f"/api/admin/orgs/{oid}/members", json={"username": "no-such-user-xyz"})
    assert r.status_code == 404, r.text
    # neither identifier → 422
    r = admin_client.post(f"/api/admin/orgs/{oid}/members", json={"role": "member"})
    assert r.status_code == 422, r.text

    # promote carol to owner (org now has two owners: admin + carol)
    r = admin_client.patch(f"/api/admin/orgs/{oid}/members/{carol_id}", json={"role": "owner"})
    assert r.status_code == 200, r.text

    detail = admin_client.get(f"/api/admin/orgs/{oid}").json()
    admin_member = next(m for m in detail["members"] if m["username"] == "admin")

    # demoting the original owner is allowed while carol remains an owner
    r = admin_client.patch(
        f"/api/admin/orgs/{oid}/members/{admin_member['user_id']}", json={"role": "member"}
    )
    assert r.status_code == 200, r.text

    # carol is now the ONLY owner → removal refused
    r = admin_client.delete(f"/api/admin/orgs/{oid}/members/{carol_id}")
    assert r.status_code == 422, r.text

    # self-removal refused (default org, where admin is still owner)
    r = admin_client.delete(f"/api/admin/orgs/{default_org_id}/members/{admin_member['user_id']}")
    assert r.status_code == 422, r.text


def test_org_changes_are_audited(admin_client, db_session, default_org_id):
    from sqlalchemy import text

    from database import set_org_context

    org = _create_org(admin_client, name="Audited Org")

    # install-level audit rows (org_id NULL) are visible to any scoped session
    set_org_context(db_session, "admin", 1, default_org_id)
    rows = db_session.execute(
        text("SELECT action, new_row FROM audit_log WHERE table_name = 'organizations' AND row_pk = :pk"),
        {"pk": org["id"]},
    ).all()
    assert any(r.new_row.get("op") == "create_org" and r.action == "INSERT" for r in rows), \
        f"no create_org audit row: {[(r.action, r.new_row) for r in rows]}"


def test_org_admin_endpoints_require_install_admin(admin_client, default_org_id):
    from fastapi.testclient import TestClient

    from main import app

    # a regular (non-admin) user — org member, not install admin
    r = admin_client.post("/api/admin/users", json={
        "username": "org-bob", "password": "bobpass123",
        "full_name": "Bob", "role": "user",
        "org_id": default_org_id, "org_role": "member",
    })
    assert r.status_code == 200, r.text

    with TestClient(app) as bob:
        login = bob.post("/api/auth/login", json={"username": "org-bob", "password": "bobpass123"})
        assert login.status_code == 200, login.text
        assert bob.get("/api/admin/orgs").status_code == 403
        assert bob.post("/api/admin/orgs", json={"name": "Nope"}).status_code == 403
