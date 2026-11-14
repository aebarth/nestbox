from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import schemas
from database import get_db, set_actor
from models import Organization, OrgMember, User
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
from services.organizations import create_org_with_defaults

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


class OrgListOut(BaseModel):
    """The caller's active org memberships + which one is currently selected.

    `current` mirrors the token's org claim (null while holding a pending
    pre-selection token); `orgs` lists every active membership so the client
    can render either the login picker or the header switcher.
    """
    current: OrgOut | None = None
    orgs: list[OrgOut] = []


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

    # Create the admin user first so the org service can record the owner
    # membership in the same transaction.
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role="admin",
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()  # get user.id

    org = create_org_with_defaults(db, body.org_name, body.org_kind, owner_user_id=user.id)

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


@router.get("/orgs", response_model=OrgListOut)
def my_orgs(request: Request, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """The caller's active org memberships and which one is currently selected.

    Powers the login picker and the header org switcher. Works with either a
    full token (`current` set) or a short-lived pending token (`current` null,
    `orgs` still populated so the client can render the picker). Returns 401
    when there is no valid session at all.
    """
    current_org_id = None
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            current_org_id = pyjwt.decode(token, SECRET_KEY, algorithms=["HS256"]).get("org_id")
        except pyjwt.PyJWTError:
            pass

    memberships = db.execute(
        select(OrgMember, Organization).join(
            Organization, OrgMember.org_id == Organization.id
        ).where(
            OrgMember.user_id == user.id,
            Organization.is_active == True,  # noqa: E712
        )
    ).all()
    orgs = [OrgOut(id=o.id, name=o.name, kind=o.kind, role=m.role) for m, o in memberships]
    current = next((o for o in orgs if o.id == current_org_id), None)
    return OrgListOut(current=current, orgs=orgs)


@router.post("/logout")
def logout(response: Response):
    clear_token(response)
    return {"ok": True}


@router.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(get_current_user)):
    return user
