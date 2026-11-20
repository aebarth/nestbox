"""Per-organization storage locations (#15).

View, change and probe an org's inbox / sources-root / backup paths under
the install-level mounts (/inbox, /sources, /backups), plus org-scoped
backup/restore (PR B) into the org's own backup path.

Auth: install admins (users.role == 'admin') may view and manage any org;
org owners/admins may only view and manage their own (current) org. The spec
draft placed these under /api/admin, but that router rejects every
non-install-admin, which conflicts with the "org admins manage their own
storage" requirement — hence /api/storage with per-endpoint authz.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_db, set_org_context
from models import AuditLog, Backup, Organization, OrgSetting, Record, Source, User
from security import get_current_user_with_org
from services import org_backup, storage as storage_svc

router = APIRouter(prefix="/api/storage", tags=["storage"])


def _authorize(auth: tuple, org_id: int) -> User:
    """Install admins may manage any org; everyone else only their own."""
    user, my_org_id, my_role = auth
    if user.role == "admin":
        return user
    if my_role not in ("owner", "admin") or org_id != my_org_id:
        raise HTTPException(status_code=403, detail="You can only manage storage for your own organization")
    return user


def _org_entry(db: Session, user: User, org: Organization) -> dict:
    # Scope RLS to this org so its settings/sources are visible (an install
    # admin's session is normally scoped to a different org).
    set_org_context(db, user.username, user.id, org.id)
    eff = storage_svc.effective_storage(db, org)
    record_count = db.execute(
        select(func.count()).select_from(Record).where(Record.org_id == org.id)
    ).scalar()
    file_source_count = db.execute(
        select(func.count()).select_from(Source).where(
            Source.org_id == org.id, Source.kind.in_(storage_svc.FILE_BASED_KINDS)
        )
    ).scalar()
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "storage": eff,
        "health": {loc: storage_svc.health_check(p) for loc, p in eff.items()},
        "record_count": record_count,
        "file_source_count": file_source_count,
    }


@router.get("")
def get_storage(db: Session = Depends(get_db), auth: tuple = Depends(get_current_user_with_org)):
    """Install mount roots + per-org storage locations with health checks.

    Install admins see every active org; org owners/admins see only their own.
    """
    user, my_org_id, my_role = auth
    if user.role != "admin" and my_role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Organization admin access required")
    orgs = db.execute(
        select(Organization).where(Organization.is_active == True)  # noqa: E712
        .order_by(Organization.created_at, Organization.id)
    ).scalars().all()
    if user.role != "admin":
        orgs = [o for o in orgs if o.id == my_org_id]
    return {
        "install": [
            {
                "location": loc,
                "container_path": root,
                "host_path": storage_svc.host_path(loc),
                "health": storage_svc.health_check(root),
            }
            for loc, (root, _, _) in storage_svc.LOCATIONS.items()
        ],
        "orgs": [_org_entry(db, user, o) for o in orgs],
    }


class StorageUpdateIn(BaseModel):
    inbox_root: str | None = None
    sources_root: str | None = None
    backup_path: str | None = None
    # Required to change sources_root on an org that already has records.
    confirm_sources_change: bool = False


@router.put("/org/{org_id}")
def update_storage(org_id: int, body: StorageUpdateIn, db: Session = Depends(get_db),
                   auth: tuple = Depends(get_current_user_with_org)):
    """Change an org's storage locations (all fields optional).

    Guardrails: changing sources_root on an org with existing records requires
    confirm_sources_change=true — existing file paths are NOT migrated. A
    non-empty old inbox folder produces a warning (unfiled PDFs stop being
    discovered). Changing the backup path never deletes existing dumps.
    """
    user = _authorize(auth, org_id)
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    set_org_context(db, user.username, user.id, org_id)

    current = storage_svc.effective_storage(db, org)
    new = dict(current)
    for location, field in (("inbox", "inbox_root"), ("sources", "sources_root"), ("backups", "backup_path")):
        value = getattr(body, field)
        if value is not None:
            try:
                new[location] = storage_svc.validate_location(location, value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))

    warnings: list[str] = []
    if new["sources"] != current["sources"]:
        has_records = db.execute(
            select(func.count()).select_from(Record).where(Record.org_id == org_id)
        ).scalar()
        if has_records and not body.confirm_sources_change:
            raise HTTPException(
                status_code=422,
                detail="This organization already has records. Changing the sources root will NOT move "
                       "existing file paths — record paths would go stale (re-scan to re-discover them). "
                       "Re-send with confirm_sources_change=true to proceed.",
            )
        if has_records:
            warnings.append("Existing records keep their old paths; run a scan to re-discover files under the new root.")
    if new["inbox"] != current["inbox"]:
        try:
            old = Path(current["inbox"])
            if old.is_dir() and any(old.iterdir()):
                warnings.append(
                    f"The previous inbox folder ({old}) is not empty; "
                    "unfiled PDFs there will no longer be discovered."
                )
        except OSError:
            pass

    changed = {loc: (current[loc], new[loc]) for loc in new if new[loc] != current[loc]}
    if changed:
        for location, (_old, value) in changed.items():
            db.merge(OrgSetting(org_id=org_id, key=storage_svc.LOCATIONS[location][1], value=value))
            storage_svc.ensure_dir(value)
        # Keep the built-in Scan Inbox source pointed at the org's inbox root
        if "inbox" in changed:
            for s in db.execute(select(Source).where(Source.org_id == org_id)).scalars().all():
                if s.config.get("builtin_inbox"):
                    s.config = {**s.config, "root": new["inbox"]}
        # Explicit audit row (same pattern as the org-management endpoints).
        db.add(AuditLog(
            at=datetime.now(timezone.utc), username=user.username, table_name="org_settings",
            row_pk=org_id, action="UPDATE",
            old_row={"op": "update_storage", **{loc: v[0] for loc, v in changed.items()}},
            new_row={"op": "update_storage", **{loc: v[1] for loc, v in changed.items()}},
            changed_cols=sorted(changed), org_id=org_id,
        ))
        db.commit()

    return {
        "ok": True,
        "storage": new,
        "health": {loc: storage_svc.health_check(p) for loc, p in new.items()},
        "warnings": warnings,
    }


class StorageTestIn(BaseModel):
    location: str = Field(pattern="^(inbox|sources|backups)$")
    org_id: int | None = None  # default: the caller's current org


@router.post("/test")
def test_storage_location(body: StorageTestIn, db: Session = Depends(get_db),
                          auth: tuple = Depends(get_current_user_with_org)):
    """Writability probe for an org's storage location (creates the folder if missing)."""
    user, my_org_id, _ = auth
    org_id = body.org_id if body.org_id is not None else my_org_id
    user = _authorize(auth, org_id)
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    set_org_context(db, user.username, user.id, org_id)
    path = storage_svc.effective_storage(db, org)[body.location]
    return storage_svc.probe_writable(path)


# ---------------------------------------------------------------------------
# Org-scoped backup / restore (PR B). Install-level full-DB backups stay in
# routers/admin.py + services/lifecycle.py; these operate on one org only.
# ---------------------------------------------------------------------------

class OrgBackupCreateIn(BaseModel):
    notes: str = ""


class OrgRestoreIn(BaseModel):
    target_org_id: int | None = None  # default: the backup's own org
    confirm: bool = False
    # Wipe the target's existing document data + projects before filling.
    replace: bool = False


def _org_backup_out(b: Backup) -> dict:
    return {
        "id": b.id,
        "filename": b.filename,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "kind": b.kind,
        "org_id": b.org_id,
        "created_by": b.created_by,
        "notes": b.notes,
        "created_at": b.created_at,
    }


@router.post("/org/{org_id}/backups")
def create_org_backup(org_id: int, body: OrgBackupCreateIn, db: Session = Depends(get_db),
                      auth: tuple = Depends(get_current_user_with_org)):
    """Run an org-scoped backup (data + file tree zip) into the org's backup path."""
    user = _authorize(auth, org_id)
    try:
        b = org_backup.create_org_backup(db, org_id, actor_username=user.username,
                                         actor_user_id=user.id, notes=body.notes)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Org backup failed: {exc}")
    return _org_backup_out(b)


@router.get("/org/{org_id}/backups")
def list_org_backups(org_id: int, db: Session = Depends(get_db),
                     auth: tuple = Depends(get_current_user_with_org)):
    """List this org's backups (newest first)."""
    _authorize(auth, org_id)
    rows = db.execute(
        select(Backup).where(Backup.org_id == org_id, Backup.kind == "org")
        .order_by(Backup.created_at.desc(), Backup.id.desc())
    ).scalars().all()
    return [_org_backup_out(b) for b in rows]


@router.post("/org/{org_id}/backups/{backup_id}/restore")
def restore_org_backup(org_id: int, backup_id: int, body: OrgRestoreIn,
                       db: Session = Depends(get_db), auth: tuple = Depends(get_current_user_with_org)):
    """Restore an org backup into a target org (fresh ids, remapped FKs).

    Fills an empty org by default; with replace=true the target's existing
document data + projects are wiped first (atomic with the fill). Org admins
may only restore their own org's backups into their own org; install admins
may target any org.
    """
    user = _authorize(auth, org_id)
    backup = db.get(Backup, backup_id)
    if backup is None or backup.org_id != org_id:
        raise HTTPException(status_code=404, detail="Backup not found for this organization")
    target = body.target_org_id if body.target_org_id is not None else org_id
    if target != org_id:
        _authorize(auth, target)
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Pass confirm=true to run the restore")
    try:
        return org_backup.restore_org_backup(db, backup_id, target, actor_username=user.username,
                                             replace=body.replace)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except org_backup.OrgBackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Org restore failed: {exc}")
