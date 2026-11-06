from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import schemas
from database import get_db
from models import AuditLog, Backup, Setting, User
from security import hash_password, require_admin
from services import lifecycle
from services.lifecycle import LifecycleError

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# Settings whose values must never be returned to the browser.
SECRET_SETTINGS = {"google_client_secret_enc", "graph_client_secret_enc"}


@router.get("/users", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.execute(select(User).order_by(User.username)).scalars().all()


@router.post("/users", response_model=schemas.UserOut)
def create_user(body: schemas.UserCreateIn, db: Session = Depends(get_db)):
    if body.role not in ("admin", "user"):
        raise HTTPException(status_code=422, detail="Role must be admin or user")
    exists = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    body: schemas.UserUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.is_active is False and user.id == admin.id:
        raise HTTPException(status_code=422, detail="You cannot deactivate your own account")
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        if body.role not in ("admin", "user"):
            raise HTTPException(status_code=422, detail="Role must be admin or user")
        if user.id == admin.id and body.role != "admin":
            raise HTTPException(status_code=422, detail="You cannot demote your own account")
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.password_hash = hash_password(body.password)
    db.commit()
    return user


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = db.execute(select(Setting)).scalars().all()
    out = {}
    for row in rows:
        if row.key in SECRET_SETTINGS:
            out[row.key] = {"configured": bool(row.value)}
        else:
            out[row.key] = row.value
    return out


@router.put("/settings")
def put_settings(body: dict, db: Session = Depends(get_db)):
    from services.crypto import encrypt_json

    for key, value in body.items():
        if key in SECRET_SETTINGS:
            if not value:
                continue  # blank input means "leave unchanged"
            value = encrypt_json({"secret": value}).decode()
        db.merge(Setting(key=key, value=value))
    db.commit()
    return {"ok": True}


@router.get("/audit")
def audit(
    table: str | None = None,
    row_pk: int | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.at.desc()).limit(min(limit, 500)).offset(offset)
    if table:
        q = q.where(AuditLog.table_name == table)
    if row_pk is not None:
        q = q.where(AuditLog.row_pk == row_pk)
    rows = db.execute(q).scalars().all()
    return [
        {
            "id": r.id,
            "at": r.at,
            "username": r.username,
            "table_name": r.table_name,
            "row_pk": r.row_pk,
            "action": r.action,
            "changed_cols": r.changed_cols,
            "old_row": r.old_row,
            "new_row": r.new_row,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Database lifecycle (Phase 2.1, #9): backup / restore / reset
# ---------------------------------------------------------------------------

class BackupCreateIn(BaseModel):
    notes: str = ""


class ConfirmIn(BaseModel):
    confirm: str


def _backup_out(b: Backup) -> dict:
    return {
        "id": b.id,
        "filename": b.filename,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "kind": b.kind,
        "created_by": b.created_by,
        "notes": b.notes,
        "created_at": b.created_at,
    }


@router.get("/backups")
def list_backups(db: Session = Depends(get_db)):
    rows = db.execute(select(Backup).order_by(Backup.created_at.desc(), Backup.id.desc())).scalars().all()
    return [_backup_out(b) for b in rows]


@router.post("/backups", status_code=201)
def create_backup_endpoint(
    body: BackupCreateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        backup = lifecycle.create_backup(
            db, kind="manual", actor_username=admin.username,
            actor_user_id=admin.id, notes=body.notes,
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return _backup_out(backup)


@router.post("/backups/{backup_id}/restore")
def restore_backup_endpoint(
    backup_id: int,
    body: ConfirmIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if body.confirm != "RESTORE":
        raise HTTPException(
            status_code=400,
            detail='Restore replaces the live database. Send {"confirm": "RESTORE"} to proceed.',
        )
    try:
        return lifecycle.restore_backup(db, backup_id, actor_username=admin.username)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except LifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/reset")
def reset_database_endpoint(
    body: ConfirmIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if body.confirm != "RESET":
        raise HTTPException(
            status_code=400,
            detail='Reset wipes ALL data (a pre-reset backup is taken automatically). '
                   'Send {"confirm": "RESET"} to proceed.',
        )
    try:
        return lifecycle.reset_database(db, actor_username=admin.username)
    except LifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
