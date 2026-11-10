"""Test fixtures: a throwaway Postgres database built from db-init/, plus a
FastAPI TestClient. Point DATABASE_URL at any Postgres server; tests create
and drop their own `nestbox_test` database next to it.

Multi-org (schema v5): db-init creates the nestbox_app / nestbox_admin DB
roles. The app engine connects as nestbox_app so RLS is genuinely exercised
(a superuser connection would bypass it), and the worker engine as
nestbox_admin (BYPASSRLS) for cross-org operations. Schema management
(apply_migrations in the app lifespan) runs as the server role, since
migrations do DDL that nestbox_app may not.
"""
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

SERVER_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://nestbox:nestbox@localhost:55432/nestbox"
)
TEST_DB = "nestbox_test"

_p = urlparse(SERVER_URL)
_hostport = f"{_p.hostname}:{_p.port}"
# Server-role connection, used to create/drop the test DB, run db-init
# (which creates the app roles), and apply migrations (DDL).
TEST_SERVER_URL = f"{_p.scheme}://{_p.username}:{_p.password}@{_hostport}/{TEST_DB}"
# The app itself connects as the RLS-subject role; worker as BYPASSRLS.
TEST_APP_URL = f"{_p.scheme}://nestbox_app:nestbox_app@{_hostport}/{TEST_DB}"
TEST_WORKER_URL = f"{_p.scheme}://nestbox_admin:nestbox_admin@{_hostport}/{TEST_DB}"

os.environ["DATABASE_URL"] = TEST_APP_URL
os.environ["WORKER_DB_URL"] = TEST_WORKER_URL
os.environ["SCHEMA_DB_URL"] = TEST_SERVER_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key")

DB_INIT = Path(__file__).resolve().parents[2] / "db-init"


@pytest.fixture(scope="session")
def database():
    import sqlalchemy
    from sqlalchemy import text

    admin = sqlalchemy.create_engine(TEST_SERVER_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    admin.dispose()

    # Apply db-init as the server role (it creates nestbox_app/nestbox_admin).
    # raw DBAPI cursor: the audit SQL contains % characters that psycopg2
    # would otherwise treat as parameter placeholders.
    init_engine = sqlalchemy.create_engine(TEST_SERVER_URL)
    raw = init_engine.raw_connection()
    try:
        cur = raw.cursor()
        for sql_file in sorted(DB_INIT.glob("*.sql")):
            cur.execute(sql_file.read_text())
        raw.commit()
    finally:
        raw.close()
    init_engine.dispose()

    from database import engine

    yield engine


@pytest.fixture()
def client(database):
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_client(client, database):
    """Logged-in admin client (creates the account on first use)."""
    status = client.get("/api/auth/status").json()
    if status["needs_setup"]:
        r = client.post("/api/auth/setup", json={
            "username": "admin", "password": "adminpass1", "full_name": "Test Admin",
        })
        assert r.status_code == 200, r.text
    else:
        r = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass1"})
        assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def default_org_id(db_session):
    """ID of the org created during first-run setup."""
    from sqlalchemy import text
    row = db_session.execute(text(
        "SELECT id FROM organizations ORDER BY created_at LIMIT 1"
    )).scalar_one()
    return row


@pytest.fixture()
def org_db(db_session, default_org_id):
    """A session with RLS context set to the default org + test actor.

    Use this (instead of raw db_session) when a test needs to insert or
    assert rows in org-scoped tables directly via the ORM — inserts carry
    an explicit org_id and the policy's WITH CHECK requires the GUC.
    """
    from database import set_org_context
    set_org_context(db_session, "admin", 1, default_org_id)
    yield db_session


@pytest.fixture()
def db_session(database):
    from database import SessionLocal

    with SessionLocal() as session:
        yield session
