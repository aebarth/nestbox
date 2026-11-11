from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import schemas
from database import get_db
from models import ScanRun, Source, User
from security import get_current_user, get_current_user_with_org

router = APIRouter(prefix="/api/scans", tags=["scans"])


@router.get("", response_model=list[schemas.ScanRunOut])
def list_runs(limit: int = 50, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # RLS scopes to the caller's org
    return db.execute(
        select(ScanRun).order_by(ScanRun.id.desc()).limit(min(limit, 200))
    ).scalars().all()


@router.post("", response_model=schemas.ScanRunOut)
def queue_scan(
    source_id: int | None = None,
    kind: str = "scan",
    db: Session = Depends(get_db),
    auth: tuple = Depends(get_current_user_with_org),
):
    _user, org_id, _role = auth
    if kind not in ("scan", "integrity"):
        raise HTTPException(status_code=422, detail="kind must be scan or integrity")
    # RLS: sources from other orgs are invisible → 404
    if source_id is not None and db.get(Source, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    pending = db.execute(
        select(ScanRun).where(
            ScanRun.status.in_(("queued", "running")),
            ScanRun.kind == kind,
            ScanRun.source_id.is_(None) if source_id is None else ScanRun.source_id == source_id,
        )
    ).scalars().first()
    if pending:
        return pending
    run = ScanRun(org_id=org_id, source_id=source_id, kind=kind, trigger="manual")
    db.add(run)
    db.commit()
    return run
