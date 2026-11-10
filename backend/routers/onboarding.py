"""Bulk auto-categorization for organizations importing thousands of existing
records. Most keep files in folders named after the record type ("Calibration
Records/", "Training/"), so: summarize the folder tree, fuzzy-suggest a record
type per folder, apply in bulk, and optionally save each mapping as a
folder_rule so future scans categorize automatically.
"""
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_db
from models import FolderRule, Project, Record, RecordType, User
from security import get_current_user, require_org_admin

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def _folder_of(path_expr):
    # "a/b/c.pdf" -> "a/b" ; "c.pdf" -> ""
    return func.coalesce(
        func.nullif(func.regexp_replace(path_expr, r"/[^/]*$", ""), path_expr), ""
    )


def _suggest_type(folder: str, types: list[RecordType]) -> tuple[int | None, float]:
    """Fuzzy-match the folder's leaf name against record type names/codes."""
    leaf = folder.rsplit("/", 1)[-1].lower().replace("_", " ").replace("-", " ").strip()
    if not leaf:
        return None, 0.0
    best_id, best_score = None, 0.0
    for rt in types:
        for candidate in (rt.name.lower(), rt.code.lower(),
                          rt.name.lower().replace(" record", "").replace(" records", "")):
            score = SequenceMatcher(None, leaf, candidate).ratio()
            # containment bonus: "2026 calibration records" contains "calibration"
            if candidate and (candidate in leaf or leaf in candidate):
                score = max(score, 0.75)
            if score > best_score:
                best_id, best_score = rt.id, score
    if best_score >= 0.55:
        return best_id, best_score
    return None, best_score


@router.get("/folders")
def folder_summary(source_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    # RLS scopes records, record types and rules to the caller's org
    folder = _folder_of(Record.path).label("folder")
    rows = db.execute(
        select(
            folder,
            func.count().label("total"),
            func.count().filter(Record.record_type_id.is_(None)).label("untyped"),
            func.count().filter(Record.project_id.is_(None)).label("no_project"),
        )
        .where(Record.source_id == source_id,
               Record.status.notin_(("retired", "superseded")))
        .group_by("folder")
        .order_by("folder")
    ).all()

    types = db.execute(
        select(RecordType).where(RecordType.is_active.is_(True))
    ).scalars().all()
    rules = {
        r.folder_prefix: r
        for r in db.execute(
            select(FolderRule).where(FolderRule.source_id == source_id,
                                     FolderRule.is_active.is_(True))
        ).scalars()
    }

    out = []
    for row in rows:
        suggestion_id, confidence = _suggest_type(row.folder, types)
        rule = rules.get(row.folder)
        out.append({
            "folder": row.folder,
            "total": row.total,
            "untyped": row.untyped,
            "no_project": row.no_project,
            "suggested_record_type_id": suggestion_id,
            "confidence": round(confidence, 2),
            "rule_record_type_id": rule.record_type_id if rule else None,
            "rule_project_id": rule.project_id if rule else None,
        })
    return out


class FolderMapping(BaseModel):
    folder: str
    record_type_id: int | None = None
    project_id: int | None = None


class ApplyIn(BaseModel):
    source_id: int
    mappings: list[FolderMapping]
    overwrite: bool = False     # False = only fill in blanks
    save_rules: bool = True     # keep as folder_rules for future scans


@router.post("/apply")
def apply(body: ApplyIn, db: Session = Depends(get_db),
          auth: tuple = Depends(require_org_admin)):
    _user, org_id, _role = auth
    # RLS scopes records/types/rules to the caller's org; unknown cross-org
    # ids simply won't resolve.
    types = {rt.id: rt for rt in db.execute(select(RecordType)).scalars()}
    updated_total = 0

    for m in body.mappings:
        if not m.record_type_id and not m.project_id:
            continue
        if m.record_type_id and m.record_type_id not in types:
            raise HTTPException(status_code=422, detail=f"Unknown record type {m.record_type_id}")

        folder = _folder_of(Record.path)
        records = db.execute(
            select(Record).where(
                Record.source_id == body.source_id,
                folder == m.folder,
                Record.status.notin_(("retired", "superseded")),
            )
        ).scalars().all()

        for r in records:
            changed = False
            if m.record_type_id and (body.overwrite or r.record_type_id is None):
                r.record_type_id = m.record_type_id
                rt = types[m.record_type_id]
                if rt.retention_years:
                    base = r.filed_at or r.file_mtime or r.first_seen_at or datetime.now(timezone.utc)
                    r.retention_until = (base + timedelta(days=365 * rt.retention_years)).date()
                else:
                    r.retention_until = None
                changed = True
            if m.project_id and (body.overwrite or r.project_id is None):
                r.project_id = m.project_id
                changed = True
            if changed:
                updated_total += 1

        if body.save_rules:
            existing = db.execute(
                select(FolderRule).where(FolderRule.source_id == body.source_id,
                                         FolderRule.folder_prefix == m.folder)
            ).scalar_one_or_none()
            if existing:
                existing.record_type_id = m.record_type_id
                existing.project_id = m.project_id
                existing.is_active = True
            else:
                db.add(FolderRule(org_id=org_id, source_id=body.source_id, folder_prefix=m.folder,
                                  record_type_id=m.record_type_id, project_id=m.project_id))

    db.commit()
    return {"updated_records": updated_total, "rules_saved": body.save_rules}
