"""Database lifecycle tests (Phase 2.1, #9): backup / restore / reset / prune.

Requires pg_dump/pg_restore on the test host (CI installs postgresql-client).
Each test gets its own temp backup dir so dumps never collide.

Multi-org (v5): the app role (nestbox_app) cannot read org-scoped tables
without an RLS context, and restore/reset drop the schema out from under
every session — so post-restore/post-reset verification reads use a fresh
session on the OWNER engine (schema_engine), which bypasses RLS by design.
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
    """A brand-new app session — required after restore/reset, when every
    other session's tables were dropped and recreated under it."""
    from database import SessionLocal

    return SessionLocal()


def _owner_session():
    """Fresh session on the table-owner engine (bypasses RLS). Used to
    verify install-level state after restore/reset, where the app role has
    no org context (and in the reset case, no user/org rows at all)."""
    from sqlalchemy.orm import sessionmaker

    from database import schema_engine

    return sessionmaker(bind=schema_engine, autoflush=False, expire_on_commit=False)()


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

    # audited with the acting user (install-level row: org_id NULL, visible
    # to every org via the audit_log policy)
    audit = admin_client.get("/api/admin/audit", params={"table": "lifecycle"}).json()
    assert any(
        a["username"] == "admin" and (a["new_row"] or {}).get("op") == "backup"
        and a["row_pk"] == body["id"]
        for a in audit
    ), f"no backup audit row found: {audit}"


def test_restore_roundtrip(admin_client, org_db, default_org_id):
    from models import Record, Source

    source = Source(org_id=default_org_id, name="lifecycle-test", kind="local", config={"path": "/tmp"})
    org_db.add(source)
    org_db.flush()
    kept = Record(org_id=default_org_id, source_id=source.id, path="keep.pdf", name="keep.pdf")
    org_db.add(kept)
    org_db.commit()

    backup = admin_client.post("/api/admin/backups", json={}).json()

    # mutate after the backup: this row must be gone again after restore
    dropped = Record(org_id=default_org_id, source_id=source.id, path="drop.pdf", name="drop.pdf")
    org_db.add(dropped)
    org_db.commit()

    r = admin_client.post(f"/api/admin/backups/{backup['id']}/restore", json={"confirm": "RESTORE"})
    assert r.status_code == 200, r.text
    assert r.json()["restored_from"] == backup["filename"]

    # org_db is stale (its schema was dropped/recreated) — verify via owner.
    # The suite shares one session-scoped database, so other tests' rows may
    # legitimately be present in the mid-test backup; assert on this test's
    # own rows rather than the full set.
    with _owner_session() as s:
        from sqlalchemy import select

        rows = s.execute(select(Record)).scalars().all()
        names = {rec.name for rec in rows}
        assert "keep.pdf" in names, f"restore lost pre-backup data: {names}"
        assert "drop.pdf" not in names, f"restore did not roll back post-backup data: {names}"

    # the restore itself is audited (on the restored schema)
    from sqlalchemy import text

    with _owner_session() as s:
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


def test_reset_wipes_to_clean_slate(client, admin_client, org_db, default_org_id):
    # seed some data: the admin account itself + one record
    from models import Record, Source

    src = Source(org_id=default_org_id, name="reset-test", kind="local", config={"path": "/tmp"})
    org_db.add(src)
    org_db.flush()
    org_db.add(Record(org_id=default_org_id, source_id=src.id, path="a.pdf", name="a.pdf"))
    org_db.commit()

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

    with _owner_session() as s:
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


def test_non_admin_forbidden(admin_client, default_org_id):
    from fastapi.testclient import TestClient

    from main import app

    # unauthenticated — a fresh client, because the shared `client` fixture is
    # logged in by `admin_client` (it reuses the same TestClient instance)
    with TestClient(app) as anon:
        assert anon.get("/api/admin/backups").status_code == 401
        assert anon.post("/api/admin/backups", json={}).status_code == 401

    # a regular (non-admin) user — v5 requires an org membership to log in
    r = admin_client.post("/api/admin/users", json={
        "username": "lifecycle-bob", "password": "bobpass123",
        "full_name": "Bob",
        "role": "user",
        "org_id": default_org_id, "org_role": "member",
    })
    assert r.status_code == 200, r.text

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
