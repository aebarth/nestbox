from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import schemas
from database import get_db, set_actor
from models import Setting, User
from security import (
    clear_token,
    get_current_user,
    hash_password,
    issue_token,
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
def login(body: schemas.LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
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
