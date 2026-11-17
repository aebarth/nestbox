"""Organization management API tests (#10): org CRUD + member management.

organizations / org_members are install-level tables (no RLS) and outside the
trigger-based audit loop, so these tests exercise the admin API directly and
verify per-org starter data through a session scoped to the new org.

#17: every org created via the API makes the ACTING install-admin its owner,
so this file drives all /api/admin/orgs* calls with a dedicated throwaway
install-admin (`orgadmin`) instead of the shared `admin` account — otherwise
`admin` would end up a member of ~7 scratch orgs for the rest of the suite
and every later login would depend on the multi-org picker being resolved.
"""
import pytest
from sqlalchemy import select


@pytest.fixture()
def orgadmin_client(admin_client, default_org_id):
    """A TestClient logged in as `orgadmin` — a dedicated install-admin that
    absorbs this file's scratch-org ownership instead of the shared admin.

    Created once (409 on later tests in this file). From the second test on it
    owns several scratch orgs (that is its job), so its login goes through the
    multi-org picker; the fixture resolves it deterministically to the default
    org. This is fine — #17's invariant is that the SHARED `admin` account
    stays single-org, not that orgadmin does.
    """
    from fastapi.testclient import TestClient

    from main import app

    r = admin_client.post("/api/admin/users", json={
        "username": "orgadmin", "password": "orgadminpass123",
        "full_name": "Org Admin", "role": "admin",
        "org_id": default_org_id, "org_role": "member",
    })
    assert r.status_code in (200, 409), r.text  # 409: created by an earlier test here

    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": "orgadmin", "password": "orgadminpass123"})
        assert login.status_code == 200, login.text
        if login.json().get("needs_org_selection"):
            sel = c.post("/api/auth/select-org", json={"org_id": default_org_id})
            assert sel.status_code == 200, sel.text
        yield c


def _create_org(client, name: str, kind: str = "personal") -> dict:
    r = client.post("/api/admin/orgs", json={"name": name, "kind": kind})
    assert r.status_code == 201, r.text
    return r.json()


def test_list_orgs_includes_default(orgadmin_client, default_org_id):
    orgs = orgadmin_client.get("/api/admin/orgs").json()
    match = [o for o in orgs if o["id"] == default_org_id]
    assert len(match) == 1
    assert match[0]["member_count"] >= 1
    assert match[0]["is_active"] is True


def test_create_org_creates_starter_defaults(orgadmin_client, db_session):
    from database import set_org_context
    from models import RecordType, Source

    org = _create_org(orgadmin_client, name="Defaults Org")
    assert org["slug"] == "defaults-org"
    # the acting install-admin (orgadmin) is added as owner — an org with no
    # members is unusable
    assert org["member_count"] == 1

    # Read the new org's starter data through an RLS-scoped session.
    set_org_context(db_session, "orgadmin", 1, org["id"])
    db_session.expire_all()
    types = db_session.execute(select(RecordType)).scalars().all()
    assert {t.code for t in types} == {"DHR", "DMR", "TRN", "CAL", "SUP", "CAPA", "GEN"}
    inbox = db_session.execute(
        select(Source).where(Source.name == "Scan Inbox")
    ).scalars().all()
    assert len(inbox) == 1
    assert inbox[0].config.get("builtin_inbox") is True


def test_create_org_slug_unique(orgadmin_client):
    a = _create_org(orgadmin_client, name="Acme Corp")
    b = _create_org(orgadmin_client, name="Acme Corp")
    assert a["slug"] == "acme-corp"
    assert b["slug"] != a["slug"]


def test_get_org_members(orgadmin_client, default_org_id):
    detail = orgadmin_client.get(f"/api/admin/orgs/{default_org_id}").json()
    assert detail["id"] == default_org_id
    roles = {m["username"]: m["role"] for m in detail["members"]}
    # the shared admin stays owner of the default org, untouched by this file
    assert roles.get("admin") == "owner"


def test_update_org_and_last_active_guard(orgadmin_client, default_org_id):
    org = _create_org(orgadmin_client, name="Guard Org")

    # rename + kind change
    r = orgadmin_client.patch(f"/api/admin/orgs/{org['id']}", json={"name": "Guard Org 2", "kind": "business"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Guard Org 2"
    assert r.json()["kind"] == "business"
    # slug is stable across renames (it identifies the org's file subtree)
    assert r.json()["slug"] == org["slug"]

    # The last-active guard is install-wide, and earlier tests in this shared
    # session DB have created other active orgs — deactivate every active org
    # except the default so the guard is actually reachable.
    all_orgs = orgadmin_client.get("/api/admin/orgs").json()
    others = [
        o for o in all_orgs
        if o["id"] not in (default_org_id, org["id"]) and o["is_active"]
    ]
    for o in others:
        r = orgadmin_client.patch(f"/api/admin/orgs/{o['id']}", json={"is_active": False})
        assert r.status_code == 200, r.text
    try:
        # deactivating the new org leaves exactly one active (the default)
        r = orgadmin_client.patch(f"/api/admin/orgs/{org['id']}", json={"is_active": False})
        assert r.status_code == 200, r.text
        # deactivating the LAST active org is refused
        r = orgadmin_client.patch(f"/api/admin/orgs/{default_org_id}", json={"is_active": False})
        assert r.status_code == 422, r.text
    finally:
        # restore state for the rest of the suite regardless of outcome
        orgadmin_client.patch(f"/api/admin/orgs/{default_org_id}", json={"is_active": True})
        orgadmin_client.patch(f"/api/admin/orgs/{org['id']}", json={"is_active": True})
        for o in others:
            orgadmin_client.patch(f"/api/admin/orgs/{o['id']}", json={"is_active": True})


def test_member_lifecycle(orgadmin_client, default_org_id):
    org = _create_org(orgadmin_client, name="Members Org")
    oid = org["id"]

    # a user with no org membership (cannot log in until added somewhere)
    r = orgadmin_client.post("/api/admin/users", json={
        "username": "member-carol", "password": "carolpass123",
        "full_name": "Carol", "role": "user",
    })
    assert r.status_code == 200, r.text
    carol_id = r.json()["id"]

    # add by username
    r = orgadmin_client.post(f"/api/admin/orgs/{oid}/members", json={"username": "member-carol", "role": "member"})
    assert r.status_code == 201, r.text
    # duplicate → 409
    r = orgadmin_client.post(f"/api/admin/orgs/{oid}/members", json={"user_id": carol_id})
    assert r.status_code == 409, r.text
    # unknown user → 404
    r = orgadmin_client.post(f"/api/admin/orgs/{oid}/members", json={"username": "no-such-user-xyz"})
    assert r.status_code == 404, r.text
    # neither identifier → 422
    r = orgadmin_client.post(f"/api/admin/orgs/{oid}/members", json={"role": "member"})
    assert r.status_code == 422, r.text

    # promote carol to owner (org now has two owners: orgadmin + carol)
    r = orgadmin_client.patch(f"/api/admin/orgs/{oid}/members/{carol_id}", json={"role": "owner"})
    assert r.status_code == 200, r.text

    detail = orgadmin_client.get(f"/api/admin/orgs/{oid}").json()
    oa_member = next(m for m in detail["members"] if m["username"] == "orgadmin")
    assert oa_member["role"] == "owner"

    # demoting the original owner is allowed while carol remains an owner
    r = orgadmin_client.patch(
        f"/api/admin/orgs/{oid}/members/{oa_member['user_id']}", json={"role": "member"}
    )
    assert r.status_code == 200, r.text

    # carol is now the ONLY owner → removal refused
    r = orgadmin_client.delete(f"/api/admin/orgs/{oid}/members/{carol_id}")
    assert r.status_code == 422, r.text

    # self-removal refused (orgadmin's own membership in the default org;
    # the guard is role-independent)
    r = orgadmin_client.delete(f"/api/admin/orgs/{default_org_id}/members/{oa_member['user_id']}")
    assert r.status_code == 422, r.text


def test_org_changes_are_audited(orgadmin_client, db_session, default_org_id):
    from sqlalchemy import text

    from database import set_org_context

    org = _create_org(orgadmin_client, name="Audited Org")

    # install-level audit rows (org_id NULL) are visible to any scoped session
    set_org_context(db_session, "orgadmin", 1, default_org_id)
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


def test_admin_not_in_scratch_orgs(db_session):
    """#17 acceptance: the shared `admin` account must not have been pulled
    into this file's scratch orgs. (Pre-existing orgs may contain it — on the
    upgrade path that also includes the setup-created org.)"""
    from sqlalchemy import text

    rows = db_session.execute(text(
        "SELECT o.name FROM org_members om "
        "JOIN users u ON u.id = om.user_id "
        "JOIN organizations o ON o.id = om.org_id "
        "WHERE u.username = 'admin' AND ("
        "  o.name IN ('Defaults Org', 'Members Org', 'Audited Org') "
        "  OR o.name LIKE 'Guard Org%' "
        "  OR o.name LIKE 'Acme Corp%'"
        ")"
    )).all()
    assert not rows, f"admin was added to this file's scratch orgs: {[r[0] for r in rows]}"
