import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db, set_org_context
from models import OrgMember, User

log = logging.getLogger("nestbox.security")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure")
if SECRET_KEY in ("dev-only-insecure", "change-me") or len(SECRET_KEY) < 16:
    log.critical(
        "SECRET_KEY is missing or weak. Set a strong value in .env "
        "(openssl rand -hex 32) — sessions and stored cloud credentials "
        "depend on it."
    )
TOKEN_TTL_HOURS = 12
PENDING_TOKEN_TTL_MINUTES = 5
COOKIE_NAME = "nestbox_token"
COOKIE_SECURE = os.environ.get("APP_BASE_URL", "").startswith("https")

# Brute-force lockout: max 5 failed logins per (client, username) per 15 min.
# In-memory is fine — a single backend container serves the app.
LOCKOUT_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 15 * 60
_failed_logins: dict[tuple[str, str], deque] = defaultdict(deque)
_failed_lock = Lock()

# Constant-time-ish response for unknown usernames: always do one bcrypt check.
_DUMMY_HASH = bcrypt.hashpw(b"nestbox-dummy-password", bcrypt.gensalt()).decode()


def check_login_allowed(client: str, username: str) -> None:
    key = (client, username.lower())
    now = time.monotonic()
    with _failed_lock:
        attempts = _failed_logins[key]
        while attempts and now - attempts[0] > LOCKOUT_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= LOCKOUT_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Too many failed sign-in attempts. Try again in 15 minutes.",
            )


def record_login_failure(client: str, username: str) -> None:
    with _failed_lock:
        _failed_logins[(client, username.lower())].append(time.monotonic())


def record_login_success(client: str, username: str) -> None:
    with _failed_lock:
        _failed_logins.pop((client, username.lower()), None)


def equalize_timing() -> None:
    """Run a bcrypt comparison so unknown-user and wrong-password paths take
    comparable time (blunts username enumeration via timing)."""
    bcrypt.checkpw(b"nestbox-dummy-password", _DUMMY_HASH.encode())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def issue_token(response: Response, username: str, org_id: int | None = None, org_role: str | None = None) -> None:
    """Issue a JWT with optional org claims and set it as an HTTP cookie."""
    claims: dict = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    if org_id is not None:
        claims["org_id"] = org_id
    if org_role is not None:
        claims["org_role"] = org_role
    token = jwt.encode(claims, SECRET_KEY, algorithm="HS256")
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=TOKEN_TTL_HOURS * 3600,
    )


def issue_pending_token(response: Response, username: str) -> None:
    """Short-lived token issued after a multi-org login, before an org is
    selected. Carries only `sub` + a `pending` flag; the sole consumer is
    /api/auth/select-org, which exchanges it for a full org-scoped token.
    Org-scoped endpoints reject it (no org_id claim → 403)."""
    claims = {
        "sub": username,
        "pending": True,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=PENDING_TOKEN_TTL_MINUTES),
    }
    token = jwt.encode(claims, SECRET_KEY, algorithm="HS256")
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=PENDING_TOKEN_TTL_MINUTES * 60,
    )


def clear_token(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def get_current_user(
    db: Session = Depends(get_db),
    nestbox_token: str | None = Cookie(default=None),
) -> User:
    """Authenticate the request and set org context for RLS.

    The JWT must carry org_id (issued at login/select-org). We verify the
    membership still exists (defense-in-depth beyond RLS) and set the
    session GUCs that drive both RLS filtering and audit attribution.
    """
    if not nestbox_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(nestbox_token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.execute(select(User).where(User.username == payload["sub"])).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account unavailable")

    org_id = payload.get("org_id")
    if org_id is not None:
        # Verify membership (defense-in-depth: RLS is the backstop, but we
        # want a clear 403 rather than silently returning zero rows).
        membership = db.execute(
            select(OrgMember).where(
                OrgMember.user_id == user.id,
                OrgMember.org_id == org_id,
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(status_code=403, detail="Not a member of this organization")
        set_org_context(db, user.username, user.id, org_id)
    else:
        # Install-level operation (setup, lifecycle) — no org scope.
        from database import set_actor
        set_actor(db, user.username)

    return user


def get_current_user_with_org(
    db: Session = Depends(get_db),
    nestbox_token: str | None = Cookie(default=None),
) -> tuple[User, int, str]:
    """Like get_current_user but requires org context. Returns (user, org_id, org_role).

    Use this dependency for all org-scoped endpoints. Raises 403 if the token
    lacks org claims or the membership no longer exists.
    """
    if not nestbox_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(nestbox_token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.execute(select(User).where(User.username == payload["sub"])).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account unavailable")

    org_id = payload.get("org_id")
    org_role = payload.get("org_role", "member")
    if org_id is None:
        raise HTTPException(status_code=403, detail="No organization selected")

    membership = db.execute(
        select(OrgMember).where(
            OrgMember.user_id == user.id,
            OrgMember.org_id == org_id,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    set_org_context(db, user.username, user.id, org_id)
    return user, org_id, membership.role


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Install-level admin (user.role == 'admin'). For lifecycle, user management."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_org_admin(auth: tuple = Depends(get_current_user_with_org)) -> tuple[User, int, str]:
    """Org-level admin (org_role in owner/admin). For org-scoped admin ops.

    MUST use Depends() to let FastAPI inject the db session and cookie into
    get_current_user_with_org — calling it directly would pass unresolved
    Depends/Cookie marker objects as arguments, causing JWT decode to fail.
    """
    user, org_id, org_role = auth
    if org_role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Organization admin access required")
    return user, org_id, org_role
