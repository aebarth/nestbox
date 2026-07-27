from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import connectors
import schemas
from database import get_db
from models import Source, User
from security import get_current_user, require_admin

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _out(s: Source) -> schemas.SourceOut:
    out = schemas.SourceOut.model_validate(s)
    out.has_credentials = s.credentials_enc is not None
    return out


@router.get("", response_model=list[schemas.SourceOut])
def list_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(select(Source).order_by(Source.name)).scalars().all()
    return [_out(s) for s in rows]


@router.post("", response_model=schemas.SourceOut, dependencies=[Depends(require_admin)])
def create_source(body: schemas.SourceIn, db: Session = Depends(get_db)):
    if body.kind not in ("local", "gdrive", "graph_drive"):
        raise HTTPException(status_code=422, detail="Unknown source kind")
    if body.kind == "local":
        root = str(body.config.get("root", "")).rstrip("/")
        if not root.startswith("/sources"):
            raise HTTPException(
                status_code=422,
                detail="Local folders must be under /sources (the NESTBOX_SOURCES_PATH mount), e.g. /sources/quality-records",
            )
        body.config["root"] = root
    source = Source(
        name=body.name,
        kind=body.kind,
        config=body.config,
        is_monitored=body.is_monitored,
        scan_cron=body.scan_cron,
        is_active=body.is_active,
    )
    db.add(source)
    db.commit()
    return _out(source)


@router.patch("/{source_id}", response_model=schemas.SourceOut, dependencies=[Depends(require_admin)])
def update_source(source_id: int, body: schemas.SourceIn, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.config.get("builtin_inbox") and not body.is_active:
        raise HTTPException(status_code=422, detail="The scan inbox cannot be deactivated")
    source.name = body.name
    source.config = {**body.config, **({"builtin_inbox": True} if source.config.get("builtin_inbox") else {})}
    source.is_monitored = body.is_monitored
    source.scan_cron = body.scan_cron
    source.is_active = body.is_active
    db.commit()
    return _out(source)


@router.post("/{source_id}/test")
def test_source(source_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        connectors.get(source).test()
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True}
