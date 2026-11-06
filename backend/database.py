import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://nestbox:nestbox@localhost:5432/nestbox"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def set_actor(db: Session, username: str) -> None:
    """Record who is acting so the audit_log trigger can attribute changes.

    SET LOCAL is transaction-scoped; sessions here use one transaction per
    request, so this must be called before any write and re-called after a
    commit if further writes follow.
    """
    db.execute(text("SET LOCAL app.username = :u"), {"u": username})


def bootstrap_schema_if_empty() -> None:
    """First boot against a blank database (image-based installs where the
    db-init folder isn't mounted into Postgres): apply db-init/*.sql ourselves."""
    from pathlib import Path

    with engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='schema_version'"
        )).scalar()
    if exists:
        return
    db_init = Path(__file__).parent / "db-init"
    if not db_init.is_dir():
        db_init = Path(__file__).parent.parent / "db-init"
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for sql_file in sorted(db_init.glob("*.sql")):
            cur.execute(sql_file.read_text())
        raw.commit()
    finally:
        raw.close()


# Stable id for the first-boot advisory lock. Any fixed integer works; it only
# needs to be consistent across processes so the API and worker serialize.
_SCHEMA_LOCK_KEY = 730142


def apply_migrations() -> None:
    """Apply backend/migrations/NNN_*.sql beyond the current schema_version.

    Wrapped in a blocking Postgres advisory lock so that simultaneous first
    boots (the API and the worker both call this at startup) serialize: the
    second caller waits until the first has finished bootstrapping + migrating,
    then sees the schema already present and no-ops. The lock is held on its own
    connection for the duration; bootstrap/migrations use separate pooled
    connections (default pool size 5 leaves headroom).
    """
    import re
    from pathlib import Path

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
        try:
            bootstrap_schema_if_empty()
            mig_dir = Path(__file__).parent / "migrations"
            files = sorted(p for p in mig_dir.glob("*.sql"))
            if not files:
                return
            with engine.begin() as mconn:
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
