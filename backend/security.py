import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db, set_actor
from models import User

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure")
TOKEN_TTL_HOURS = 12
COOKIE_NAME = "nestbox_token"


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
