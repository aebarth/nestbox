"""Test fixtures: a throwaway Postgres database built from db-init/, plus a
FastAPI TestClient. Point DATABASE_URL at any Postgres server; tests create
and drop their own `nestbox_test` database next to it.
"""
import os
from pathlib import Path

import pytest

SERVER_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://nestbox:nestbox@localhost:55432/nestbox"
)
TEST_URL = SERVER_URL.rsplit("/", 1)[0] + "/nestbox_test"
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key")

DB_INIT = Path(__file__).resolve().parents[2] / "db-init"


@pytest.fixture(scope="session")
def database():
    import sqlalchemy
    from sqlalchemy import text

    admin = sqlalchemy.create_engine(SERVER_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS nestbox_test WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE nestbox_test"))
    admin.dispose()

    from database import engine

    # raw DBAPI cursor: the audit SQL contains % characters that psycopg2
    # would otherwise treat as parameter placeholders
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for sql_file in sorted(DB_INIT.glob("*.sql")):
            cur.execute(sql_file.read_text())
        raw.commit()
    finally:
        raw.close()
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
def db_session(database):
    from database import SessionLocal

    with SessionLocal() as session:
        yield session
