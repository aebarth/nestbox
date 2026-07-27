from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import schemas
from database import get_db, set_actor
from models import Setting, User
from security import (
    check_login_allowed,
    clear_token,
    equalize_timing,
    get_current_user,
    hash_password,
    issue_token,
    record_login_failure,
    record_login_success,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """First-run detection: the frontend shows Setup until an admin exists."""
    user_count = db.execute(select(func.count()).select_from(User)).scalar()
    return {"needs_setup": user_count == 0}


@router.post("/setup", response_model=schemas.UserOut)
def setup(body: schemas.SetupIn, response: Response, db: Session = Depends(get_db)):
    user_count = db.execute(select(func.count()).select_from(User)).scalar()
    if user_count:
        raise HTTPException(status_code=403, detail="Setup already completed")
    set_actor(db, body.username)
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role="admin",
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    if body.org_name:
        db.merge(Setting(key="org_name", value=body.org_name))
    db.commit()
    issue_token(response, user.username)
    return user


@router.post("/login", response_model=schemas.UserOut)
def login(body: schemas.LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)):
    client = request.client.host if request.client else "unknown"
    check_login_allowed(client, body.username)
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None:
        equalize_timing()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        record_login_failure(client, body.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    record_login_success(client, body.username)
    set_actor(db, user.username)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    issue_token(response, user.username)
    return user


@router.post("/logout")
def logout(response: Response):
    clear_token(response)
    return {"ok": True}


@router.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(get_current_user)):
    return user
