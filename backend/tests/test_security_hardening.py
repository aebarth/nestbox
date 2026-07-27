def test_login_lockout_after_failures(admin_client, db_session):
    from security import LOCKOUT_ATTEMPTS

    # a dedicated account so failures don't pollute other tests
    admin_client.post("/api/auth/login", json={"username": "admin", "password": "adminpass1"})
    admin_client.post("/api/admin/users", json={
        "username": "lockme", "password": "lockmepass1", "role": "user",
    })
    for _ in range(LOCKOUT_ATTEMPTS):
        r = admin_client.post("/api/auth/login",
                              json={"username": "lockme", "password": "wrong-pass"})
        assert r.status_code == 401
    r = admin_client.post("/api/auth/login",
                          json={"username": "lockme", "password": "lockmepass1"})
    assert r.status_code == 429  # even the right password is locked out now
    # restore admin session for subsequent tests
    admin_client.post("/api/auth/login", json={"username": "admin", "password": "adminpass1"})


def test_record_lifecycle_is_admin_only(admin_client, db_session):
    from database import set_actor
    from models import Record, Source
    from sqlalchemy import select

    source = db_session.execute(select(Source)).scalars().first()
    set_actor(db_session, "pytest")
    rec = Record(source_id=source.id, path="sec-test.pdf", name="sec-test.pdf",
                 title="sec", status="active")
    db_session.add(rec)
    db_session.commit()

    admin_client.post("/api/admin/users", json={
        "username": "plainuser", "password": "plainpass1", "role": "user",
    })
    admin_client.post("/api/auth/login",
                      json={"username": "plainuser", "password": "plainpass1"})
    r = admin_client.post(f"/api/records/{rec.id}/status",
                          json={"status": "retired", "reason": "should fail"})
    assert r.status_code == 403

    admin_client.post("/api/auth/login", json={"username": "admin", "password": "adminpass1"})
    r = admin_client.post(f"/api/records/{rec.id}/status",
                          json={"status": "retired", "reason": "admin ok"})
    assert r.status_code == 200
    assert r.json()["status"] == "retired"


def test_path_traversal_rejected(tmp_path):
    import pytest
    from connectors.local import LocalConnector

    class FakeSource:
        config = {"root": str(tmp_path / "docs")}

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs-sibling").mkdir()
    conn = LocalConnector(FakeSource())
    with pytest.raises(ValueError):
        conn._abs("../docs-sibling/escape.pdf")
    with pytest.raises(ValueError):
        conn._abs("../../etc/passwd")


def test_csv_formula_injection_neutralized():
    from routers.reports import _csv_safe

    assert _csv_safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert _csv_safe("+SUM(1,2)") == "'+SUM(1,2)"
    assert _csv_safe("@evil") == "'@evil"
    assert _csv_safe("LOT-42") == "LOT-42"  # normal values untouched
    assert _csv_safe(42) == 42


def test_header_filename_sanitized():
    from routers.records import safe_header_filename

    assert "\r" not in safe_header_filename('bad"\r\nSet-Cookie: x=y.pdf')
    assert '"' not in safe_header_filename('quote".pdf')
    assert safe_header_filename("normal-file (1).pdf") == "normal-file (1).pdf"
