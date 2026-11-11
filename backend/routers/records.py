from datetime import datetime, timedelta, timezone

import re
import subprocess
import tempfile
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

import schemas
from database import get_db
from models import Box, FileEvent, Project, Record, RecordType, User
from security import get_current_user, get_current_user_with_org, require_org_admin
from services.scanner import add_event

router = APIRouter(prefix="/api/records", tags=["records"])


def safe_header_filename(name: str) -> str:
    """Filenames come from scanned filesystems — strip anything that could
    break out of the Content-Disposition header."""
    cleaned = re.sub(r'[^\w.\- ()]', "_", name)[:150]
    return cleaned or "document.pdf"

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
    # RLS scopes every filter to the caller's org (context set by get_current_user)
    query = select(Record).options(*RECORD_OPTS)
    count_q = select(func.count()).select_from(Record)

    def apply(qy):
        if q:
            like = f"%{q}%"
            qy = qy.where(or_(
                Record.name.ilike(like), Record.title.ilike(like),
                Record.path.ilike(like), Record.lot_number.ilike(like),
                Record.part_number.ilike(like), Record.notes.ilike(like),
                # full-text search over the extracted/OCR'd document content
                Record.ocr_tsv.op("@@")(func.websearch_to_tsquery("english", q)),
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


@router.get("/{record_id}/content")
def record_content(record_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Stream the PDF for in-app viewing."""
    import connectors
    from connectors.base import RemoteFile

    r = db.execute(
        select(Record).options(joinedload(Record.source)).where(Record.id == record_id)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if r.status in ("missing",):
        raise HTTPException(status_code=409, detail="File is currently missing")
    try:
        connector = connectors.get(r.source)
        rf = RemoteFile(external_id=r.external_id, path=r.path, name=r.name,
                        size=r.size_bytes, mtime=r.file_mtime)
        stream = connector.open_stream(rf)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read the file: {exc}")
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe_header_filename(r.name)}"',
            # PDFs are untrusted scanned input: sandbox blocks any active
            # content if the file is opened top-level in a browser tab
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{record_id}/preview")
def record_preview(record_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Return the first page of a PDF as a small JPEG thumbnail (120px height).

    Uses pdftoppm (poppler-utils, already in the image for OCR). Non-PDF files
    get a 404 so the frontend can show a generic file-type icon instead.
    """
    r = db.execute(
        select(Record).options(joinedload(Record.source)).where(Record.id == record_id)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")

    # Only PDFs get a rendered preview; everything else → 404 (frontend shows icon)
    if not r.path.lower().endswith(".pdf"):
        raise HTTPException(status_code=404, detail="Preview available for PDF files only")

    import connectors
    from connectors.base import RemoteFile

    try:
        connector = connectors.get(r.source)
        rf = RemoteFile(external_id=r.external_id, path=r.path, name=r.name,
                        size=r.size_bytes, mtime=r.file_mtime)
        with connector.open_stream(rf) as fh:
            pdf_bytes = fh.read()
    except Exception:
        raise HTTPException(status_code=502, detail="Could not read file for preview")

    # Render first page to JPEG at 120px height using pdftoppm
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["pdftoppm", "-jpeg", "-f", "1", "-l", "1", "-scale-to-height", "120",
             tmp_path, "/dev/stdout"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout:
            raise HTTPException(status_code=502, detail="Could not render preview")
        return Response(content=result.stdout, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Preview render timed out")
    finally:
        os.unlink(tmp_path)


@router.get("/{record_id}/text")
def record_text(record_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    r = db.get(Record, record_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return {
        "ocr_status": r.ocr_status,
        "ocr_method": r.ocr_method,
        "ocr_at": r.ocr_at,
        "chars": len(r.ocr_text or ""),
        "preview": (r.ocr_text or "")[:3000],
    }


@router.post("/{record_id}/ocr")
def requeue_ocr(record_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    r = db.get(Record, record_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    r.ocr_status = "pending"
    db.commit()
    return {"ok": True, "detail": "Queued — the worker extracts text within a minute or two."}


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
    # RLS: records from other orgs are invisible → 404; the UPDATE's WITH
    # CHECK passes because org_id is unchanged and matches the session GUC.
    r = db.execute(
        select(Record).options(*RECORD_OPTS).where(Record.id == record_id)
    ).unique().scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if r.status in ("retired", "superseded"):
        raise HTTPException(status_code=422, detail=f"Record is {r.status} and read-only")

    # model_fields_set tells us which fields the client actually sent: omitted =
    # unchanged, explicit null/'' = clear, explicit value = set. This is what
    # makes clearing nullable FKs (and blanking strings) possible via PATCH.
    provided = body.model_fields_set
    changes = {}

    # NOT NULL string fields: explicit null or '' -> "".
    for field in ("title", "lot_number", "part_number", "notes"):
        if field not in provided:
            continue
        value = getattr(body, field)
        new = "" if value is None else value
        if new != getattr(r, field):
            changes[field] = {"from": getattr(r, field), "to": new}
            setattr(r, field, new)

    # tags: NOT NULL array; explicit null or [] -> [].
    if "tags" in provided:
        new = [] if body.tags is None else list(body.tags)
        if new != r.tags:
            changes["tags"] = {"from": r.tags, "to": new}
            r.tags = new

    # Nullable FK fields: explicit null -> clear. Referenced rows (project,
    # record type) must live in the same org — RLS makes cross-org ids 404/None.
    for field in ("project_id", "record_type_id"):
        if field not in provided:
            continue
        value = getattr(body, field)
        if value != getattr(r, field):
            changes[field] = {"from": getattr(r, field), "to": value}
            setattr(r, field, value)

    # box_id: explicit null -> unfile; a real id must reference an open box.
    if "box_id" in provided and body.box_id != r.box_id:
        if body.box_id is None:
            changes["box_id"] = {"from": r.box_id, "to": None}
            r.box_id = None
        else:
            box = db.get(Box, body.box_id)
            if box is None:
                raise HTTPException(status_code=404, detail="Box not found")
            if box.status != "open":
                raise HTTPException(status_code=422, detail=f"Box {box.box_number} is {box.status}")
            changes["box_id"] = {"from": r.box_id, "to": body.box_id}
            r.box_id = body.box_id
            r.filed_at = datetime.now(timezone.utc)
            # Filing an inbox record promotes it to active
            if r.status == "inbox":
                r.status = "active"
                changes["status"] = {"from": "inbox", "to": "active"}
            add_event(db, r, None, "filed", new={"box": box.box_number}, alert=False)

    # Retention: recompute when the record type is set/cleared or a filing lands.
    # Clearing the type (rt is None) drops retention_until too.
    if "record_type_id" in provided or (changes and r.record_type_id):
        rt = db.get(RecordType, r.record_type_id) if r.record_type_id else None
        if rt and rt.retention_years:
            base = r.filed_at or r.first_seen_at or datetime.now(timezone.utc)
            r.retention_until = (base + timedelta(days=365 * rt.retention_years)).date()
        else:
            r.retention_until = None

    if changes:
        add_event(db, r, None, "categorized", new=changes, alert=False)
    db.commit()
    db.refresh(r)
    return record_out(r)


@router.post("/bulk-patch", response_model=schemas.BulkPatchOut)
def bulk_patch(
    body: schemas.BulkPatchIn,
    db: Session = Depends(get_db),
    auth: tuple = Depends(get_current_user_with_org),
):
    """Apply the same patch to many records in a single transaction.

    Used by the review queue's bulk actions (select 10 inbox items → file into
    one box). Each record gets its own audit_log row via the trigger. RLS-scoped:
    only affects records visible to the caller's org.
    """
    user, org_id, org_role = auth
    errors = []
    updated = 0

    # Validate box reference once (if provided)
    box = None
    if body.patch.box_id is not None:
        box = db.get(Box, body.patch.box_id)
        if box is None:
            raise HTTPException(status_code=404, detail="Box not found")
        if box.status != "open":
            raise HTTPException(status_code=422, detail=f"Box {box.box_number} is {box.status}")

    # Validate record type once (if provided)
    rt = None
    if body.patch.record_type_id is not None:
        rt = db.get(RecordType, body.patch.record_type_id)

    for rid in body.ids:
        r = db.execute(
            select(Record).where(Record.id == rid)
        ).scalar_one_or_none()
        if r is None:
            errors.append({"id": rid, "detail": "not found"})
            continue
        if r.status in ("retired", "superseded"):
            errors.append({"id": rid, "detail": f"read-only ({r.status})"})
            continue

        # Apply the same field logic as single-record PATCH
        provided = body.patch.model_fields_set
        for field in ("title", "lot_number", "part_number", "notes"):
            if field in provided:
                value = getattr(body.patch, field)
                setattr(r, field, "" if value is None else value)

        if "tags" in provided:
            r.tags = [] if body.patch.tags is None else list(body.patch.tags)

        for field in ("project_id", "record_type_id"):
            if field in provided:
                setattr(r, field, getattr(body.patch, field))

        if "box_id" in provided and body.patch.box_id != r.box_id:
            if body.patch.box_id is None:
                r.box_id = None
            else:
                r.box_id = box.id
                r.filed_at = datetime.now(timezone.utc)
                if r.status == "inbox":
                    r.status = "active"
                add_event(db, r, None, "filed", new={"box": box.box_number}, alert=False)

        # Retention recompute
        if rt and rt.retention_years:
            base = r.filed_at or r.first_seen_at or datetime.now(timezone.utc)
            r.retention_until = (base + timedelta(days=365 * rt.retention_years)).date()
        elif "record_type_id" in provided and body.patch.record_type_id is None:
            r.retention_until = None

        updated += 1

    db.commit()
    return schemas.BulkPatchOut(updated=updated, errors=errors)


@router.post("/{record_id}/status", response_model=schemas.RecordOut)
def change_status(
    record_id: int,
    body: schemas.RecordStatusIn,
    db: Session = Depends(get_db),
    auth: tuple = Depends(require_org_admin),  # lifecycle changes are org-admin-only
):
    user, _org_id, _role = auth
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
