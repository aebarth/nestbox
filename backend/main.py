import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_migrations()
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
