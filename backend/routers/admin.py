from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import schemas
from database import get_db
from models import AuditLog, Backup, Organization, OrgMember, Setting, User
from security import hash_password, require_admin
from services import lifecycle
from services import organizations as orgsvc
from services.lifecycle import LifecycleError, _audit as audit_install_level

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# Settings whose values must never be returned to the browser.
SECRET_SETTINGS = {"google_client_secret_enc", "graph_client_secret_enc"}


@router.get("/users", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db)):
    # users is install-level (not RLS): admins see every account
    return db.execute(select(User).order_by(User.username)).scalars().all()


class UserCreateIn(schemas.UserCreateIn):
    """Extends the base schema with optional org assignment.

    Since schema v5 a user with no org membership cannot log in, so new
    accounts should normally be created into an org. org_id is optional to
    keep existing callers working; when given, an org_members row is created.
    """
    org_id: int | None = None
    org_role: str = Field(default="member", pattern="^(owner|admin|member)$")


@router.post("/users", response_model=schemas.UserOut)
def create_user(body: UserCreateIn, db: Session = Depends(get_db)):
    if body.role not in ("admin", "user"):
        raise HTTPException(status_code=422, detail="Role must be admin or user")
    exists = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")
    if body.org_id is not None and db.get(Organization, body.org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    db.flush()  # get user.id
    if body.org_id is not None:
        db.add(OrgMember(user_id=user.id, org_id=body.org_id, role=body.org_role))
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
    # settings is install-level (not RLS); org-scoped keys live in org_settings
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
    # audit_log is RLS-scoped: an admin sees their org's rows plus
    # install-level (org_id IS NULL) events. A full cross-org audit view
    # needs a BYPASSRLS session — deferred to the org-admin UI work.
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
# Organization management (#10): org CRUD + member management
#
# organizations / org_members are install-level (not RLS-restricted) and are
# NOT covered by the trigger-based audit loop, so every mutation here writes
# an explicit install-level (org_id NULL) audit row via the owner-role engine
# — same pattern as the lifecycle operations. Note audit_log.action only
# allows INSERT/UPDATE, so removals are logged as UPDATE with op=remove_member.
# ---------------------------------------------------------------------------

class OrgCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="personal", pattern="^(personal|business)$")


class OrgUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: str | None = Field(default=None, pattern="^(personal|business)$")
    is_active: bool | None = None


class MemberAddIn(BaseModel):
    user_id: int | None = None
    username: str | None = None
    role: str = Field(default="member", pattern="^(owner|admin|member)$")


class MemberUpdateIn(BaseModel):
    role: str = Field(pattern="^(owner|admin|member)$")


def _org_out(o: Organization, member_count: int) -> dict:
    return {
        "id": o.id,
        "name": o.name,
        "slug": o.slug,
        "kind": o.kind,
        "is_active": o.is_active,
        "member_count": member_count,
        "created_at": o.created_at,
    }


def _member_counts(db: Session) -> dict[int, int]:
    rows = db.execute(select(OrgMember.org_id, func.count()).group_by(OrgMember.org_id)).all()
    return {org_id: n for org_id, n in rows}


def _get_membership(db: Session, org_id: int, user_id: int) -> OrgMember | None:
    return db.execute(
        select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == user_id)
    ).scalar_one_or_none()


def _owner_count(db: Session, org_id: int) -> int:
    return db.execute(
        select(func.count()).select_from(OrgMember).where(
            OrgMember.org_id == org_id, OrgMember.role == "owner"
        )
    ).scalar()


@router.get("/orgs")
def list_orgs(db: Session = Depends(get_db)):
    orgs = db.execute(
        select(Organization).order_by(Organization.created_at, Organization.id)
    ).scalars().all()
    counts = _member_counts(db)
    return [_org_out(o, counts.get(o.id, 0)) for o in orgs]


@router.post("/orgs", status_code=201)
def create_org(
    body: OrgCreateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    org = orgsvc.create_org_with_defaults(db, body.name, body.kind)
    audit_install_level(admin.username, "INSERT", org.id, {
        "op": "create_org", "name": org.name, "slug": org.slug, "kind": org.kind,
    })
    db.commit()
    return _org_out(org, 0)


@router.get("/orgs/{org_id}")
def get_org(org_id: int, db: Session = Depends(get_db)):
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    members = db.execute(
        select(OrgMember, User)
        .join(User, User.id == OrgMember.user_id)
        .where(OrgMember.org_id == org_id)
        .order_by(User.username)
    ).all()
    return {
        **_org_out(org, len(members)),
        "members": [
            {
                "user_id": u.id,
                "username": u.username,
                "full_name": u.full_name,
                "role": m.role,
                "is_active": u.is_active,
            }
            for m, u in members
        ],
    }


@router.patch("/orgs/{org_id}")
def update_org(
    org_id: int,
    body: OrgUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    changed: dict[str, tuple] = {}
    if body.name is not None and body.name != org.name:
        changed["name"] = (org.name, body.name)
        org.name = body.name  # slug stays stable — it identifies the org's file subtree
    if body.kind is not None and body.kind != org.kind:
        changed["kind"] = (org.kind, body.kind)
        org.kind = body.kind
    if body.is_active is not None and body.is_active != org.is_active:
        if not body.is_active:
            active_count = db.execute(
                select(func.count()).select_from(Organization).where(Organization.is_active == True)  # noqa: E712
            ).scalar()
            if active_count <= 1:
                raise HTTPException(
                    status_code=422,
                    detail="Cannot deactivate the last active organization",
                )
        changed["is_active"] = (org.is_active, body.is_active)
        org.is_active = body.is_active

    if changed:
        audit_install_level(admin.username, "UPDATE", org.id, {
            "op": "update_org",
            "old": {k: v[0] for k, v in changed.items()},
            "new": {k: v[1] for k, v in changed.items()},
        })
        db.commit()
    return get_org(org_id, db)


@router.post("/orgs/{org_id}/members", status_code=201)
def add_member(
    org_id: int,
    body: MemberAddIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if (body.user_id is None) == (body.username is None):
        raise HTTPException(status_code=422, detail="Provide exactly one of user_id or username")
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    if body.user_id is not None:
        user = db.get(User, body.user_id)
    else:
        user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if _get_membership(db, org_id, user.id) is not None:
        raise HTTPException(status_code=409, detail="User is already a member of this organization")

    db.add(OrgMember(user_id=user.id, org_id=org_id, role=body.role))
    audit_install_level(admin.username, "INSERT", user.id, {
        "op": "add_member", "org_id": org_id, "username": user.username, "role": body.role,
    })
    db.commit()
    return {"user_id": user.id, "username": user.username, "role": body.role}


@router.patch("/orgs/{org_id}/members/{user_id}")
def update_member(
    org_id: int,
    user_id: int,
    body: MemberUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    m = _get_membership(db, org_id, user_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Not a member of this organization")
    if m.role == "owner" and body.role != "owner" and _owner_count(db, org_id) <= 1:
        raise HTTPException(
            status_code=422,
            detail="An organization must keep at least one owner; promote another member first",
        )
    old_role = m.role
    m.role = body.role
    audit_install_level(admin.username, "UPDATE", user_id, {
        "op": "change_member_role", "org_id": org_id,
        "username": admin.username and user_id,  # placeholder replaced below
        "old_role": old_role, "new_role": body.role,
    })
    db.commit()
    return {"user_id": user_id, "role": body.role}


@router.delete("/orgs/{org_id}/members/{user_id}")
def remove_member(
    org_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    m = _get_membership(db, org_id, user_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Not a member of this organization")
    if m.role == "owner" and _owner_count(db, org_id) <= 1:
        raise HTTPException(
            status_code=422,
            detail="Cannot remove the last owner; transfer ownership first",
        )
    if user_id == admin.id:
        raise HTTPException(
            status_code=422,
            detail="You cannot remove your own membership; change roles or ask another admin",
        )
    username = db.execute(select(User.username).where(User.id == user_id)).scalar()
    db.delete(m)
    audit_install_level(admin.username, "UPDATE", user_id, {
        "op": "remove_member", "org_id": org_id, "username": username, "role": m.role,
    })
    db.commit()
    return {"ok": True}


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
    # backups is install-level (not RLS): full-DB dumps span all orgs
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
