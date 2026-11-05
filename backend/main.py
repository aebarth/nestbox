import logging
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import InterfaceError, OperationalError

from database import apply_migrations
from routers import (
    admin,
    alerts,
    auth,
    boxes,
    cloud,
    labels,
    lookups,
    onboarding,
    records,
    reports,
    scans,
    sources,
    wizard,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nestbox.api")


def _migration_attempts() -> int:
    """How many times to retry migrations on startup (env-overridable)."""
    try:
        return max(1, int(os.environ.get("NESTBOX_MIGRATION_RETRIES", "8")))
    except ValueError:
        return 8


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cold start: the API and worker can both bootstrap/migrate at once, and
    # Postgres may not yet be accepting connections. apply_migrations() is
    # serialized behind a pg_advisory_lock (see database.py), so concurrent
    # callers are safe; here we additionally retry with exponential backoff +
    # jitter so transient "not ready / still initializing" errors resolve on
    # their own instead of killing the container on the first blip. We only
    # retry connection-level failures — a real SQL error propagates immediately.
    attempts = _migration_attempts()
    delay = 0.5
    for attempt in range(1, attempts + 1):
        try:
            apply_migrations()
            break
        except (OperationalError, InterfaceError) as exc:
            if attempt >= attempts:
                logger.exception("Migrations failed after %d attempts; giving up", attempts)
                raise
            sleep = delay * (1 + random.random())  # full jitter
            detail = getattr(exc, "orig", None) or exc
            logger.warning(
                "Migration attempt %d/%d hit a connection error (%s); retrying in %.2fs",
                attempt, attempts, detail, sleep,
            )
            time.sleep(sleep)
            delay = min(delay * 2, 10.0)
    logger.info("Database schema is up to date")
    yield


app = FastAPI(
    title="Nestbox",
    description="Scanned records management: track, categorize, monitor and box your documents.",
    version="1.0.0",
    lifespan=lifespan,
)

for router in (auth, admin, sources, scans, records, lookups, boxes, alerts, labels, reports, wizard, cloud, onboarding):
    app.include_router(router.router)


@app.get("/api/health")
def health():
    return {"ok": True}
