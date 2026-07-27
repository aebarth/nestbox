"""Integrity check: verify active records still exist and content is unchanged.

For local files we re-hash files whose mtime is UNCHANGED and compare to the
stored hash — a mismatch there means silent corruption, not an edit. Cloud
files are verified against the provider checksum. Hashing is budgeted per run
(oldest hashed_at first) so very large repositories rotate through over weeks.
"""
from sqlalchemy import select

import connectors
from models import Record, ScanRun, Source
from services.hashing import sha256_stream
from services.scanner import add_event, now, _snapshot

HASH_BUDGET_PER_RUN = 20_000


def check_source(db, source: Source, run: ScanRun) -> dict:
    connector = connectors.get(source)
    connector.test()
    stats = {"checked": 0, "hash_mismatch": 0, "missing": 0}

    records = db.execute(
        select(Record)
        .where(Record.source_id == source.id, Record.status == "active")
        .order_by(Record.hashed_at.asc().nulls_first())
        .limit(HASH_BUDGET_PER_RUN)
    ).scalars().all()

    listed = {}
    for f in connector.list_files():
        key = f.external_id or f.path
        listed[key] = f

    for r in records:
        stats["checked"] += 1
        f = listed.get(r.external_id or r.path)
        if f is None:
            old = _snapshot(r)
            r.status = "missing"
            add_event(db, r, run, "missing", old=old)
            stats["missing"] += 1
            continue
        if f.checksum:
            algo, value = f.checksum
            if r.hash_value and r.hash_algo == algo and r.hash_value != value:
                add_event(db, r, run, "hash_mismatch",
                          old={"hash": r.hash_value}, new={"hash": value})
                stats["hash_mismatch"] += 1
        elif r.hash_value and r.file_mtime and f.mtime == r.file_mtime:
            with connector.open_stream(f) as fh:
                current = sha256_stream(fh)
            if current != r.hash_value:
                add_event(db, r, run, "hash_mismatch",
                          old={"hash": r.hash_value}, new={"hash": current})
                stats["hash_mismatch"] += 1
            r.hashed_at = now()
    return stats
