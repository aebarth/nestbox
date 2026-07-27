from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

import schemas
from database import get_db
from models import Alert, DuplicateGroup, Record, User
from security import get_current_user

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _out(a: Alert) -> schemas.AlertOut:
    out = schemas.AlertOut.model_validate(a)
    if a.record:
        out.record_name = a.record.title or a.record.name
        out.record_path = a.record.path
        out.source_name = a.record.source.name if a.record.source else None
    return out


@router.get("", response_model=list[schemas.AlertOut])
def list_alerts(
    status: str = "open",
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = (
        select(Alert)
        .options(joinedload(Alert.record).joinedload(Record.source))
        .order_by(Alert.created_at.desc())
        .limit(min(limit, 500))
        .offset(offset)
    )
    if status == "unresolved":
        q = q.where(Alert.status != "resolved")
    elif status != "all":
        q = q.where(Alert.status == status)
    rows = db.execute(q).unique().scalars().all()
    return [_out(a) for a in rows]


@router.post("/{alert_id}", response_model=schemas.AlertOut)
def review_alert(
    alert_id: int,
    body: schemas.AlertResolveIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = db.execute(
        select(Alert)
        .options(joinedload(Alert.record).joinedload(Record.source))
        .where(Alert.id == alert_id)
    ).unique().scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if body.status == "acknowledged":
        a.status = "acknowledged"
    elif body.status == "resolved":
        if body.resolution not in ("accepted", "restored", "retired", "false_positive"):
            raise HTTPException(status_code=422, detail="Invalid resolution")
        if body.resolution == "retired" and user.role != "admin":
            raise HTTPException(status_code=403,
                                detail="Retiring a record requires an admin")
        a.status = "resolved"
        a.resolution = body.resolution
        a.resolved_by = user.id
        a.resolved_at = datetime.now(timezone.utc)
        if body.resolution == "retired" and a.record and a.record.status == "missing":
            a.record.status = "retired"
            a.record.retired_at = datetime.now(timezone.utc)
            a.record.retired_by = user.id
    else:
        raise HTTPException(status_code=422, detail="status must be acknowledged or resolved")
    if body.notes:
        a.notes = body.notes
    db.commit()
    return _out(a)


@router.post("/duplicates/{group_id}/dismiss")
def dismiss_duplicate_group(group_id: int, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    g = db.get(DuplicateGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Duplicate group not found")
    g.is_dismissed = True
    g.dismissed_by = user.id
    g.dismissed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}
