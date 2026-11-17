"""Upgrade-path rehearsal (#10): legacy v4 install → migrations 005/006.

CI's backend-tests-upgraded job builds the test database from
db-init/legacy-v4 — a pre-multi-org, single-org install carrying real data
(see that folder's README) — so the app's startup migration run performs the
actual v4 → v5 upgrade. These tests assert the acceptance criterion: an
existing single-org install upgrades with all data intact under the auto-
created "Default" org, with zero user action.

Skipped when the suite runs against the fresh-install (v5) snapshot.
"""
import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    "legacy-v4" not in os.environ.get("NESTBOX_TEST_INIT_DIR", ""),
    reason="only meaningful against the legacy-v4 snapshot (CI: backend-tests-upgraded)",
)


@pytest.fixture()
def owner_db(database):
    """Table-owner connection (bypasses RLS): sees every org's rows, so it
    can verify where the migration put things."""
    from database import schema_engine

    with schema_engine.connect() as conn:
        yield conn


def _default_org_id(conn) -> int:
    return conn.execute(text(
        "SELECT id FROM organizations WHERE slug = 'default'"
    )).scalar_one()


def test_migrations_applied(owner_db):
    versions = set(owner_db.execute(text("SELECT version FROM schema_version")).scalars())
    assert {1, 2, 3, 4, 5, 6} <= versions


def test_default_org_created_from_legacy_settings(owner_db):
    name, kind = owner_db.execute(text(
        "SELECT name, kind FROM organizations WHERE slug = 'default'"
    )).one()
    # The v4 seed's settings.org_name seeded the Default org's name; kind
    # defaults to personal.
    assert name == "My Organization"
    assert kind == "personal"


def test_db_roles_split_for_rls(owner_db):
    rows = owner_db.execute(text(
        "SELECT rolname, rolbypassrls FROM pg_roles "
        "WHERE rolname IN ('nestbox_app', 'nestbox_admin')"
    )).all()
    assert {r[0]: r[1] for r in rows} == {"nestbox_app": False, "nestbox_admin": True}


def test_legacy_rows_intact_under_default_org(owner_db):
    default_id = _default_org_id(owner_db)

    def orgs_of(sql: str) -> set:
        return set(owner_db.execute(text(sql)).scalars())

    # Every table the migration scopes carried at least one legacy row; each
    # must now sit under the Default org.
    assert orgs_of("SELECT org_id FROM sources WHERE id = 1") == {default_id}
    assert orgs_of("SELECT org_id FROM projects WHERE code IN ('PRJ-1', 'PRJ-2')") == {default_id}
    assert orgs_of("SELECT org_id FROM record_types WHERE code = 'DHR'") == {default_id}
    assert orgs_of("SELECT org_id FROM boxes WHERE box_number = 'B-001'") == {default_id}
    assert orgs_of(
        "SELECT org_id FROM records WHERE name IN ('one.pdf', 'two.pdf', 'three.pdf')"
    ) == {default_id}
    assert orgs_of("SELECT org_id FROM duplicate_groups WHERE id = 1") == {default_id}
    assert orgs_of("SELECT org_id FROM folder_rules WHERE id = 1") == {default_id}
    assert orgs_of("SELECT org_id FROM scan_runs WHERE id = 1") == {default_id}
    assert orgs_of("SELECT org_id FROM file_events WHERE id = 1") == {default_id}
    assert orgs_of("SELECT org_id FROM alerts WHERE id = 1") == {default_id}

    # Row content survived, not just the org stamp.
    size, hash_value = owner_db.execute(text(
        "SELECT size_bytes, hash_value FROM records WHERE name = 'three.pdf'"
    )).one()
    assert size == 200
    assert hash_value == "bb" * 32

    # The duplicate group kept both members.
    assert owner_db.execute(text(
        "SELECT count(*) FROM records WHERE duplicate_group_id = 1"
    )).scalar() == 2


def test_org_scoped_settings_moved(owner_db):
    default_id = _default_org_id(owner_db)
    got = dict(owner_db.execute(text(
        "SELECT key, value FROM org_settings WHERE org_id = :o"
    ), {"o": default_id}).all())
    assert got["schedule_cron"] == "0 2 * * *"
    assert got["integrity_cron"] == "0 3 * * 0"
    assert got["label_default_format"] == "avery5160"


def test_pre_upgrade_audit_rows_are_install_level(owner_db):
    # Audit rows written by the v4 trigger (before org_id existed) survive as
    # install-level (org_id NULL); the RLS policy restricts those to admins.
    n = owner_db.execute(text(
        "SELECT count(*) FROM audit_log WHERE org_id IS NULL "
        "AND table_name IN ('projects', 'records', 'boxes')"
    )).scalar()
    assert n >= 6
