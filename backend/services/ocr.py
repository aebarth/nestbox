"""Text extraction + content-based duplicate detection.

Strategy per record:
  1. try the PDF's embedded text layer (pypdf — fast, exact)
  2. if there's no real text layer (a raw scan), rasterize with poppler and
     OCR with Tesseract
Then store the text (full-text searchable via the generated ocr_tsv column)
and a normalized fingerprint. Two records whose fingerprints are >= 85%
trigram-similar are flagged as content duplicates — this catches the same
paper document scanned twice, which byte hashing never can.

Runs in the worker between scans, a small batch at a time.
"""
import io
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select, text as sqltext
from sqlalchemy.orm import Session

import connectors
from connectors.base import RemoteFile
from models import Alert, DuplicateGroup, Record, Source

log = logging.getLogger("nestbox.ocr")

MIN_EMBEDDED_CHARS = 80          # less than this => treat as scan, run OCR
MAX_PAGES = 25                   # extraction page cap per document
MAX_BYTES = 200 * 1024 * 1024    # skip anything bigger
FINGERPRINT_CHARS = 4000
CONTENT_SIMILARITY = 0.85
MIN_FINGERPRINT_CHARS = 120      # don't match near-empty documents


def extract_text(data: bytes) -> tuple[str, str]:
    """Return (text, method). Raises on unreadable input."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PAGES])
        if len(text.strip()) >= MIN_EMBEDDED_CHARS:
            return text, "embedded"
    except Exception as exc:
        log.debug("Embedded text extraction failed: %s", exc)

    import pytesseract
    from pdf2image import convert_from_bytes

    pages = convert_from_bytes(data, dpi=200, first_page=1, last_page=MAX_PAGES)
    text = "\n".join(pytesseract.image_to_string(img) for img in pages)
    return text, "tesseract"


def make_fingerprint(text: str) -> str:
    """Normalized text for similarity matching: lowercase, alphanumerics only,
    whitespace collapsed — OCR noise in punctuation/casing washes out."""
    norm = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm[:FINGERPRINT_CHARS]


def find_content_duplicates(db: Session, record: Record) -> None:
    if not record.text_fingerprint or len(record.text_fingerprint) < MIN_FINGERPRINT_CHARS:
        return
    rows = db.execute(sqltext("""
        SELECT id, duplicate_group_id,
               similarity(text_fingerprint, :fp) AS sim
        FROM records
        WHERE id <> :id
          AND text_fingerprint IS NOT NULL
          AND length(text_fingerprint) >= :minlen
          AND status NOT IN ('retired', 'superseded')
        ORDER BY text_fingerprint <-> :fp
        LIMIT 5
    """), {"fp": record.text_fingerprint, "id": record.id,
           "minlen": MIN_FINGERPRINT_CHARS}).all()
    matches = [r for r in rows if r.sim is not None and r.sim >= CONTENT_SIMILARITY]
    if not matches:
        return

    # already grouped together (e.g. exact-hash duplicates)? nothing to do
    if record.duplicate_group_id and all(
        m.duplicate_group_id == record.duplicate_group_id for m in matches
    ):
        return

    existing_ids = [g for g in {m.duplicate_group_id for m in matches} | {record.duplicate_group_id} if g]
    if existing_ids:
        group = db.get(DuplicateGroup, min(existing_ids))
        if group.is_dismissed:
            return
    else:
        group = DuplicateGroup(match_kind="content")
        db.add(group)
        db.flush()
        db.add(Alert(
            record_id=record.id,
            alert_type="duplicate",
            severity="warning",
            notes=f"{len(matches) + 1} documents have near-identical text content "
                  f"(best match {matches[0].sim:.0%}) — possibly the same paper "
                  "record scanned more than once.",
        ))
    record.duplicate_group_id = group.id
    for m in matches:
        db.get(Record, m.id).duplicate_group_id = group.id


def process_pending(db: Session, budget: int = 10) -> int:
    """Extract text for up to `budget` pending records. Returns count handled."""
    pending = db.execute(
        select(Record)
        .where(Record.ocr_status == "pending",
               Record.status.in_(("active", "inbox")))
        .order_by(Record.id)
        .limit(budget)
    ).scalars().all()
    if not pending:
        return 0

    db.execute(sqltext("SET LOCAL app.username = 'system'"))
    conns: dict[int, object] = {}
    for record in pending:
        try:
            if record.size_bytes > MAX_BYTES:
                record.ocr_status = "skipped"
                record.ocr_method = "too-large"
                continue
            if record.source_id not in conns:
                conns[record.source_id] = connectors.get(db.get(Source, record.source_id))
            connector = conns[record.source_id]
            rf = RemoteFile(external_id=record.external_id, path=record.path,
                            name=record.name, size=record.size_bytes, mtime=record.file_mtime)
            with connector.open_stream(rf) as fh:
                data = fh.read()
            text, method = extract_text(data)
            record.ocr_text = text[:1_000_000]
            record.ocr_method = method
            record.text_fingerprint = make_fingerprint(text)
            record.ocr_status = "done"
            record.ocr_at = datetime.now(timezone.utc)
            db.flush()
            find_content_duplicates(db, record)
        except Exception as exc:
            log.warning("OCR failed for record %s (%s): %s", record.id, record.path, exc)
            record.ocr_status = "failed"
            record.ocr_method = str(exc)[:200]
    db.commit()
    return len(pending)
