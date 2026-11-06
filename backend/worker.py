"""Nestbox worker: executes queued scan runs and fires scheduled ones.

Runs as its own container so long scans never block the API. Talks to the
backend only through Postgres:
  * every 10s: pick up queued scan_runs (the API's "Scan Now" button)
  * every minute: check cron schedules (global scan, weekly integrity,
    per-source overrides, scheduled database backups) and enqueue/execute

Run claiming is lease-based: a run is claimed with UPDATE ... FOR UPDATE SKIP
LOCKED and stamped with locked_at/lease_until. A worker that dies mid-run
leaves the run 'running' past its lease; the reaper below returns it to
'queued' so it is retried instead of stuck forever.
"""
import logging
import time
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select, text

from database import SessionLocal
from models import ScanRun, Setting, Source
from services.scanner import RUN_LEASE_SECONDS, execute_run

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

    # Scheduled database backups (Phase 2.1, #9): settings key 'backup_cron',
    # e.g. "0 3 * * *" for 03:00 daily. Runs inline (not via scan_runs) and
    # prunes old dumps to NESTBOX_BACKUP_RETENTION afterwards. Failures are
    # logged, never fatal to the loop.
    backup_cron = settings.get("backup_cron")
    if backup_cron and cron_matches(backup_cron, minute):
        try:
            from services import lifecycle

            lifecycle.create_backup(db, kind="scheduled", actor_username="system")
            lifecycle.prune_old_backups(db)
        except Exception:
            log.exception("Scheduled database backup failed")


def reap_stale_runs(db) -> None:
    """Return 'running' runs past their lease to 'queued'.

    Covers a worker that crashed or was OOM-killed mid-run: without this the run
    would sit in 'running' forever. Only runs whose lease_until has passed are
    touched, so an actively-heartbeating run is never disturbed.
    """
    ids = db.execute(
        text(
            """
            UPDATE scan_runs
               SET status = 'queued', locked_at = NULL, lease_until = NULL
             WHERE status = 'running'
               AND lease_until IS NOT NULL
               AND lease_until < now()
            RETURNING id
            """
        )
    ).scalars().all()
    if ids:
        db.commit()
        log.warning("Reaped %d stale run(s) past their lease: %s", len(ids), list(ids))


def claim_next_run(db) -> ScanRun | None:
    """Atomically claim the oldest queued run.

    UPDATE ... FOR UPDATE SKIP LOCKED guarantees two workers can never grab the
    same row, and stamping locked_at/lease_until makes a crashed worker's run
    recoverable (see reap_stale_runs). Returns the claimed ORM object or None.
    """
    run_id = db.execute(
        text(
            """
            UPDATE scan_runs
               SET status = 'running',
                   started_at = COALESCE(started_at, now()),
                   locked_at = now(),
                   lease_until = now() + make_interval(secs => :lease)
             WHERE id = (
                 SELECT s.id FROM scan_runs s
                  WHERE s.status = 'queued'
                  ORDER BY s.id
                  LIMIT 1
                  FOR UPDATE SKIP LOCKED
             )
            RETURNING id
            """
        ),
        {"lease": RUN_LEASE_SECONDS},
    ).scalar_one_or_none()
    if run_id is None:
        return None
    db.commit()
    return db.get(ScanRun, run_id)


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
                reap_stale_runs(db)
                run = claim_next_run(db)
                if run is not None:
                    log.info("Executing run %s (%s)", run.id, run.kind)
                    execute_run(db, run)
                    continue  # drain the queue before sleeping
                from services import ocr

                if ocr.process_pending(db, budget=10) == 10:
                    continue  # more OCR waiting; keep going without sleeping
        except Exception:
            log.exception("Worker loop error")
        time.sleep(10)


if __name__ == "__main__":
    main()
