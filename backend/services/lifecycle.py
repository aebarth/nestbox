"""Database lifecycle: backup, restore, reset (Phase 2.1, #9).

Backups are `pg_dump -Fc` files written to NESTBOX_BACKUP_PATH (default
/backups), each with a SHA-256 checksum recorded in the `backups` table.
Restore verifies the checksum, drops and recreates the public schema, then
runs pg_restore into the empty schema. A full reset takes a pre-reset backup,
then drops and rebuilds the public schema from db-init/ — the install returns
to first-boot state.

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

# Subprocess timeout for pg_dump / pg_restore. 120 s is generous for the
# small databases this app targets; if a restore truly hangs (lock, I/O)
# we want a fast, clear failure rather than a multi-hour CI stall.
DUMP_TIMEOUT_SECONDS = 120
# Lock waits during restore/reset fail after this long instead of blocking
# forever (a session can grab a lock after _terminate_other_sessions() runs).
LOCK_TIMEOUT_SQL = "120s"


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
        proc = subprocess.run(
            cmd, env=_pg_env(), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=DUMP_TIMEOUT_SECONDS,
        )
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
    """End every other session on this database so schema operations can proceed.

    Admin-initiated restores/resets are expected to interrupt in-flight work;
    the engine's pool reconnects lazily (pool_pre_ping) afterwards.
    """
    from database import engine

    with engine.connect() as conn:
        pids = conn.execute(text(
            "SELECT pid FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )).scalars().all()
        for pid in pids:
            conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})


def _drop_and_recreate_schema() -> None:
    """Drop and recreate the public schema on a dedicated raw connection.

    Uses SET LOCAL lock_timeout so that if a session grabs a lock after
    _terminate_other_sessions() ran, we fail fast instead of hanging.
    The raw connection is separate from the engine pool so it isn't affected
    by the terminate (and won't poison pooled connections).
    """
    from database import engine

    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_SQL}'")
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        # Restore default privileges that Postgres sets up for new schemas.
        cur.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
        raw.commit()
    finally:
        raw.close()


def restore_backup(db: Session, backup_id: int, actor_username: str = "system") -> dict:
    """Restore a verified backup over the current database.

    Sequence: verify checksum → extract file metadata → release our own
    transaction → terminate other sessions → DROP SCHEMA public + recreate →
    pg_restore into empty schema → write an audit row on the restored schema
    (in a fresh session; the caller's session is stale after the restore).

    We drop and recreate the schema ourselves rather than relying on
    pg_restore --clean. The --clean flag issues individual DROP commands for
    each object (tables, indexes, triggers, FKs) which can deadlock on
    inter-object dependencies in ways that are hard to diagnose. Dropping the
    entire schema in one atomic DDL statement avoids all of that — it's the
    same approach reset_database uses and is proven to work in CI.
    """
    backup = get_backup(db, backup_id)
    if backup is None:
        raise LookupError(f"Backup {backup_id} not found")

    # Extract all data we need from the ORM object NOW. After db.rollback()
    # below, the instance's attributes are expired and the backups table will
    # no longer exist once we drop the schema — any later attribute access
    # would trigger a lazy-load against a missing table.
    backup_id_val = backup.id
    backup_filename = backup.filename
    backup_sha256 = backup.sha256

    verify_backup(backup)

    # Release any locks our transaction holds before the schema drop.
    db.rollback()
    _terminate_other_sessions()
    _drop_and_recreate_schema()

    params = _db_params()
    path = backup_dir() / backup_filename
    # No --clean: the schema is already empty, so pg_restore just creates
    # everything fresh. This avoids any lock contention during restore.
    cmd = [
        "pg_restore",
        "-h", params["host"], "-p", params["port"],
        "-U", params["user"], "-d", params["dbname"],
        "--no-owner",
        str(path),
    ]
    env = _pg_env()
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=DUMP_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise LifecycleError(f"pg_restore failed to run: {exc}") from exc
    if proc.returncode != 0:
        # pg_restore can return non-zero for warnings; check if it's a real
        # failure by looking for ERROR lines (not just NOTICE/WARNING).
        stderr_lines = (proc.stderr or "").strip().splitlines()
        error_lines = [l for l in stderr_lines if "ERROR" in l][-5:]
        if error_lines:
            raise LifecycleError("pg_restore failed: " + " | ".join(error_lines))

    # The caller's session is stale (its tables were dropped/recreated); audit
    # on a fresh one so the record survives even though the old log was wiped.
    from database import SessionLocal

    with SessionLocal() as fresh:
        _audit(fresh, actor_username, "UPDATE", backup_id_val, {
            "op": "restore", "filename": backup_filename, "sha256": backup_sha256,
        })
        fresh.commit()
    log.warning("Database restored from %s by %s", backup_filename, backup_sha256)
    return {"restored_from": backup_filename, "sha256": backup_sha256}


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
        # Fail fast if a session grabs a lock after the terminate above,
        # instead of blocking the DROP forever. Applies to this transaction
        # (the whole drop + rebuild runs in one).
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_SQL}'")
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
