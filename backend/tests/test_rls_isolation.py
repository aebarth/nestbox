"""RLS isolation proof — the #10 acceptance criterion.

Two organizations in one database. Verifies, end to end:
  * per-org unique constraints (same project code in two orgs is allowed)
  * RLS read isolation (each org lists only its own rows)
  * cross-org id access is invisible (404, not empty-mutation)
  * the database itself enforces isolation: a nestbox_app session with org B's
    GUC cannot see org A rows, and WITH CHECK rejects an INSERT that claims
    a different org than the session context
  * the multi-org login → pending-token → select-org flow, including switching

Org B is created directly via ORM: organizations/org_members are not RLS-
restricted (they must be readable during auth), and org-management endpoints
land in the follow-up PR.
"""
import uuid

import pytest


@pytest.fixture()
def org_b(db_session):
    """A second organization + its id, created outside RLS."""
    from models import Organization

    org = Organization(name="Isolation Org B", slug=f"iso-b-{uuid.uuid4().hex[:8]}",
                       kind="business")
    db_session.add(org)
    db_session.commit()
    return org.id


@pytest.fixture()
def client_b(admin_client, org_b):
    """A fresh TestClient logged in as an owner of org B."""
    from fastapi.testclient import TestClient

    from main import app

    r = admin_client.post("/api/admin/users", json={
        "username": f"orgb-{uuid.uuid4().hex[:6]}", "password": "orgbpass123",
        "full_name": "Org B Admin", "role": "user",
        "org_id": org_b, "org_role": "owner",
    })
    assert r.status_code == 200, r.text
    username = r.json()["username"]

    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": username, "password": "orgbpass123"})
        assert login.status_code == 200, login.text
        assert login.json()["needs_org_selection"] is False  # single org → auto-select
        yield c


def test_per_org_unique_codes(admin_client, client_b):
    r_a = admin_client.post("/api/lookups/projects", json={"code": "ISO-X", "name": "A project"})
    assert r_a.status_code == 200, r_a.text
    # same code in org B: allowed (uniqueness is per-org)
    r_b = client_b.post("/api/lookups/projects", json={"code": "ISO-X", "name": "B project"})
    assert r_b.status_code == 200, r_b.text
    # ...but a duplicate within the same org is still rejected
    r_dup = client_b.post("/api/lookups/projects", json={"code": "ISO-X", "name": "dup"})
    assert r_dup.status_code == 409


def test_read_isolation(admin_client, client_b):
    admin_client.post("/api/lookups/projects", json={"code": "ONLY-A", "name": "A only"})
    client_b.post("/api/lookups/projects", json={"code": "ONLY-B", "name": "B only"})

    codes_a = {p["code"] for p in admin_client.get("/api/lookups/projects").json()}
    codes_b = {p["code"] for p in client_b.get("/api/lookups/projects").json()}
    assert "ONLY-A" in codes_a and "ONLY-B" not in codes_a
    assert "ONLY-B" in codes_b and "ONLY-A" not in codes_b


def test_cross_org_ids_invisible(admin_client, client_b):
    proj_a = admin_client.post("/api/lookups/projects", json={"code": "HIDDEN-A", "name": "x"}).json()

    # org B cannot read or mutate org A's row by id — RLS makes it a 404
    assert client_b.get(f"/api/lookups/projects/{proj_a['id']}").status_code == 404
    assert client_b.patch(
        f"/api/lookups/projects/{proj_a['id']}", json={"code": "HIDDEN-A", "name": "pwned"}
    ).status_code == 404
    # and it is untouched
    assert admin_client.get(f"/api/lookups/projects/{proj_a['id']}").json()["name"] == "x"


def test_database_enforces_isolation(db_session, org_b):
    """Bypass the API entirely: raw nestbox_app session with org B's GUC."""
    from sqlalchemy import text

    from database import schema_engine, set_org_context

    org_a = db_session.execute(text(
        "SELECT id FROM organizations ORDER BY created_at LIMIT 1"
    )).scalar_one()

    # seed one project per org as the table owner
    with schema_engine.begin() as conn:
        for oid, code in ((org_a, "DB-A"), (org_b, "DB-B")):
            conn.execute(
                text("INSERT INTO projects (org_id, code, name) VALUES (:o, :c, :c)"),
                {"o": oid, "c": code},
            )

    # org B's context sees its row but not org A's
    set_org_context(db_session, "pytest", 1, org_b)
    visible = {r.code for r in db_session.execute(text("SELECT code FROM projects")).scalars()}
    assert "DB-B" in visible and "DB-A" not in visible

    # WITH CHECK: an INSERT claiming org A while the session is scoped to B
    # must be rejected by the policy, not silently accepted
    with pytest.raises(Exception):
        db_session.execute(
            text("INSERT INTO projects (org_id, code, name) VALUES (:o, 'EVIL', 'EVIL')"),
            {"o": org_a},
        )
        db_session.commit()
    db_session.rollback()

    # cleanup via owner so other tests' counts stay predictable
    with schema_engine.begin() as conn:
        conn.execute(text("DELETE FROM projects WHERE code IN ('DB-A', 'DB-B')"))


def test_multi_org_login_and_select_org(admin_client, org_b):
    """A user in BOTH orgs: login returns the picker, select-org exchanges
    the pending token, and switching works from a full session."""
    import uuid as _uuid

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from database import SessionLocal
    from main import app

    username = f"dual-{_uuid.uuid4().hex[:6]}"
    r = admin_client.post("/api/admin/users", json={
        "username": username, "password": "dualpass123",
        "full_name": "Dual", "role": "user",
        "org_id": org_b, "org_role": "member",
    })
    assert r.status_code == 200, r.text

    # also add the membership in org A (the first-created org)
    with SessionLocal() as s:
        uid = s.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()
        org_a_id = s.execute(
            text("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
        ).scalar_one()
        s.execute(
            text("INSERT INTO org_members (user_id, org_id, role) VALUES (:u, :o, 'member')"),
            {"u": uid, "o": org_a_id},
        )
        s.commit()

    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": username, "password": "dualpass123"})
        assert login.status_code == 200, login.text
        body = login.json()
        assert body["needs_org_selection"] is True
        assert len(body["orgs"]) == 2

        # without an org selected, org-scoped endpoints refuse the pending token
        assert c.get("/api/lookups/projects").status_code == 403

        # exchange the pending token for org B
        r = c.post("/api/auth/select-org", json={"org_id": org_b})
        assert r.status_code == 200, r.text
        assert r.json()["needs_org_selection"] is False

        codes = {p["code"] for p in c.get("/api/lookups/projects").json()}
        assert "ONLY-B" in codes and "ONLY-A" not in codes

        # switch to org A from the full session
        r = c.post("/api/auth/select-org", json={"org_id": org_a_id})
        assert r.status_code == 200, r.text
        codes = {p["code"] for p in c.get("/api/lookups/projects").json()}
        assert "ONLY-A" in codes and "ONLY-B" not in codes
