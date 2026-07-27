"""Nestbox worker: executes queued scan runs and fires scheduled ones.

Runs as its own container so long scans never block the API. Talks to the
backend only through Postgres:
  * every 10s: pick up queued scan_runs (the API's "Scan Now" button)
  * every minute: check cron schedules (global scan, weekly integrity,
    per-source overrides) and enqueue scheduled runs
"""
import logging
import time
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from database import SessionLocal
from models import ScanRun, Setting, Source
from services.scanner import execute_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("nestbox.worker")


def cron_matches(expr: str, minute: datetime) -> bool:
    try:
        return croniter.match(expr, minute)
    except (ValueError, KeyError):
        log.warning("Invalid cron expression: %r", expr)
        return False


def enqueue_scheduled(db, kind: str, source_id: int | None) -> None:
    exists = db.execute(
        select(ScanRun).where(
            ScanRun.status.in_(("queued", "running")),
            ScanRun.kind == kind,
            ScanRun.source_id.is_(None) if source_id is None else ScanRun.source_id == source_id,
        )
    ).scalars().first()
    if exists:
        return
    db.add(ScanRun(source_id=source_id, kind=kind, trigger="scheduled"))
    db.commit()
    log.info("Enqueued scheduled %s run (source_id=%s)", kind, source_id)


def check_schedules(db, minute: datetime) -> None:
    settings = {s.key: s.value for s in db.execute(select(Setting)).scalars()}
    global_cron = settings.get("schedule_cron")
    integrity_cron = settings.get("integrity_cron")
    if global_cron and cron_matches(global_cron, minute):
        enqueue_scheduled(db, "scan", None)
    if integrity_cron and cron_matches(integrity_cron, minute):
        enqueue_scheduled(db, "integrity", None)
    for source in db.execute(
        select(Source).where(Source.is_active.is_(True), Source.scan_cron.isnot(None))
    ).scalars():
        if source.scan_cron and cron_matches(source.scan_cron, minute):
            enqueue_scheduled(db, "scan", source.id)


def main() -> None:
    log.info("Nestbox worker started")
    try:
        from database import apply_migrations

        apply_migrations()
    except Exception:
        # backend may be bootstrapping the schema concurrently; the loop
        # below retries anyway
        log.exception("Startup schema check failed (will retry in the loop)")
    last_minute_checked = None
    while True:
        try:
            with SessionLocal() as db:
                minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
                if minute != last_minute_checked:
                    last_minute_checked = minute
                    check_schedules(db, minute)
                run = db.execute(
                    select(ScanRun).where(ScanRun.status == "queued").order_by(ScanRun.id)
                ).scalars().first()
                if run is not None:
                    log.info("Executing run %s (%s)", run.id, run.kind)
                    execute_run(db, run)
                    continue  # drain the queue before sleeping
        except Exception:
            log.exception("Worker loop error")
        time.sleep(10)


if __name__ == "__main__":
    main()
