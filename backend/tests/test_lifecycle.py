"""Database lifecycle tests (Phase 2.1, #9): backup / restore / reset / prune.

Requires pg_dump/pg_restore on the test host (CI installs postgresql-client).
Each test gets its own temp backup dir so dumps never collide.
"""
import hashlib
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def backup_tmpdir(tmp_path, monkeypatch):
    d = tmp_path / "backups"
    monkeypatch.setenv("NESTBOX_BACKUP_PATH", str(d))
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fresh_session():
    """A brand-new session — required after restore/reset, when every other
    session's tables were dropped and recreated under it."""
    from database import SessionLocal

    return SessionLocal()


def test_backup_creates_file_row_and_audit(admin_client):
    r = admin_client.post("/api/admin/backups", json={"notes": "unit test"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "manual"
    assert body["created_by"] is not None

    path = Path(os.environ["NESTBOX_BACKUP_PATH"]) / body["filename"]
    assert path.is_file(), "dump file was not written to the backup directory"
    assert _sha256(path) == body["sha256"], "recorded checksum does not match the file"

    # listed with metadata
    listing = admin_client.get("/api/admin/backups").json()
    assert any(b["id"] == body["id"] and b["filename"] == body["filename"] for b in listing)

    # audited with the acting user
    audit = admin_client.get("/api/admin/audit", params={"table": "lifecycle"}).json()
    assert any(
        a["username"] == "admin" and (a["new_row"] or {}).get("op") == "backup"
        and a["row_pk"] == body["id"]
        for a in audit
    ), f"no backup audit row found: {audit}"


def test_restore_roundtrip(admin_client, db_session):
    from models import Record, Source

    source = Source(name="lifecycle-test", kind="local", config={"path": "/tmp"})
    db_session.add(source)
    db_session.flush()
    kept = Record(source_id=source.id, path="keep.pdf", name="keep.pdf")
    db_session.add(kept)
    db_session.commit()

    backup = admin_client.post("/api/admin/backups", json={}).json()

    # mutate after the backup: this row must be gone again after restore
    dropped = Record(source_id=source.id, path="drop.pdf", name="drop.pdf")
    db_session.add(dropped)
    db_session.commit()

    r = admin_client.post(f"/api/admin/backups/{backup['id']}/restore", json={"confirm": "RESTORE"})
    assert r.status_code == 200, r.text
    assert r.json()["restored_from"] == backup["filename"]

    with _fresh_session() as s:
        from sqlalchemy import select

        rows = s.execute(select(Record)).scalars().all()
        names = {rec.name for rec in rows}
        assert names == {"keep.pdf"}, f"restore did not roll back post-backup data: {names}"

    # the restore itself is audited (on the restored schema)
    from sqlalchemy import text

    with _fresh_session() as s:
        audit_rows = s.execute(text(
            "SELECT username, new_row FROM audit_log WHERE table_name = 'lifecycle'"
        )).all()
    assert any(r.new_row.get("op") == "restore" and r.username == "admin" for r in audit_rows), \
        f"no restore audit row: {[(r.username, r.new_row) for r in audit_rows]}"


def test_restore_requires_confirmation(admin_client):
    backup = admin_client.post("/api/admin/backups", json={}).json()
    r = admin_client.post(f"/api/admin/backups/{backup['id']}/restore", json={"confirm": "yes"})
    assert r.status_code == 400
    assert "RESTORE" in r.json()["detail"]


def test_restore_unknown_backup_is_404(admin_client):
    r = admin_client.post("/api/admin/backups/999999/restore", json={"confirm": "RESTORE"})
    assert r.status_code == 404


def test_reset_wipes_to_clean_slate(client, admin_client):
    # seed some data: the admin account itself + one record
    from models import Record, Source

    with _fresh_session() as s:
        src = Source(name="reset-test", kind="local", config={"path": "/tmp"})
        s.add(src)
        s.flush()
        s.add(Record(source_id=src.id, path="a.pdf", name="a.pdf"))
        s.commit()

    r = admin_client.post("/api/admin/reset", json={"confirm": "RESET"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reset"] is True

    # a pre-reset backup exists on disk (recoverability)
    pre = Path(os.environ["NESTBOX_BACKUP_PATH"]) / body["pre_reset_backup"]
    assert pre.is_file(), "reset did not take a pre-reset backup"

    # back to first-boot state: setup flow re-runs, no data rows
    status = client.get("/api/auth/status").json()
    assert status["needs_setup"] is True

    with _fresh_session() as s:
        from sqlalchemy import text

        records = s.execute(text("SELECT count(*) FROM records")).scalar()
        users = s.execute(text("SELECT count(*) FROM users")).scalar()
        assert records == 0 and users == 0

        reset_rows = s.execute(text(
            "SELECT username, new_row FROM audit_log WHERE table_name = 'lifecycle'"
        )).all()
    assert any(r.new_row.get("op") == "reset" and r.username == "admin" for r in reset_rows), \
        f"no reset audit row on the fresh schema: {[(r.username, r.new_row) for r in reset_rows]}"


def test_reset_requires_confirmation(admin_client):
    r = admin_client.post("/api/admin/reset", json={"confirm": "do it"})
    assert r.status_code == 400
    assert "RESET" in r.json()["detail"]


def test_non_admin_forbidden(client, admin_client):
    # unauthenticated
    assert client.get("/api/admin/backups").status_code == 401

    # a regular (non-admin) user
    r = admin_client.post("/api/admin/users", json={
        "username": "lifecycle-bob", "password": "bobpass123",
        "full_name": "Bob", "role": "user",
    })
    assert r.status_code == 200, r.text

    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as bob:
        login = bob.post("/api/auth/login", json={"username": "lifecycle-bob", "password": "bobpass123"})
        assert login.status_code == 200, login.text
        assert bob.get("/api/admin/backups").status_code == 403
        assert bob.post("/api/admin/backups", json={}).status_code == 403


def test_prune_retention(database):
    from sqlalchemy import select

    from models import Backup
    from services import lifecycle

    with _fresh_session() as s:
        for i in range(5):
            lifecycle.create_backup(s, kind="manual", actor_username="prune-test")
        pruned = lifecycle.prune_old_backups(s, retention=2)
        assert pruned == 3

        remaining = s.execute(select(Backup).order_by(Backup.id.desc())).scalars().all()
        assert len(remaining) == 2

    d = Path(os.environ["NESTBOX_BACKUP_PATH"])
    files = [p.name for p in d.iterdir()] if d.exists() else []
    expected = {b.filename for b in remaining}
    assert set(files) == expected, f"pruned files still on disk: {files} vs {expected}"
