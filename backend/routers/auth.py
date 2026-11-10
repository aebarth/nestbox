from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import schemas
from database import get_db, set_actor
from models import Organization, OrgMember, RecordType, Source, User
from security import (
    COOKIE_NAME,
    SECRET_KEY,
    check_login_allowed,
    clear_token,
    equalize_timing,
    get_current_user,
    hash_password,
    issue_pending_token,
    issue_token,
    record_login_failure,
    record_login_success,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# --- Schemas for org-aware auth (local to avoid circular imports) ---

class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    org_name: str = "My Organization"
    org_kind: str = Field(default="personal", pattern="^(personal|business)$")


class SelectOrgIn(BaseModel):
    org_id: int


class OrgOut(BaseModel):
    id: int
    name: str
    kind: str
    role: str = "member"


class LoginOut(BaseModel):
    """Login response: either a token is set (single-org auto-select) or
    the client needs to pick an org."""
    needs_org_selection: bool = False
    orgs: list[OrgOut] = []
    user: schemas.UserOut | None = None


def _create_org_defaults(db: Session, org_id: int) -> None:
    """Insert per-org starter data when a new org is created."""
    from sqlalchemy import text
    db.execute(text("SET LOCAL app.org_id = :oid"), {"oid": str(org_id)})
    # Default record types (same as old global seed)
    defaults = [
        ("DHR", "Device History Record", None),
        ("DMR", "Device Master Record", None),
        ("TRN", "Training Record", 5),
        ("CAL", "Calibration Record", 5),
        ("SUP", "Supplier Record", 7),
        ("CAPA", "CAPA Record", 7),
        ("GEN", "General Document", None),
    ]
    for code, name, retention in defaults:
        db.add(RecordType(org_id=org_id, code=code, name=name, retention_years=retention))
    # Built-in scan inbox source
    db.add(Source(org_id=org_id, name="Scan Inbox", kind="local",
                  config={"root": "/inbox", "builtin_inbox": True}, is_monitored=True))


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """First-run detection: the frontend shows Setup until an admin exists."""
    user_count = db.execute(select(func.count()).select_from(User)).scalar()
    return {"needs_setup": user_count == 0}


@router.post("/setup", response_model=schemas.UserOut)
def setup(body: SetupIn, response: Response, db: Session = Depends(get_db)):
    """First-run setup: creates the admin user, their organization, and starter defaults."""
    user_count = db.execute(select(func.count()).select_from(User)).scalar()
    if user_count:
        raise HTTPException(status_code=403, detail="Setup already completed")

    set_actor(db, body.username)

    # Create the organization
    slug = body.org_name.lower().replace(" ", "-")[:32] or "default"
    org = Organization(name=body.org_name, slug=slug, kind=body.org_kind)
    db.add(org)
    db.flush()  # get org.id

    # Create the admin user
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role="admin",
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()  # get user.id

    # Create membership (owner)
    db.add(OrgMember(user_id=user.id, org_id=org.id, role="owner"))

    # Create per-org starter defaults
    _create_org_defaults(db, org.id)

    db.commit()
    issue_token(response, user.username, org_id=org.id, org_role="owner")
    return user


@router.post("/login", response_model=LoginOut)
def login(body: schemas.LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)):
    """Login with org auto-select or multi-org picker.

    - User in exactly 1 org → full token issued, needs_org_selection=False
    - User in multiple orgs → short-lived pending token + org list; the
      client must call /select-org to exchange it for a full token
    - User in 0 orgs → 403
    """
    client = request.client.host if request.client else "unknown"
    check_login_allowed(client, body.username)
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None:
        equalize_timing()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        record_login_failure(client, body.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    record_login_success(client, body.username)

    # Look up the user's org memberships (org_members is NOT RLS-restricted)
    memberships = db.execute(
        select(OrgMember, Organization).join(
            Organization, OrgMember.org_id == Organization.id
        ).where(
            OrgMember.user_id == user.id,
            Organization.is_active == True,  # noqa: E712
        )
    ).all()

    if not memberships:
        raise HTTPException(status_code=403, detail="No active organization. Contact your admin.")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    if len(memberships) == 1:
        # Auto-select the single org
        member, org = memberships[0]
        issue_token(response, user.username, org_id=org.id, org_role=member.role)
        return LoginOut(needs_org_selection=False, user=user)

    # Multiple orgs: pending token + list for the client to pick from
    issue_pending_token(response, user.username)
    org_list = [OrgOut(id=o.id, name=o.name, kind=o.kind, role=m.role) for m, o in memberships]
    return LoginOut(needs_org_selection=True, orgs=org_list, user=user)


@router.post("/select-org", response_model=LoginOut)
def select_org(body: SelectOrgIn, request: Request, response: Response,
               db: Session = Depends(get_db)):
    """Exchange a pending token (post multi-org login) for a full org-scoped
    token — or switch the active org when already holding a full token.

    Accepts either token shape from the cookie; in both cases membership in
    the requested org is verified server-side before issuing.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.execute(select(User).where(User.username == payload["sub"])).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account unavailable")

    membership = db.execute(
        select(OrgMember).where(
            OrgMember.user_id == user.id,
            OrgMember.org_id == body.org_id,
        )
    ).scalar_one_or_none()
    org = db.execute(
        select(Organization).where(
            Organization.id == body.org_id,
            Organization.is_active == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if membership is None or org is None:
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    issue_token(response, user.username, org_id=org.id, org_role=membership.role)
    return LoginOut(
        needs_org_selection=False,
        orgs=[OrgOut(id=org.id, name=org.name, kind=org.kind, role=membership.role)],
        user=user,
    )


@router.post("/logout")
def logout(response: Response):
    clear_token(response)
    return {"ok": True}


@router.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(get_current_user)):
    return user
