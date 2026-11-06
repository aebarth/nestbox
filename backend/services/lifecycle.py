"""Database lifecycle: backup, restore, reset (Phase 2.1, #9).

Backups are `pg_dump -Fc` files written to NESTBOX_BACKUP_PATH (default
/backups), each with a SHA-256 checksum recorded in the `backups` table.
Restore verifies the checksum before running `pg_restore --clean`. A full
reset takes a pre-reset backup, then drops and rebuilds the public schema
from db-init/ — the install returns to first-boot state.

The `backups` table is deliberately outside the trigger-based audit loop
(retention pruning deletes rows), so every lifecycle action here writes an
explicit audit_log row with the acting user.

pg_dump/pg_restore are shell-invoked; the backend image ships
postgresql-client for that reason.
"""
import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

from models import Backup

log = logging.getLogger("nestbox.lifecycle")

DUMP_TIMEOUT_SECONDS = 3600


class LifecycleError(Exception):
    """Raised when a backup/restore/reset operation fails."""


def backup_dir() -> Path:
    d = Path(os.environ.get("NESTBOX_BACKUP_PATH", "/backups"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_params() -> dict:
    """Connection parameters for pg_dump/pg_restore, from DATABASE_URL.

    Read at call time (not import time) so tests can point the app at the
    test database before these functions run.
    """
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgresql+"):
        url = "postgresql" + url.split("+", 1)[1]
    p = urlparse(url)
    return {
        "host": p.hostname or "localhost",
        "port": str(p.port or 5432),
        "user": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "dbname": (p.path or "/").lstrip("/") or "postgres",
    }


def _pg_env() -> dict:
    env = dict(os.environ)
    env["PGPASSWORD"] = _db_params()["password"]
    return env


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit(db: Session, actor: str, action: str, row_pk: int | None, detail: dict) -> None:
    """Explicit audit_log entry for a lifecycle action (see module docstring).

    The detail is serialized to a JSON string here and cast with CAST():
    untyped text() parameters are not adapted to JSONB by psycopg2, and the
    `:name::jsonb` shorthand is NOT parsed as a bind parameter by SQLAlchemy
    2.0 (a name immediately followed by another colon renders literally), so
    an explicit CAST is required.
    """
    db.execute(
        text(
            "INSERT INTO audit_log (username, table_name, row_pk, action, new_row, changed_cols) "
            "VALUES (:u, 'lifecycle', :pk, :a, CAST(:d AS jsonb), '{}')"
        ),
        {"u": actor, "pk": row_pk, "a": action, "d": json.dumps(detail)},
    )


def create_backup(
    db: Session,
    kind: str = "manual",
    actor_username: str = "system",
    actor_user_id: int | None = None,
    notes: str = "",
) -> Backup:
    """Run pg_dump into the backup dir, record it with its checksum.

    Commits on success; raises LifecycleError (and rolls back) on failure,
    removing a partial dump file if one was left behind.
    """
    params = _db_params()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    import uuid

    filename = f"nestbox-{stamp}-{uuid.uuid4().hex[:8]}.dump"
    path = backup_dir() / filename
    cmd = [
        "pg_dump",
        "-h", params["host"], "-p", params["port"],
        "-U", params["user"], "-d", params["dbname"],
        "-Fc", "-f", str(path),
    ]
    try:
        proc = subprocess.run(cmd, env=_pg_env(), capture_output=True, text=True, timeout=DUMP_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        path.unlink(missing_ok=True)
        raise LifecycleError(f"pg_dump failed to run: {exc}") from exc
    if proc.returncode != 0:
        path.unlink(missing_ok=True)
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise LifecycleError("pg_dump failed: " + " | ".join(tail))

    sha = _sha256(path)
    backup = Backup(
        filename=filename,
        size_bytes=path.stat().st_size,
        sha256=sha,
        kind=kind,
        created_by=actor_user_id,
        notes=notes,
    )
    db.add(backup)
    db.flush()  # get the id for the audit row
    _audit(db, actor_username, "INSERT", backup.id, {
        "op": "backup", "filename": filename, "sha256": sha, "size_bytes": backup.size_bytes,
    })
    db.commit()
    log.info("Backup %s created (%d bytes) by %s", filename, backup.size_bytes, actor_username)
    return backup


def get_backup(db: Session, backup_id: int) -> Backup | None:
    return db.get(Backup, backup_id)


def verify_backup(backup: Backup) -> None:
    """Raise LifecycleError if the file is missing or its checksum drifted."""
    path = backup_dir() / backup.filename
    if not path.is_file():
        raise LifecycleError(f"Backup file {backup.filename} is missing from the backup directory")
    if _sha256(path) != backup.sha256:
        raise LifecycleError(
            f"Checksum mismatch for {backup.filename} — the file on disk does not match "
            f"the recorded SHA-256; refusing to restore a corrupted backup"
        )


def _terminate_other_sessions() -> None:
    """End every other session on this database so pg_restore can drop objects.

    Admin-initiated restores are expected to interrupt in-flight work; the
    engine's pool reconnects lazily (pool_pre_ping) after the restore.
    """
    from database import engine

    with engine.connect() as conn:
        pids = conn.execute(text(
            "SELECT pid FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )).scalars().all()
        for pid in pids:
            conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})


def restore_backup(db: Session, backup_id: int, actor_username: str = "system") -> dict:
    """Restore a verified backup over the current database.

    Sequence: verify checksum → release our own transaction → terminate other
    sessions → pg_restore --clean → write an audit row on the restored schema
    (in a fresh session; the caller's session is stale after the restore).
    """
    backup = get_backup(db, backup_id)
    if backup is None:
        raise LookupError(f"Backup {backup_id} not found")
    verify_backup(backup)

    # Release any locks our transaction holds before pg_restore drops objects.
    db.rollback()
    _terminate_other_sessions()

    params = _db_params()
    path = backup_dir() / backup.filename
    cmd = [
        "pg_restore",
        "-h", params["host"], "-p", params["port"],
        "-U", params["user"], "-d", params["dbname"],
        "--clean", "--if-exists", "--no-owner",
        "-j", "2",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, env=_pg_env(), capture_output=True, text=True, timeout=DUMP_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise LifecycleError(f"pg_restore failed to run: {exc}") from exc
    if proc.returncode != 0 and "ERROR" in (proc.stderr or ""):
        # pg_restore returns non-zero when it hits errors; --clean against a
        # live schema can report harmless 'does not exist' noise, so only fail
        # on real failures — but never stay silent about them.
        tail = [l for l in (proc.stderr or "").strip().splitlines() if "does not exist" not in l][-5:]
        if tail:
            raise LifecycleError("pg_restore failed: " + " | ".join(tail))

    # The caller's session is stale (its tables were dropped/recreated); audit
    # on a fresh one so the record survives even though the old log was wiped.
    from database import SessionLocal

    with SessionLocal() as fresh:
        _audit(fresh, actor_username, "UPDATE", backup.id, {
            "op": "restore", "filename": backup.filename, "sha256": backup.sha256,
        })
        fresh.commit()
    log.warning("Database restored from %s by %s", backup.filename, backup.sha256)
    return {"restored_from": backup.filename, "sha256": backup.sha256}


def reset_database(db: Session, actor_username: str = "system") -> dict:
    """Wipe all data and rebuild the schema from db-init/ (clean slate).

    Takes a pre-reset backup first so an accidental reset is recoverable.
    The audit log is part of the wiped data; one row on the fresh schema
    records that the reset happened and who did it.
    """
    pre = create_backup(db, kind="manual", actor_username=actor_username, notes="pre-reset backup")

    db.rollback()  # release our transaction's locks before DROP SCHEMA
    _terminate_other_sessions()

    from database import engine

    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        db_init = Path(__file__).resolve().parents[2] / "db-init"
        if not db_init.is_dir():
            db_init = Path(__file__).resolve().parent.parent / "db-init"
        for sql_file in sorted(db_init.glob("*.sql")):
            cur.execute(sql_file.read_text())
        # Record the reset on the fresh schema (audit_log is otherwise empty).
        cur.execute(
            "INSERT INTO audit_log (username, table_name, row_pk, action, new_row, changed_cols) "
            "VALUES (%s, 'lifecycle', NULL, 'UPDATE', %s::jsonb, '{}')",
            (actor_username, json.dumps({"op": "reset", "pre_reset_backup": pre.filename})),
        )
        raw.commit()
    finally:
        raw.close()

    log.warning("Database reset to clean slate by %s (pre-reset backup: %s)", actor_username, pre.filename)
    return {"reset": True, "pre_reset_backup": pre.filename}


def prune_old_backups(db: Session, retention: int | None = None, actor_username: str = "system") -> int:
    """Delete oldest backups beyond the retention count (default 14).

    Returns the number of backups pruned. Missing files are removed from the
    table anyway (they're unusable); a single summary audit row records it.
    """
    if retention is None:
        try:
            retention = max(1, int(os.environ.get("NESTBOX_BACKUP_RETENTION", "14")))
        except ValueError:
            retention = 14
    rows = db.execute(text(
        "SELECT id, filename FROM backups ORDER BY created_at DESC, id DESC OFFSET :keep"
    ), {"keep": retention}).all()
    if not rows:
        return 0
    removed = []
    for row in rows:
        path = backup_dir() / row.filename
        path.unlink(missing_ok=True)
        db.execute(text("DELETE FROM backups WHERE id = :id"), {"id": row.id})
        removed.append(row.filename)
    _audit(db, actor_username, "UPDATE", None, {"op": "prune", "removed": removed, "retention": retention})
    db.commit()
    log.info("Pruned %d old backup(s): %s", len(removed), removed)
    return len(removed)
