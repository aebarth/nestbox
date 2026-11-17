"""Test fixtures: a throwaway Postgres database built from db-init/, plus a
FastAPI TestClient. Point DATABASE_URL at any Postgres server; tests create
and drop their own `nestbox_test` database next to it.

Multi-org (schema v5): db-init creates the nestbox_app / nestbox_admin DB
roles. The app engine connects as nestbox_app so RLS is genuinely exercised
(a superuser connection would bypass it), and the worker engine as
nestbox_admin (BYPASSRLS) for cross-org operations. Schema management
(apply_migrations in the app lifespan) runs as the server role, since
migrations do DDL that nestbox_app may not.

Upgrade path (#10): NESTBOX_TEST_INIT_DIR points the database fixture at a
different schema snapshot (CI's backend-tests-upgraded job uses
db-init/legacy-v4 — a pre-multi-org install with data), after which the
explicit apply_migrations() call below performs the real v4 → v5 upgrade.
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

# Bootstrap connection: points to the EXISTING server database (e.g. `nestbox`).
# Used ONLY for the CREATE/DROP DATABASE step — you can't connect to nestbox_test
# to create nestbox_test.
BOOTSTRAP_URL = SERVER_URL

# Server-role connection to the test DB (valid only AFTER it exists).
# Used to run db-init (which creates nestbox_app/nestbox_admin) and apply
# migrations (DDL).
TEST_SERVER_URL = f"{_p.scheme}://{_p.username}:{_p.password}@{_hostport}/{TEST_DB}"

# The app itself connects as the RLS-subject role; worker as BYPASSRLS.
TEST_APP_URL = f"{_p.scheme}://nestbox_app:nestbox_app@{_hostport}/{TEST_DB}"
TEST_WORKER_URL = f"{_p.scheme}://nestbox_admin:nestbox_admin@{_hostport}/{TEST_DB}"

os.environ["DATABASE_URL"] = TEST_APP_URL
os.environ["WORKER_DB_URL"] = TEST_WORKER_URL
os.environ["SCHEMA_DB_URL"] = TEST_SERVER_URL
os.environ.setdefault("SECRET_KEY", "test-secret-key")

REPO_ROOT = Path(__file__).resolve().parents[2]

# Schema snapshot used to build the test database. Defaults to the current
# fresh-install DDL (db-init/, v5). CI's backend-tests-upgraded job points
# this at db-init/legacy-v4 so the suite runs against an upgraded schema.
_init_override = os.environ.get("NESTBOX_TEST_INIT_DIR", "").strip()
INIT_DIR = REPO_ROOT / _init_override if _init_override else REPO_ROOT / "db-init"
UPGRADE_PATH = "legacy-v4" in _init_override


@pytest.fixture(scope="session")
def database():
    import sqlalchemy
    from sqlalchemy import text

    # Connect to the EXISTING server DB (e.g. `nestbox`) to create/drop the
    # test database. This must NOT be TEST_SERVER_URL — that points at
    # nestbox_test which doesn't exist yet.
    admin = sqlalchemy.create_engine(BOOTSTRAP_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    admin.dispose()

    # Now connect to the test DB (which exists) as the server role to run
    # db-init. It creates nestbox_app/nestbox_admin and all tables.
    # raw DBAPI cursor: the audit SQL contains % characters that psycopg2
    # would otherwise treat as parameter placeholders.
    init_engine = sqlalchemy.create_engine(TEST_SERVER_URL)
    raw = init_engine.raw_connection()
    try:
        cur = raw.cursor()
        for sql_file in sorted(INIT_DIR.glob("*.sql")):
            cur.execute(sql_file.read_text())
        raw.commit()
    finally:
        raw.close()
    init_engine.dispose()

    # Apply pending migrations as the owner role, now, instead of relying on
    # the first TestClient's lifespan: on the upgrade path this is what runs
    # 005/006 against the legacy snapshot, and every fixture then sees a
    # deterministic schema state. On the fresh path this applies 006 (db-init
    # records versions 1-5) — same as the lifespan would have.
    from database import apply_migrations

    apply_migrations()

    from database import engine

    yield engine


@pytest.fixture()
def client(database):
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        yield c


def _default_org_id() -> int:
    """The first-created organization — the suite's canonical "default" org.

    Matches the `default_org_id` fixture so an API-scoped session and a raw
    ORM session agree on which org is "the default".
    """
    from sqlalchemy import text

    from database import SessionLocal

    with SessionLocal() as s:
        return s.execute(text(
            "SELECT id FROM organizations ORDER BY created_at LIMIT 1"
        )).scalar_one()


def _grant_admin_default_org_ownership() -> None:
    """Upgrade path only: make the suite's admin an owner of the org the
    migration backfilled legacy data into.

    On a fresh install the first-created org is the one setup makes for the
    admin, so every fixture agrees on "the default" org. On the upgrade path
    the migration creates the 'default' org (holding all legacy rows) before
    any user exists, and setup then gives the admin a *second* org — leaving
    org_db (first-created org) and admin_client (setup org) scoped to
    different orgs. A real v4 install would have had its pre-existing users
    backfilled as owners of the default org; this reproduces that for the
    test admin. Idempotent; no-ops when the user or org doesn't exist yet
    (e.g. right after a lifecycle reset rebuilt from the fresh snapshot).
    """
    from sqlalchemy import text

    from database import schema_engine

    with schema_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO org_members (user_id, org_id, role) "
            "SELECT u.id, o.id, 'owner' "
            "FROM users u CROSS JOIN organizations o "
            "WHERE u.username = 'admin' AND o.slug = 'default' "
            "ON CONFLICT (user_id, org_id) DO NOTHING"
        ))


@pytest.fixture()
def admin_client(client, database):
    """Logged-in admin client scoped to the default org (created on first use).

    The suite shares one session-scoped database. The org-admin tests make the
    acting install-admin an *owner* of every organization they create, so by
    the time later test files run the `admin` account belongs to several orgs.
    A multi-org login therefore returns a short-lived PENDING token with no
    `org_id` claim, which org-scoped endpoints reject with 403 "No
    organization selected". When that happens we resolve it exactly as the
    frontend does — pick the default (first-created) org via /select-org — so
    every test gets a deterministic, fully-scoped admin session on the default
    org. Single-org logins auto-select and skip this step entirely.

    Upgrade path: after setup (which creates the admin in a fresh org), the
    admin is also made an owner of the migration-created 'default' org — see
    _grant_admin_default_org_ownership — so the session scopes to it, keeping
    admin_client and org_db on the same org.
    """
    status = client.get("/api/auth/status").json()
    if status["needs_setup"]:
        r = client.post("/api/auth/setup", json={
            "username": "admin", "password": "adminpass1", "full_name": "Test Admin",
        })
        assert r.status_code == 200, r.text
        if not UPGRADE_PATH:
            return client

    if UPGRADE_PATH:
        _grant_admin_default_org_ownership()

    r = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    if r.json().get("needs_org_selection"):
        sel = client.post("/api/auth/select-org", json={"org_id": _default_org_id()})
        assert sel.status_code == 200, sel.text
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
