"""Scan-to-shelf wizard.

1. scan lands in the inbox folder  ->  worker/scan picks it up as status='inbox'
2. name + categorize               ->  this router
3. print label                     ->  routers/labels.py
4. assign to box                   ->  this router
5. move to electronic location     ->  this router (local move or cloud upload)
"""
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import connectors
import schemas
from connectors.local import LocalConnector
from database import get_db
from models import Box, Record, RecordType, Source, User
from routers.records import RECORD_OPTS, record_out
from security import get_current_user
from services.scanner import add_event

router = APIRouter(prefix="/api/wizard", tags=["wizard"])


class FileIn(BaseModel):
    title: str
    project_id: int | None = None
    record_type_id: int | None = None
    lot_number: str = ""
    part_number: str = ""
    tags: list[str] = []
    notes: str = ""
    box_id: int | None = None
    destination_source_id: int
    destination_folder: str = ""   # relative folder inside the destination source
    new_filename: str | None = None


@router.get("/inbox")
def inbox(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Record).options(*RECORD_OPTS).where(Record.status == "inbox")
        .order_by(Record.first_seen_at.asc())
    ).unique().scalars().all()
    return [record_out(r) for r in rows]


@router.post("/{record_id}/file", response_model=schemas.RecordOut)
def file_record(
    record_id: int,
    body: FileIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.execute(
        select(Record).options(*RECORD_OPTS).where(Record.id == record_id)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if r.status != "inbox":
        raise HTTPException(status_code=422, detail="Only inbox records can go through the wizard")

    dest = db.get(Source, body.destination_source_id)
    if dest is None or not dest.is_active:
        raise HTTPException(status_code=404, detail="Destination source not found")
    if dest.config.get("builtin_inbox"):
        raise HTTPException(status_code=422, detail="Destination cannot be the inbox itself")

    filename = (body.new_filename or r.name).strip()
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    folder = body.destination_folder.strip("/ ")
    new_rel = f"{folder}/{filename}" if folder else filename

    # categorize
    r.title = body.title or r.title
    r.project_id = body.project_id
    r.record_type_id = body.record_type_id
    r.lot_number = body.lot_number
    r.part_number = body.part_number
    r.tags = body.tags
    r.notes = body.notes

    box = None
    if body.box_id is not None:
        box = db.get(Box, body.box_id)
        if box is None:
            raise HTTPException(status_code=404, detail="Box not found")
        if box.status != "open":
            raise HTTPException(status_code=422, detail=f"Box {box.box_number} is {box.status}")

    # move the file BEFORE mutating location fields, so a failure leaves the
    # record consistent (still in the inbox)
    src_conn = connectors.get(r.source)
    from connectors.base import RemoteFile

    current = RemoteFile(external_id=r.external_id, path=r.path, name=r.name,
                         size=r.size_bytes, mtime=r.file_mtime)
    old_loc = {"source": r.source.name, "path": r.path}
    try:
        dest_conn = connectors.get(dest)
        dest_conn.test()
        if dest.kind == "local" and isinstance(src_conn, LocalConnector):
            moved = src_conn.move_to(current, dest_conn, new_rel)
        elif dest.kind == "local":
            raise HTTPException(status_code=422, detail="Unsupported source/destination pairing")
        else:
            # cloud destination: upload the inbox file, then remove the local copy
            with src_conn.open_stream(current) as fh:
                moved = dest_conn.upload(fh, new_rel)
            os.unlink(src_conn._abs(current.path))
    except HTTPException:
        raise
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Move failed: {exc}")

    r.source_id = dest.id
    r.external_id = moved.external_id
    r.path = moved.path
    r.name = moved.name
    r.size_bytes = moved.size
    r.file_mtime = moved.mtime
    if moved.checksum:
        r.hash_algo, r.hash_value = moved.checksum
    r.status = "active"
    now = datetime.now(timezone.utc)
    if box is not None:
        r.box_id = box.id
        r.filed_at = now
        add_event(db, r, None, "filed", new={"box": box.box_number}, alert=False)

    rt = db.get(RecordType, r.record_type_id) if r.record_type_id else None
    if rt and rt.retention_years:
        r.retention_until = (now + timedelta(days=365 * rt.retention_years)).date()

    add_event(db, r, None, "moved", old=old_loc,
              new={"source": dest.name, "path": moved.path}, alert=False)
    db.commit()
    db.refresh(r)
    return record_out(r)
