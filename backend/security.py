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

from database import get_db, set_actor
from models import User

log = logging.getLogger("nestbox.security")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure")
if SECRET_KEY in ("dev-only-insecure", "change-me") or len(SECRET_KEY) < 16:
    log.critical(
        "SECRET_KEY is missing or weak. Set a strong value in .env "
        "(openssl rand -hex 32) — sessions and stored cloud credentials "
        "depend on it."
    )
TOKEN_TTL_HOURS = 12
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


def issue_token(response: Response, username: str) -> None:
    token = jwt.encode(
        {
            "sub": username,
            "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=TOKEN_TTL_HOURS * 3600,
    )


def clear_token(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def get_current_user(
    db: Session = Depends(get_db),
    nestbox_token: str | None = Cookie(default=None),
) -> User:
    if not nestbox_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(nestbox_token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    user = db.execute(select(User).where(User.username == payload["sub"])).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account unavailable")
    set_actor(db, user.username)
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
