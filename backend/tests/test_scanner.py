"""Change-detection matrix against real temp directories.

Multi-org (v5): fixtures run inside the default org's RLS context (org_db)
and every directly-inserted row carries an explicit org_id. The session-
scoped GUCs survive the commits inside scan_source, so reads after a scan
still see the org's rows.
"""
import os
import shutil

import pytest
from sqlalchemy import select


@pytest.fixture()
def source_env(tmp_path, org_db, default_org_id, admin_client):
    os.environ["NESTBOX_SOURCE_ROOTS"] = str(tmp_path)
    root = tmp_path / "docs"
    root.mkdir()
    from models import Source

    source = Source(org_id=default_org_id, name=f"t-{tmp_path.name}", kind="local",
                    config={"root": str(root)})
    org_db.add(source)
    org_db.commit()
    return root, source, org_db


def write_pdf(path, content=b"%PDF-1.4 test content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def run_scan(db, source):
    from database import set_org_context
    from models import ScanRun
    from services.scanner import scan_source

    # RLS context for this session (worker would be BYPASSRLS; tests run as
    # nestbox_app so the GUCs are required for every org-scoped statement).
    set_org_context(db, "pytest", 1, source.org_id)
    run = ScanRun(org_id=source.org_id, source_id=source.id, kind="scan",
                  trigger="manual", status="running")
    db.add(run)
    db.flush()
    stats = scan_source(db, source, run)
    db.commit()
    return stats


def events_for(db, record_id):
    from models import FileEvent

    return [e.event_type for e in db.execute(
        select(FileEvent).where(FileEvent.record_id == record_id).order_by(FileEvent.id)
    ).scalars()]


def get_records(db, source):
    from models import Record

    return db.execute(select(Record).where(Record.source_id == source.id)).scalars().all()


def test_discover_hash_and_idempotency(source_env):
    root, source, db = source_env
    write_pdf(root / "a.pdf", b"AAA")
    write_pdf(root / "sub/b.pdf", b"BBB")
    stats = run_scan(db, source)
    assert stats["new"] == 2
    records = get_records(db, source)
    assert {r.path for r in records} == {"a.pdf", "sub/b.pdf"}
    assert all(r.hash_algo == "sha256" and r.hash_value for r in records)

    stats2 = run_scan(db, source)
    assert stats2["new"] == 0 and stats2["missing"] == 0 and stats2["moved"] == 0


def test_rename_keeps_identity(source_env):
    root, source, db = source_env
    write_pdf(root / "old-name.pdf", b"SAME BYTES")
    run_scan(db, source)
    (rec,) = get_records(db, source)

    os.rename(root / "old-name.pdf", root / "new-name.pdf")
    stats = run_scan(db, source)
    assert stats["moved"] == 1 and stats["new"] == 0 and stats["missing"] == 0
    db.refresh(rec)
    assert rec.path == "new-name.pdf"
    assert "renamed" in events_for(db, rec.id)


def test_move_to_subfolder(source_env):
    root, source, db = source_env
    write_pdf(root / "c.pdf", b"CCC")
    run_scan(db, source)
    (rec,) = get_records(db, source)

    (root / "archive").mkdir()
    shutil.move(str(root / "c.pdf"), str(root / "archive/c.pdf"))
    run_scan(db, source)
    db.refresh(rec)
    assert rec.path == "archive/c.pdf"
    assert "moved" in events_for(db, rec.id)


def test_missing_then_reappear(source_env):
    root, source, db = source_env
    write_pdf(root / "d.pdf", b"DDD")
    run_scan(db, source)
    (rec,) = get_records(db, source)

    os.remove(root / "d.pdf")
    stats = run_scan(db, source)
    assert stats["missing"] == 1
    db.refresh(rec)
    assert rec.status == "missing"

    write_pdf(root / "d.pdf", b"DDD")
    run_scan(db, source)
    db.refresh(rec)
    assert rec.status == "active"
    assert "reappeared" in events_for(db, rec.id)


def test_modified_content_detected(source_env):
    root, source, db = source_env
    f = root / "e.pdf"
    write_pdf(f, b"ORIGINAL")
    run_scan(db, source)
    (rec,) = get_records(db, source)
    old_hash = rec.hash_value

    write_pdf(f, b"TAMPERED CONTENT!")
    os.utime(f, (os.path.getatime(f), os.path.getmtime(f) + 60))
    stats = run_scan(db, source)
    assert stats["modified"] == 1
    db.refresh(rec)
    assert rec.hash_value != old_hash
    assert "modified" in events_for(db, rec.id)


def test_duplicates_grouped(source_env):
    root, source, db = source_env
    write_pdf(root / "orig.pdf", b"IDENTICAL")
    write_pdf(root / "copy.pdf", b"IDENTICAL")
    run_scan(db, source)
    records = get_records(db, source)
    gids = {r.duplicate_group_id for r in records}
    assert len(gids) == 1 and None not in gids
