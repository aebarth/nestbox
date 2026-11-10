from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import schemas
from database import get_db
from models import Project, RecordType
from security import get_current_user_with_org, require_org_admin

router = APIRouter(prefix="/api/lookups", tags=["lookups"])


@router.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(db: Session = Depends(get_db), _auth: tuple = Depends(get_current_user_with_org)):
    # RLS scopes to the caller's org (context set by get_current_user_with_org);
    # pending tokens (no org_id claim) are rejected with 403.
    return db.execute(select(Project).order_by(Project.code)).scalars().all()


@router.post("/projects", response_model=schemas.ProjectOut)
def create_project(body: schemas.LookupIn, db: Session = Depends(get_db),
                   auth: tuple = Depends(require_org_admin)):
    _user, org_id, _role = auth
    # RLS scopes the duplicate check to the caller's org; the (org_id, code)
    # unique constraint is the backstop against races.
    if db.execute(select(Project).where(Project.code == body.code)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Project code already exists")
    p = Project(org_id=org_id, code=body.code, name=body.name, is_active=body.is_active)
    db.add(p)
    db.commit()
    return p


@router.patch("/projects/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: int, body: schemas.LookupIn, db: Session = Depends(get_db),
                   auth: tuple = Depends(require_org_admin)):
    # RLS: projects from other orgs are invisible → 404
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    p.code, p.name, p.is_active = body.code, body.name, body.is_active
    db.commit()
    return p


@router.get("/record-types", response_model=list[schemas.RecordTypeOut])
def list_record_types(db: Session = Depends(get_db), _auth: tuple = Depends(get_current_user_with_org)):
    # RLS scopes to the caller's org; pending tokens rejected with 403.
    return db.execute(select(RecordType).order_by(RecordType.code)).scalars().all()


@router.post("/record-types", response_model=schemas.RecordTypeOut)
def create_record_type(body: schemas.LookupIn, db: Session = Depends(get_db),
                       auth: tuple = Depends(require_org_admin)):
    _user, org_id, _role = auth
    # RLS scopes the duplicate check to the caller's org; the (org_id, code)
    # unique constraint is the backstop against races.
    if db.execute(select(RecordType).where(RecordType.code == body.code)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Record type code already exists")
    rt = RecordType(org_id=org_id, code=body.code, name=body.name,
                    retention_years=body.retention_years, is_active=body.is_active)
    db.add(rt)
    db.commit()
    return rt


@router.patch("/record-types/{type_id}", response_model=schemas.RecordTypeOut)
def update_record_type(type_id: int, body: schemas.LookupIn, db: Session = Depends(get_db),
                       auth: tuple = Depends(require_org_admin)):
    # RLS: record types from other orgs are invisible → 404
    rt = db.get(RecordType, type_id)
    if rt is None:
        raise HTTPException(status_code=404, detail="Record type not found")
    rt.code, rt.name = body.code, body.name
    rt.retention_years, rt.is_active = body.retention_years, body.is_active
    db.commit()
    return rt
