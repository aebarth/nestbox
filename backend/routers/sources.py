from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import connectors
import schemas
from database import get_db
from models import Source, User
from security import get_current_user, require_org_admin

router = APIRouter(prefix="/api/sources", tags=["sources"])

# Kinds the backend supports. Network kinds (smb, nfs) point at a mounted
# folder inside the container — Docker volume mounts handle the protocol.
VALID_KINDS = ("local", "smb", "nfs", "gdrive", "graph_drive")


def _out(s: Source) -> schemas.SourceOut:
    out = schemas.SourceOut.model_validate(s)
    out.has_credentials = s.credentials_enc is not None
    return out


@router.get("", response_model=list[schemas.SourceOut])
def list_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # RLS scopes rows to the caller's org (context set by get_current_user)
    rows = db.execute(select(Source).order_by(Source.name)).scalars().all()
    return [_out(s) for s in rows]


@router.post("", response_model=schemas.SourceOut)
def create_source(body: schemas.SourceIn, db: Session = Depends(get_db),
                  auth: tuple = Depends(require_org_admin)):
    _user, org_id, _role = auth
    if body.kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of: {', '.join(VALID_KINDS)}")
    if body.kind in ("local", "smb", "nfs"):
        root = str(body.config.get("root", "")).rstrip("/")
        if not root.startswith("/sources"):
            raise HTTPException(
                status_code=422,
                detail="File-based sources must point under /sources (the NESTBOX_SOURCES_PATH mount), "
                       "e.g. /sources/quality-records or /sources/scanner-output",
            )
        body.config["root"] = root
    source = Source(
        org_id=org_id,
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


@router.patch("/{source_id}", response_model=schemas.SourceOut)
def update_source(source_id: int, body: schemas.SourceIn, db: Session = Depends(get_db),
                  auth: tuple = Depends(require_org_admin)):
    # RLS: a source from another org is invisible → 404
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if body.kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of: {', '.join(VALID_KINDS)}")
    if source.config.get("builtin_inbox") and not body.is_active:
        raise HTTPException(status_code=422, detail="The scan inbox cannot be deactivated")
    source.name = body.name
    source.kind = body.kind
    source.config = {**body.config, **({"builtin_inbox": True} if source.config.get("builtin_inbox") else {})}
    source.is_monitored = body.is_monitored
    source.scan_cron = body.scan_cron
    source.is_active = body.is_active
    db.commit()
    return _out(source)


@router.post("/{source_id}/test")
def test_source(source_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Test that a source's path is readable from inside the container.

    For file-based sources (local, smb, nfs): verifies the root directory
    exists and lists files. Returns {ok, file_count} or {ok: false, detail}.
    For cloud sources: verifies credentials are valid by listing the drive.
    """
    # RLS hides other orgs' sources → 404
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        connector = connectors.get(source)
        files = connector.list_files()
        return {"ok": True, "file_count": len(files)}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
