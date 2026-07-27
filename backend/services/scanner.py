"""Scan a source, detect changes, raise alerts, group duplicates.

Identity rules:
  * cloud files: the provider file ID is stable — renames/moves are trivial
  * local files: identified by path; a disappeared path and a new path with
    the same (hash, size) are correlated into a single moved/renamed event so
    categorization, box assignment and history follow the file.
"""
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

import connectors
from connectors.base import RemoteFile
from models import Alert, DuplicateGroup, FileEvent, Record, ScanRun, Source
from services.hashing import sha256_stream

log = logging.getLogger("nestbox.scanner")

ALERT_SEVERITY = {
    "missing": "critical",
    "hash_mismatch": "critical",
    "modified": "warning",
    "duplicate": "warning",
    "moved": "info",
    "renamed": "info",
    "discovered": "info",
    "reappeared": "info",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def add_event(
    db: Session,
    record: Record,
    run: ScanRun | None,
    event_type: str,
    old: dict | None = None,
    new: dict | None = None,
    alert: bool = True,
) -> FileEvent:
    ev = FileEvent(
        record_id=record.id,
        scan_run_id=run.id if run else None,
        event_type=event_type,
        old_value=old,
        new_value=new,
    )
    db.add(ev)
    db.flush()
    if alert and event_type in ALERT_SEVERITY:
        db.add(
            Alert(
                file_event_id=ev.id,
                record_id=record.id,
                alert_type=event_type,
                severity=ALERT_SEVERITY[event_type],
            )
        )
    if event_type == "reappeared":
        # A found file supersedes its open "missing" alert.
        for a in db.execute(
            select(Alert).where(
                Alert.record_id == record.id,
                Alert.alert_type == "missing",
                Alert.status != "resolved",
            )
        ).scalars():
            a.status = "resolved"
            a.resolution = "false_positive"
            a.resolved_at = now()
            a.notes = (a.notes + " Auto-resolved: file reappeared.").strip()
    return ev


def _snapshot(r: Record) -> dict:
    return {"path": r.path, "name": r.name, "size": r.size_bytes,
            "mtime": r.file_mtime.isoformat() if r.file_mtime else None,
            "hash": r.hash_value}


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _file_snapshot(f: RemoteFile) -> dict:
    return {"path": f.path, "name": f.name, "size": f.size,
            "mtime": f.mtime.isoformat() if f.mtime else None}


def scan_source(db: Session, source: Source, run: ScanRun) -> dict:
    connector = connectors.get(source)
    connector.test()
    is_inbox = bool(source.config.get("builtin_inbox"))
    stats = {"seen": 0, "new": 0, "modified": 0, "moved": 0, "missing": 0, "reappeared": 0}

    existing = db.execute(
        select(Record).where(
            Record.source_id == source.id,
            Record.status.notin_(("retired", "superseded")),
        )
    ).scalars().all()
    by_ext = {r.external_id: r for r in existing if r.external_id}
    by_path = {r.path: r for r in existing if not r.external_id}
    seen_ids: set[int] = set()
    new_files: list[RemoteFile] = []

    for f in connector.list_files():
        stats["seen"] += 1
        record = by_ext.get(f.external_id) if f.external_id else by_path.get(f.path)
        if record is None:
            new_files.append(f)
            continue
        seen_ids.add(record.id)
        old = _snapshot(record)

        if record.path != f.path or record.name != f.name:
            same_dir = _dirname(record.path) == _dirname(f.path)
            etype = "renamed" if same_dir else "moved"
            record.path, record.name = f.path, f.name
            add_event(db, record, run, etype, old=old, new=_file_snapshot(f),
                      alert=source.is_monitored)
            stats["moved"] += 1

        content_changed = False
        if f.checksum:
            algo, value = f.checksum
            if record.hash_value and record.hash_algo == algo and record.hash_value != value:
                content_changed = True
            record.hash_algo, record.hash_value, record.hashed_at = algo, value, now()
        elif record.size_bytes != f.size or (
            f.mtime and record.file_mtime and f.mtime != record.file_mtime
        ):
            with connector.open_stream(f) as fh:
                new_hash = sha256_stream(fh)
            if record.hash_value and new_hash != record.hash_value:
                content_changed = True
            record.hash_algo, record.hash_value, record.hashed_at = "sha256", new_hash, now()

        if content_changed:
            add_event(db, record, run, "modified", old=old, new=_file_snapshot(f),
                      alert=source.is_monitored)
            stats["modified"] += 1

        record.size_bytes, record.file_mtime = f.size, f.mtime
        record.last_seen_at = now()
        if record.status == "missing":
            record.status = "inbox" if is_inbox else "active"
            add_event(db, record, run, "reappeared", new=_file_snapshot(f))
            stats["reappeared"] += 1

    # Hash unknown local files (bounded) so move-correlation can match them.
    def _hash_one(f: RemoteFile) -> tuple[RemoteFile, str | None, str | None]:
        if f.checksum:
            return f, f.checksum[0], f.checksum[1]
        try:
            with connector.open_stream(f) as fh:
                return f, "sha256", sha256_stream(fh)
        except Exception:
            log.warning("Could not hash %s", f.path)
            return f, None, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        hashed_new = list(pool.map(_hash_one, new_files))

    missing_candidates = [r for r in existing if r.id not in seen_ids and r.status != "missing"]

    # Correlate disappeared + appeared with identical content into a move.
    unmatched_new = []
    for f, algo, value in hashed_new:
        match = None
        if value:
            match = next(
                (r for r in missing_candidates
                 if r.hash_algo == algo and r.hash_value == value and r.size_bytes == f.size),
                None,
            )
        if match is not None:
            old = _snapshot(match)
            same_dir = _dirname(match.path) == _dirname(f.path)
            etype = "renamed" if same_dir else "moved"
            match.path, match.name = f.path, f.name
            match.size_bytes, match.file_mtime = f.size, f.mtime
            match.external_id = f.external_id or match.external_id
            match.last_seen_at = now()
            missing_candidates.remove(match)
            add_event(db, match, run, etype, old=old, new=_file_snapshot(f),
                      alert=source.is_monitored)
            stats["moved"] += 1
        else:
            unmatched_new.append((f, algo, value))

    for f, algo, value in unmatched_new:
        record = Record(
            source_id=source.id,
            external_id=f.external_id,
            path=f.path,
            name=f.name,
            size_bytes=f.size,
            file_mtime=f.mtime,
            hash_algo=algo,
            hash_value=value,
            hashed_at=now() if value else None,
            title=f.name.rsplit(".", 1)[0],
            status="inbox" if is_inbox else "active",
        )
        db.add(record)
        db.flush()
        add_event(db, record, run, "discovered", new=_file_snapshot(f),
                  alert=source.is_monitored and not is_inbox)
        stats["new"] += 1

    for r in missing_candidates:
        old = _snapshot(r)
        r.status = "missing"
        add_event(db, r, run, "missing", old=old)
        stats["missing"] += 1

    source.last_scan_at = now()
    detect_duplicates(db)
    return stats


def detect_duplicates(db: Session) -> int:
    """Group live records with identical content (per hash algo), plus a
    name+size fallback for records whose hashes aren't comparable."""
    live = db.execute(
        select(Record).where(
            Record.status.notin_(("retired", "superseded", "missing")),
        )
    ).scalars().all()

    groups: dict[tuple, list[Record]] = {}
    for r in live:
        if r.hash_value:
            groups.setdefault(("hash", r.hash_algo, r.hash_value, r.size_bytes), []).append(r)
    hashed_ids = set()
    for key, members in groups.items():
        if len(members) > 1:
            hashed_ids.update(m.id for m in members)
    for r in live:
        if r.id not in hashed_ids:
            groups.setdefault(("name_size", r.name.lower(), r.size_bytes), []).append(r)

    new_groups = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # cross-algo name_size buckets containing already-hash-grouped files are noise
        if key[0] == "name_size" and all(m.id in hashed_ids for m in members):
            continue
        existing_group_ids = {m.duplicate_group_id for m in members if m.duplicate_group_id}
        if existing_group_ids:
            gid = min(existing_group_ids)
            group = db.get(DuplicateGroup, gid)
            if group.is_dismissed:
                continue
        else:
            group = DuplicateGroup(match_kind="hash" if key[0] == "hash" else "name_size")
            db.add(group)
            db.flush()
            gid = group.id
            new_groups += 1
            db.add(
                Alert(
                    record_id=members[0].id,
                    alert_type="duplicate",
                    severity="warning",
                    notes=f"{len(members)} files share the same "
                          f"{'content' if key[0] == 'hash' else 'name and size'}.",
                )
            )
        for m in members:
            if m.duplicate_group_id != gid:
                m.duplicate_group_id = gid
    return new_groups


def execute_run(db: Session, run: ScanRun) -> None:
    """Worker entry: execute one queued scan_run inside the caller's session."""
    run.status = "running"
    run.started_at = now()
    db.commit()

    try:
        if run.source_id:
            sources = [db.get(Source, run.source_id)]
        else:
            sources = db.execute(
                select(Source).where(Source.is_active.is_(True), Source.is_monitored.is_(True))
            ).scalars().all()

        totals: dict[str, int] = {}
        errors = []
        for source in sources:
            if source is None or not source.is_active:
                continue
            try:
                if run.kind == "integrity":
                    from services.integrity import check_source

                    stats = check_source(db, source, run)
                else:
                    stats = scan_source(db, source, run)
                for k, v in stats.items():
                    totals[k] = totals.get(k, 0) + v
            except Exception as exc:
                log.error("Scan failed for %s: %s", source.name, traceback.format_exc())
                errors.append(f"{source.name}: {exc}")
        run.stats = totals
        run.status = "done" if not errors else "error"
        run.error_text = "\n".join(errors) if errors else None
    except Exception as exc:
        run.status = "error"
        run.error_text = str(exc)
        log.error("Run %s failed: %s", run.id, traceback.format_exc())
    finally:
        run.finished_at = now()
        db.commit()
