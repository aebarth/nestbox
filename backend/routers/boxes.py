from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import schemas
from database import get_db
from models import Box, Record, User
from security import get_current_user, get_current_user_with_org

router = APIRouter(prefix="/api/boxes", tags=["boxes"])


def _with_counts(db: Session, boxes: list[Box]) -> list[schemas.BoxOut]:
    # RLS scopes both the box list and the record counts to the caller's org
    counts = dict(
        db.execute(
            select(Record.box_id, func.count())
            .where(Record.box_id.isnot(None), Record.status.notin_(("retired", "superseded")))
            .group_by(Record.box_id)
        ).all()
    )
    out = []
    for b in boxes:
        item = schemas.BoxOut.model_validate(b)
        item.record_count = counts.get(b.id, 0)
        out.append(item)
    return out


@router.get("", response_model=list[schemas.BoxOut])
def list_boxes(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    boxes = db.execute(select(Box).order_by(Box.box_number)).scalars().all()
    return _with_counts(db, boxes)


@router.post("", response_model=schemas.BoxOut)
def create_box(body: schemas.BoxIn, db: Session = Depends(get_db),
               auth: tuple = Depends(get_current_user_with_org)):
    _user, org_id, _role = auth
    # RLS scopes this to the caller's org; the (org_id, box_number) unique
    # constraint is the backstop against races.
    if db.execute(select(Box).where(Box.box_number == body.box_number)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Box number already exists")
    box = Box(org_id=org_id, box_number=body.box_number, physical_location=body.physical_location,
              description=body.description, status="open")
    db.add(box)
    db.commit()
    return _with_counts(db, [box])[0]


@router.patch("/{box_id}", response_model=schemas.BoxOut)
def update_box(box_id: int, body: schemas.BoxIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    # RLS: boxes from other orgs are invisible → 404
    box = db.get(Box, box_id)
    if box is None:
        raise HTTPException(status_code=404, detail="Box not found")
    if body.status not in ("open", "sealed", "destroyed"):
        raise HTTPException(status_code=422, detail="Invalid box status")
    box.box_number = body.box_number
    box.physical_location = body.physical_location
    box.description = body.description
    if body.status != box.status:
        box.status = body.status
        box.sealed_at = datetime.now(timezone.utc) if body.status == "sealed" else box.sealed_at
    db.commit()
    return _with_counts(db, [box])[0]
