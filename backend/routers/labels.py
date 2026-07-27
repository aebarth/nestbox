import os

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Box, Record, User
from security import get_current_user
from services.labels import FORMATS, build_labels_pdf, record_label_item

router = APIRouter(prefix="/api/labels", tags=["labels"])


class LabelRequest(BaseModel):
    record_ids: list[int] = []
    box_ids: list[int] = []          # one label per box
    box_contents_ids: list[int] = [] # a label for every record in these boxes
    format: str = "avery5160"
    start_offset: int = 0


@router.post("")
def make_labels(body: LabelRequest, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    if body.format not in FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {sorted(FORMATS)}")
    base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")
    items = []

    if body.record_ids or body.box_contents_ids:
        q = select(Record).options(
            joinedload(Record.project), joinedload(Record.record_type), joinedload(Record.box)
        )
        conditions = []
        if body.record_ids:
            conditions.append(Record.id.in_(body.record_ids))
        if body.box_contents_ids:
            conditions.append(Record.box_id.in_(body.box_contents_ids))
        from sqlalchemy import or_
        rows = db.execute(q.where(or_(*conditions))).unique().scalars().all()
        items += [record_label_item(r, base_url) for r in rows]

    for box_id in body.box_ids:
        b = db.get(Box, box_id)
        if b:
            items.append({
                "title": f"Box {b.box_number}",
                "line2": b.description,
                "line3": b.physical_location,
                "url": f"{base_url}/boxes?box={b.id}",
            })

    if not items:
        raise HTTPException(status_code=422, detail="Nothing to print")
    pdf = build_labels_pdf(items, body.format, body.start_offset)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="nestbox-labels.pdf"'},
    )
