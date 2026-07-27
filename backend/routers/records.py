from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

import schemas
from database import get_db
from models import Box, FileEvent, Project, Record, RecordType, User
from security import get_current_user
from services.scanner import add_event

router = APIRouter(prefix="/api/records", tags=["records"])

RECORD_OPTS = (
    joinedload(Record.source),
    joinedload(Record.project),
    joinedload(Record.record_type),
    joinedload(Record.box),
)


def record_out(r: Record) -> schemas.RecordOut:
    out = schemas.RecordOut.model_validate(r)
    out.source_name = r.source.name if r.source else None
    out.project_code = r.project.code if r.project else None
    out.record_type_code = r.record_type.code if r.record_type else None
    out.box_number = r.box.box_number if r.box else None
    return out


@router.get("")
def search(
    q: str | None = None,
    status: str | None = None,
    source_id: int | None = None,
    project_id: int | None = None,
    record_type_id: int | None = None,
    box_id: int | None = None,
    lot_number: str | None = None,
    part_number: str | None = None,
    tag: str | None = None,
    duplicates_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Record).options(*RECORD_OPTS)
    count_q = select(func.count()).select_from(Record)

    def apply(qy):
        if q:
            like = f"%{q}%"
            qy = qy.where(or_(
                Record.name.ilike(like), Record.title.ilike(like),
                Record.path.ilike(like), Record.lot_number.ilike(like),
                Record.part_number.ilike(like), Record.notes.ilike(like),
            ))
        if status:
            qy = qy.where(Record.status == status)
        else:
            qy = qy.where(Record.status.notin_(("retired", "superseded")))
        if source_id:
            qy = qy.where(Record.source_id == source_id)
        if project_id:
            qy = qy.where(Record.project_id == project_id)
        if record_type_id:
            qy = qy.where(Record.record_type_id == record_type_id)
        if box_id:
            qy = qy.where(Record.box_id == box_id)
        if lot_number:
            qy = qy.where(Record.lot_number.ilike(f"%{lot_number}%"))
        if part_number:
            qy = qy.where(Record.part_number.ilike(f"%{part_number}%"))
        if tag:
            qy = qy.where(Record.tags.any(tag))
        if duplicates_only:
            qy = qy.where(Record.duplicate_group_id.isnot(None))
        return qy

    total = db.execute(apply(count_q)).scalar()
    rows = db.execute(
        apply(query).order_by(Record.last_seen_at.desc()).limit(min(limit, 200)).offset(offset)
    ).scalars().all()
    return {"total": total, "items": [record_out(r) for r in rows]}


@router.get("/by-uuid/{record_uuid}", response_model=schemas.RecordOut)
def get_by_uuid(record_uuid: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.execute(
        select(Record).options(*RECORD_OPTS).where(Record.uuid == record_uuid)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record_out(r)


@router.get("/{record_id}", response_model=schemas.RecordOut)
def get_record(record_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.execute(
        select(Record).options(*RECORD_OPTS).where(Record.id == record_id)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record_out(r)


@router.get("/{record_id}/events", response_model=list[schemas.FileEventOut])
def record_events(record_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.execute(
        select(FileEvent).where(FileEvent.record_id == record_id).order_by(FileEvent.occurred_at.desc())
    ).scalars().all()


@router.get("/{record_id}/duplicates")
def record_duplicates(record_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(Record, record_id)
    if r is None or r.duplicate_group_id is None:
        return []
    rows = db.execute(
        select(Record).options(*RECORD_OPTS).where(
            Record.duplicate_group_id == r.duplicate_group_id, Record.id != record_id
        )
    ).unique().scalars().all()
    return [record_out(x) for x in rows]


@router.patch("/{record_id}", response_model=schemas.RecordOut)
def categorize(
    record_id: int,
    body: schemas.RecordCategorizeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.execute(
        select(Record).options(*RECORD_OPTS).where(Record.id == record_id)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if r.status in ("retired", "superseded"):
        raise HTTPException(status_code=422, detail=f"Record is {r.status} and read-only")

    changes = {}
    for field in ("title", "project_id", "record_type_id", "lot_number", "part_number", "tags", "notes"):
        value = getattr(body, field)
        if value is not None and getattr(r, field) != value:
            changes[field] = {"from": getattr(r, field), "to": value}
            setattr(r, field, value)

    if body.box_id is not None and body.box_id != r.box_id:
        box = db.get(Box, body.box_id)
        if box is None:
            raise HTTPException(status_code=404, detail="Box not found")
        if box.status != "open":
            raise HTTPException(status_code=422, detail=f"Box {box.box_number} is {box.status}")
        changes["box_id"] = {"from": r.box_id, "to": body.box_id}
        r.box_id = body.box_id
        r.filed_at = datetime.now(timezone.utc)
        add_event(db, r, None, "filed", new={"box": box.box_number}, alert=False)

    if body.record_type_id is not None or (changes and r.record_type_id):
        rt = db.get(RecordType, r.record_type_id) if r.record_type_id else None
        if rt and rt.retention_years:
            base = r.filed_at or r.first_seen_at or datetime.now(timezone.utc)
            r.retention_until = (base + timedelta(days=365 * rt.retention_years)).date()
        elif rt:
            r.retention_until = None

    if changes:
        add_event(db, r, None, "categorized", new=changes, alert=False)
    db.commit()
    db.refresh(r)
    return record_out(r)


@router.post("/{record_id}/status", response_model=schemas.RecordOut)
def change_status(
    record_id: int,
    body: schemas.RecordStatusIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.execute(
        select(Record).options(*RECORD_OPTS).where(Record.id == record_id)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if body.status not in ("retired", "superseded", "active"):
        raise HTTPException(status_code=422, detail="Status must be retired, superseded or active")

    old_status = r.status
    if body.status == "retired":
        r.status = "retired"
        r.retired_at = datetime.now(timezone.utc)
        r.retired_by = user.id
        add_event(db, r, None, "retired",
                  old={"status": old_status}, new={"reason": body.reason}, alert=False)
    elif body.status == "superseded":
        if not body.superseded_by_id or db.get(Record, body.superseded_by_id) is None:
            raise HTTPException(status_code=422, detail="superseded_by_id must reference an existing record")
        r.status = "superseded"
        r.superseded_by_id = body.superseded_by_id
        add_event(db, r, None, "superseded",
                  old={"status": old_status},
                  new={"superseded_by": body.superseded_by_id, "reason": body.reason}, alert=False)
    else:
        r.status = "active"
        r.retired_at = None
        r.retired_by = None
        r.superseded_by_id = None
    db.commit()
    db.refresh(r)
    return record_out(r)
