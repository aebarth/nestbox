import csv
import io
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Alert, Box, DuplicateGroup, Record, ScanRun, Source, User
from security import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])

RECORD_COLS = [
    "id", "title", "name", "path", "source", "status", "project", "record_type",
    "lot_number", "part_number", "box", "retention_until", "last_seen_at",
]


def _record_row(r: Record) -> list:
    return [
        r.id, r.title, r.name, r.path,
        r.source.name if r.source else "",
        r.status,
        r.project.code if r.project else "",
        r.record_type.code if r.record_type else "",
        r.lot_number, r.part_number,
        r.box.box_number if r.box else "",
        r.retention_until.isoformat() if r.retention_until else "",
        r.last_seen_at.isoformat() if r.last_seen_at else "",
    ]


def _records_query():
    return select(Record).options(
        joinedload(Record.source), joinedload(Record.project),
        joinedload(Record.record_type), joinedload(Record.box),
    )


def _report_data(name: str, db: Session):
    today = date.today()
    if name == "missing":
        rows = db.execute(
            _records_query().where(Record.status == "missing")
        ).unique().scalars().all()
        return RECORD_COLS, [_record_row(r) for r in rows]
    if name == "duplicates":
        rows = db.execute(
            _records_query()
            .join(DuplicateGroup, Record.duplicate_group_id == DuplicateGroup.id)
            .where(DuplicateGroup.is_dismissed.is_(False))
            .order_by(Record.duplicate_group_id)
        ).unique().scalars().all()
        cols = ["duplicate_group"] + RECORD_COLS
        return cols, [[r.duplicate_group_id] + _record_row(r) for r in rows]
    if name == "uncategorized":
        rows = db.execute(
            _records_query().where(
                Record.status.in_(("active", "inbox")),
                (Record.record_type_id.is_(None)) | (Record.project_id.is_(None)),
            )
        ).unique().scalars().all()
        return RECORD_COLS, [_record_row(r) for r in rows]
    if name == "retention-due":
        rows = db.execute(
            _records_query().where(
                Record.status == "active",
                Record.retention_until.isnot(None),
                Record.retention_until <= today + timedelta(days=365),
            ).order_by(Record.retention_until)
        ).unique().scalars().all()
        return RECORD_COLS, [_record_row(r) for r in rows]
    if name == "box-inventory":
        rows = db.execute(
            _records_query().where(Record.box_id.isnot(None),
                                   Record.status.notin_(("retired", "superseded")))
            .order_by(Record.box_id)
        ).unique().scalars().all()
        return RECORD_COLS, [_record_row(r) for r in rows]
    if name == "all-records":
        rows = db.execute(
            _records_query().where(Record.status.notin_(("retired", "superseded")))
        ).unique().scalars().all()
        return RECORD_COLS, [_record_row(r) for r in rows]
    raise HTTPException(status_code=404, detail="Unknown report")


@router.get("/status-summary")
def status_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    by_status = dict(db.execute(select(Record.status, func.count()).group_by(Record.status)).all())
    by_source = dict(
        db.execute(
            select(Source.name, func.count())
            .join(Record, Record.source_id == Source.id)
            .where(Record.status.notin_(("retired", "superseded")))
            .group_by(Source.name)
        ).all()
    )
    open_alerts = dict(
        db.execute(
            select(Alert.severity, func.count()).where(Alert.status != "resolved")
            .group_by(Alert.severity)
        ).all()
    )
    dupe_groups = db.execute(
        select(func.count()).select_from(DuplicateGroup)
        .where(DuplicateGroup.is_dismissed.is_(False))
    ).scalar()
    last_scan = db.execute(
        select(ScanRun).where(ScanRun.status == "done").order_by(ScanRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    boxes = db.execute(select(func.count()).select_from(Box)).scalar()
    live = Record.status.notin_(("retired", "superseded"))
    uncategorized = db.execute(
        select(func.count()).select_from(Record)
        .where(live, Record.record_type_id.is_(None))
    ).scalar()
    ocr_pending = db.execute(
        select(func.count()).select_from(Record)
        .where(Record.ocr_status == "pending", Record.status.in_(("active", "inbox")))
    ).scalar()
    return {
        "uncategorized": uncategorized,
        "ocr_pending": ocr_pending,
        "by_status": by_status,
        "by_source": by_source,
        "open_alerts": open_alerts,
        "duplicate_groups": dupe_groups,
        "boxes": boxes,
        "last_scan": {
            "id": last_scan.id,
            "finished_at": last_scan.finished_at,
            "stats": last_scan.stats,
        } if last_scan else None,
        "generated_at": datetime.now(timezone.utc),
    }


def _csv_safe(value):
    """Blunt spreadsheet formula injection: file/lot/part values are untrusted
    and Excel executes cells starting with = + - @."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t"):
        return "'" + value
    return value


@router.get("/{name}.csv")
def report_csv(name: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cols, rows = _report_data(name, db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    writer.writerows([[_csv_safe(v) for v in row] for row in rows])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="nestbox-{name}.csv"'},
    )


@router.get("/{name}.pdf")
def report_pdf(name: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    cols, rows = _report_data(name, db)
    keep = [0, 1, 4, 5, 6, 7, 8, 9, 10, 11]  # fit the page: drop name/path/last_seen
    cols = [cols[i] if i < len(cols) else "" for i in keep]
    rows = [[str(row[i])[:40] if i < len(row) else "" for i in keep] for row in rows]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                            leftMargin=0.5 * inch, rightMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"Nestbox report: {name}", styles["Title"]),
        Paragraph(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — {len(rows)} rows",
                  styles["Normal"]),
        Spacer(1, 12),
    ]
    table = Table([cols] + rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
    ]))
    story.append(table)
    doc.build(story)
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="nestbox-{name}.pdf"'},
    )
