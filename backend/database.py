import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://nestbox_app:nestbox_app@localhost:5432/nestbox"
)

# Worker/admin operations use a BYPASSRLS role to work across orgs.
WORKER_DB_URL = os.environ.get(
    "WORKER_DB_URL", "postgresql+psycopg2://nestbox_admin:nestbox_admin@localhost:5432/nestbox"
)

# Schema management (first-boot bootstrap + migrations) must run as the table
# OWNER role: nestbox_app / nestbox_admin hold DML grants only and cannot
# CREATE/ALTER tables, create roles, or manage policies. In default installs
# that owner is the Postgres superuser 'nestbox'.
SCHEMA_DB_URL = os.environ.get(
    "SCHEMA_DB_URL", "postgresql+psycopg2://nestbox:nestbox@localhost:5432/nestbox"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
worker_engine = create_engine(WORKER_DB_URL, pool_pre_ping=True)
schema_engine = create_engine(SCHEMA_DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
WorkerSessionLocal = sessionmaker(bind=worker_engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def set_org_context(db: Session, username: str, user_id: int, org_id: int) -> None:
    """Set the RLS org scope and audit actor for this session.

    Uses plain SET (session-scoped), NOT SET LOCAL: a request may commit
    mid-flight (login, wizards, heartbeats) and then query again — with
    SET LOCAL the GUCs would vanish at COMMIT and every post-commit RLS
    query would fail closed. Session-scoped is safe because each request
    owns one connection for its lifetime and the auth dependency always
    re-sets these before any RLS access; a recycled pooled connection can
    never reach an RLS table without going through that dependency first.
    """
    db.execute(text("SET app.username = :u"), {"u": username})
    db.execute(text("SET app.user_id = :uid"), {"uid": str(user_id)})
    db.execute(text("SET app.org_id = :oid"), {"oid": str(org_id)})


def set_actor(db: Session, username: str) -> None:
    """Set only the audit actor (no org context).

    Used by install-level operations (lifecycle, setup) that don't have
    an org scope. Session-scoped for the same reason as set_org_context.
    """
    db.execute(text("SET app.username = :u"), {"u": username})


def bootstrap_schema_if_empty() -> None:
    """First boot against a blank database (image-based installs where the
    db-init folder isn't mounted into Postgres): apply db-init/*.sql ourselves."""
    from pathlib import Path

    with schema_engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='schema_version'"
        )).scalar()
    if exists:
        return
    db_init = Path(__file__).parent / "db-init"
    if not db_init.is_dir():
        db_init = Path(__file__).parent.parent / "db-init"
    raw = schema_engine.raw_connection()
    try:
        cur = raw.cursor()
        for sql_file in sorted(db_init.glob("*.sql")):
            cur.execute(sql_file.read_text())
        raw.commit()
    finally:
        raw.close()


# Stable id for the first-boot advisory lock. Any fixed integer works; it only
# needs to be consistent across processes so the API and the worker serialize.
_SCHEMA_LOCK_KEY = 730142


def apply_migrations() -> None:
    """Apply backend/migrations/NNN_*.sql beyond the current schema_version.

    Wrapped in a blocking Postgres advisory lock so that simultaneous first
    boots (the API and the worker both call this at startup) serialize: the
    second caller waits until the first has finished bootstrapping + migrating,
    then sees the schema already present and no-ops. The lock is held on its own
    connection for the duration; bootstrap/migrations use separate pooled
    connections (default pool size 5 leaves headroom).

    Runs as the table-owner role (SCHEMA_DB_URL): migrations do DDL, create
    roles, and manage RLS policies — none of which nestbox_app may do.
    """
    import re
    from pathlib import Path

    with schema_engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
        try:
            bootstrap_schema_if_empty()
            mig_dir = Path(__file__).parent / "migrations"
            files = sorted(p for p in mig_dir.glob("*.sql"))
            if not files:
                return
            with schema_engine.begin() as mconn:
                current = mconn.execute(text("SELECT max(version) FROM schema_version")).scalar() or 0
                for path in files:
                    m = re.match(r"^(\d+)_", path.name)
                    if not m:
                        continue
                    version = int(m.group(1))
                    if version <= current:
                        continue
                    mconn.execute(text(path.read_text()))
                    mconn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": version})
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEMA_LOCK_KEY})
