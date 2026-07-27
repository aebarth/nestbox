from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import schemas
from database import get_db
from models import Project, RecordType, User
from security import get_current_user, require_admin

router = APIRouter(prefix="/api/lookups", tags=["lookups"])


@router.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.execute(select(Project).order_by(Project.code)).scalars().all()


@router.post("/projects", response_model=schemas.ProjectOut, dependencies=[Depends(require_admin)])
def create_project(body: schemas.LookupIn, db: Session = Depends(get_db)):
    if db.execute(select(Project).where(Project.code == body.code)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Project code already exists")
    p = Project(code=body.code, name=body.name, is_active=body.is_active)
    db.add(p)
    db.commit()
    return p


@router.patch("/projects/{project_id}", response_model=schemas.ProjectOut, dependencies=[Depends(require_admin)])
def update_project(project_id: int, body: schemas.LookupIn, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    p.code, p.name, p.is_active = body.code, body.name, body.is_active
    db.commit()
    return p


@router.get("/record-types", response_model=list[schemas.RecordTypeOut])
def list_record_types(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.execute(select(RecordType).order_by(RecordType.code)).scalars().all()


@router.post("/record-types", response_model=schemas.RecordTypeOut, dependencies=[Depends(require_admin)])
def create_record_type(body: schemas.LookupIn, db: Session = Depends(get_db)):
    if db.execute(select(RecordType).where(RecordType.code == body.code)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Record type code already exists")
    rt = RecordType(code=body.code, name=body.name,
                    retention_years=body.retention_years, is_active=body.is_active)
    db.add(rt)
    db.commit()
    return rt


@router.patch("/record-types/{type_id}", response_model=schemas.RecordTypeOut, dependencies=[Depends(require_admin)])
def update_record_type(type_id: int, body: schemas.LookupIn, db: Session = Depends(get_db)):
    rt = db.get(RecordType, type_id)
    if rt is None:
        raise HTTPException(status_code=404, detail="Record type not found")
    rt.code, rt.name = body.code, body.name
    rt.retention_years, rt.is_active = body.retention_years, body.is_active
    db.commit()
    return rt
