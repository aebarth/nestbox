import pytest
from sqlalchemy import text


def test_setup_then_login_flow(admin_client):
    me = admin_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    # second setup attempt is rejected
    r = admin_client.post("/api/auth/setup", json={"username": "intruder", "password": "password1"})
    assert r.status_code == 403


def test_wrong_password_rejected(admin_client):
    r = admin_client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_role_guard(admin_client):
    admin_client.post("/api/admin/users", json={
        "username": "clerk", "password": "clerkpass1", "role": "user",
    })
    r = admin_client.post("/api/auth/login", json={"username": "clerk", "password": "clerkpass1"})
    assert r.status_code == 200
    assert admin_client.get("/api/admin/users").status_code == 403     # user blocked
    assert admin_client.get("/api/records?limit=1").status_code == 200  # but can read records
    # restore admin session for other tests
    admin_client.post("/api/auth/login", json={"username": "admin", "password": "adminpass1"})


def test_audit_rows_written_with_actor(admin_client, db_session):
    admin_client.post("/api/lookups/projects", json={"code": "AUD-1", "name": "Audit test"})
    row = db_session.execute(text(
        "SELECT username, action FROM audit_log WHERE table_name='projects' "
        "AND new_row->>'code'='AUD-1'"
    )).first()
    assert row is not None
    assert row.username == "admin"
    assert row.action == "INSERT"


def test_deletes_blocked_at_database(admin_client, db_session):
    # row-level triggers only fire on actual rows, so target tables that
    # are guaranteed non-empty (admin user + its audit entries exist)
    for stmt in (
        "DELETE FROM users",
        "UPDATE audit_log SET username='hacker' WHERE id = (SELECT min(id) FROM audit_log)",
        "DELETE FROM audit_log WHERE id = (SELECT min(id) FROM audit_log)",
    ):
        with pytest.raises(Exception, match="not permitted"):
            db_session.execute(text(stmt))
        db_session.rollback()
