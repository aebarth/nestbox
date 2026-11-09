-- 005_multi_org.sql — Phase 2.2: Multi-organization support (#10)
--
-- Adds organizations, org_members, org_settings; scopes all data tables
-- with org_id + RLS; splits DB roles so RLS is actually enforced;
-- updates the audit trigger to capture org context.
--
-- Runs as the existing table-owner role (nestbox superuser in default
-- installs). Safe to run once; guarded by schema_version like all migrations.

BEGIN;

-- ============================================================================
-- 1. New tables
-- ============================================================================

CREATE TABLE organizations (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL DEFAULT 'personal' CHECK (kind IN ('personal', 'business')),
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE org_members (
    user_id BIGINT NOT NULL REFERENCES users(id),
    org_id  BIGINT NOT NULL REFERENCES organizations(id),
    role    TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    PRIMARY KEY (user_id, org_id)
);
CREATE INDEX org_members_org_idx ON org_members (org_id);

CREATE TABLE org_settings (
    org_id     BIGINT NOT NULL REFERENCES organizations(id),
    key        TEXT NOT NULL,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, key)
);

-- ============================================================================
-- 2. Backfill: create Default org + memberships
-- ============================================================================

INSERT INTO organizations (name, slug, kind)
SELECT
    COALESCE(
        NULLIF((SELECT value->>'org_name' FROM settings WHERE key = 'org_name'), ''),
        'My Organization'
    ),
    'default',
    'personal';

INSERT INTO org_members (user_id, org_id, role)
SELECT u.id, o.id,
       CASE WHEN u.role = 'admin' THEN 'owner' ELSE 'member' END
FROM users u
CROSS JOIN organizations o WHERE o.slug = 'default';

-- ============================================================================
-- 3. Add org_id to all data tables
--    (nullable first → backfill → NOT NULL → FK)
-- ============================================================================

ALTER TABLE sources          ADD COLUMN org_id BIGINT;
ALTER TABLE projects         ADD COLUMN org_id BIGINT;
ALTER TABLE record_types     ADD COLUMN org_id BIGINT;
ALTER TABLE boxes            ADD COLUMN org_id BIGINT;
ALTER TABLE duplicate_groups ADD COLUMN org_id BIGINT;
ALTER TABLE records          ADD COLUMN org_id BIGINT;
ALTER TABLE folder_rules     ADD COLUMN org_id BIGINT;
ALTER TABLE scan_runs        ADD COLUMN org_id BIGINT;
ALTER TABLE file_events      ADD COLUMN org_id BIGINT;
ALTER TABLE alerts           ADD COLUMN org_id BIGINT;

-- Backfill all existing rows to the Default org
DO $$
DECLARE o_id BIGINT;
BEGIN
    SELECT id INTO o_id FROM organizations WHERE slug = 'default';
    UPDATE sources          SET org_id = o_id WHERE org_id IS NULL;
    UPDATE projects         SET org_id = o_id WHERE org_id IS NULL;
    UPDATE record_types     SET org_id = o_id WHERE org_id IS NULL;
    UPDATE boxes            SET org_id = o_id WHERE org_id IS NULL;
    UPDATE duplicate_groups SET org_id = o_id WHERE org_id IS NULL;
    UPDATE records          SET org_id = o_id WHERE org_id IS NULL;
    UPDATE folder_rules     SET org_id = o_id WHERE org_id IS NULL;
    UPDATE scan_runs        SET org_id = o_id WHERE org_id IS NULL;
    UPDATE file_events      SET org_id = o_id WHERE org_id IS NULL;
    UPDATE alerts           SET org_id = o_id WHERE org_id IS NULL;
END $$;

-- Enforce NOT NULL + add FK
ALTER TABLE sources          ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE projects         ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE record_types     ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE boxes            ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE duplicate_groups ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE records          ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE folder_rules     ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE scan_runs        ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE file_events      ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE alerts           ALTER COLUMN org_id SET NOT NULL;

ALTER TABLE sources          ADD CONSTRAINT sources_org_fk          FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE projects         ADD CONSTRAINT projects_org_fk         FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE record_types     ADD CONSTRAINT record_types_org_fk     FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE boxes            ADD CONSTRAINT boxes_org_fk            FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE duplicate_groups ADD CONSTRAINT duplicate_groups_org_fk FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE records          ADD CONSTRAINT records_org_fk          FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE folder_rules     ADD CONSTRAINT folder_rules_org_fk     FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE scan_runs        ADD CONSTRAINT scan_runs_org_fk        FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE file_events      ADD CONSTRAINT file_events_org_fk      FOREIGN KEY (org_id) REFERENCES organizations(id);
ALTER TABLE alerts           ADD CONSTRAINT alerts_org_fk           FOREIGN KEY (org_id) REFERENCES organizations(id);

-- audit_log: nullable org_id (NULL = install-level event)
ALTER TABLE audit_log ADD COLUMN org_id BIGINT REFERENCES organizations(id);

-- ============================================================================
-- 4. Replace global uniques with per-org composites
-- ============================================================================

ALTER TABLE sources      DROP CONSTRAINT sources_name_key;
ALTER TABLE sources      ADD CONSTRAINT sources_org_name_uq UNIQUE (org_id, name);

ALTER TABLE projects     DROP CONSTRAINT projects_code_key;
ALTER TABLE projects     ADD CONSTRAINT projects_org_code_uq UNIQUE (org_id, code);

ALTER TABLE record_types DROP CONSTRAINT record_types_code_key;
ALTER TABLE record_types ADD CONSTRAINT record_types_org_code_uq UNIQUE (org_id, code);

ALTER TABLE boxes        DROP CONSTRAINT boxes_box_number_key;
ALTER TABLE boxes        ADD CONSTRAINT boxes_org_number_uq UNIQUE (org_id, box_number);

-- Records partial unique: include org_id so the same path can exist in different orgs
DROP INDEX records_source_path_live_uq;
CREATE UNIQUE INDEX records_org_source_path_live_uq
    ON records (org_id, source_id, path) WHERE status NOT IN ('retired', 'superseded');

-- ============================================================================
-- 5. Move org-scoped settings to org_settings
-- ============================================================================

INSERT INTO org_settings (org_id, key, value)
SELECT o.id, s.key, s.value
FROM settings s
CROSS JOIN organizations o WHERE o.slug = 'default'
WHERE s.key IN ('schedule_cron', 'integrity_cron', 'label_default_format');

-- ============================================================================
-- 6. Indexes for RLS performance
-- ============================================================================

CREATE INDEX sources_org_idx          ON sources (org_id);
CREATE INDEX projects_org_idx         ON projects (org_id);
CREATE INDEX record_types_org_idx     ON record_types (org_id);
CREATE INDEX boxes_org_idx            ON boxes (org_id);
CREATE INDEX records_org_idx          ON records (org_id);
CREATE INDEX scan_runs_org_idx        ON scan_runs (org_id);
CREATE INDEX file_events_org_idx      ON file_events (org_id);
CREATE INDEX alerts_org_idx           ON alerts (org_id);
CREATE INDEX audit_log_org_idx        ON audit_log (org_id) WHERE org_id IS NOT NULL;

-- ============================================================================
-- 7. DB role split (critical for RLS enforcement)
--
-- RLS is bypassed by table owners and roles with BYPASSRLS. The existing
-- 'nestbox' role owns all tables → it bypasses RLS. The application must
-- connect as a DIFFERENT role that does NOT own the tables.
--
--   nestbox_app   — LOGIN, no BYPASSRLS, DML grants. The app connects here.
--   nestbox_admin — LOGIN, BYPASSRLS. Worker + lifecycle (backup/restore/reset).
--
-- The existing 'nestbox' superuser remains the table owner (used for
-- migrations and first-boot bootstrap).
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nestbox_app') THEN
        CREATE ROLE nestbox_app LOGIN PASSWORD 'nestbox_app';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nestbox_admin') THEN
        CREATE ROLE nestbox_admin LOGIN PASSWORD 'nestbox_admin' BYPASSRLS;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO nestbox_app;
GRANT USAGE ON SCHEMA public TO nestbox_admin;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nestbox_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nestbox_admin;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO nestbox_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO nestbox_admin;

-- Future tables created by the owner should also be accessible
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nestbox_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nestbox_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO nestbox_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO nestbox_admin;

-- ============================================================================
-- 8. Enable RLS on org-scoped data tables
--
-- NOT enabled on: users, settings, backups, schema_version (install-level),
-- organizations, org_members (small lookup tables; app enforces membership).
-- ============================================================================

ALTER TABLE org_settings     ENABLE ROW LEVEL SECURITY;
ALTER TABLE sources          ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects         ENABLE ROW LEVEL SECURITY;
ALTER TABLE record_types     ENABLE ROW LEVEL SECURITY;
ALTER TABLE boxes            ENABLE ROW LEVEL SECURITY;
ALTER TABLE duplicate_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE records          ENABLE ROW LEVEL SECURITY;
ALTER TABLE folder_rules     ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_runs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts           ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log        ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- 9. RLS policies
-- ============================================================================

-- Standard data tables: row visible iff org_id matches session GUC
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'org_settings','sources','projects','record_types','boxes',
        'duplicate_groups','records','folder_rules','scan_runs',
        'file_events','alerts'
    ] LOOP
        EXECUTE format(
            'CREATE POLICY org_isolation ON %I
             USING (org_id = current_setting(''app.org_id'')::bigint)
             WITH CHECK (org_id = current_setting(''app.org_id'')::bigint)', t);
    END LOOP;
END $$;

-- audit_log: see your org's rows + install-level (NULL org_id) rows
CREATE POLICY audit_org_scope ON audit_log
    USING (org_id = current_setting('app.org_id')::bigint OR org_id IS NULL)
    WITH CHECK (org_id = current_setting('app.org_id')::bigint);

-- ============================================================================
-- 10. Update audit trigger to capture org_id
-- ============================================================================

CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger AS $$
DECLARE
    actor TEXT := COALESCE(NULLIF(current_setting('app.username', TRUE), ''), 'system');
    org   BIGINT;
    cols  TEXT[] := '{}';
    k     TEXT;
BEGIN
    -- Capture org context (NULL if not set, e.g. system operations)
    BEGIN
        org := current_setting('app.org_id', TRUE)::bigint;
    EXCEPTION WHEN OTHERS THEN
        org := NULL;
    END;

    IF TG_OP = 'UPDATE' THEN
        FOR k IN SELECT jsonb_object_keys(to_jsonb(NEW)) LOOP
            IF to_jsonb(NEW) -> k IS DISTINCT FROM to_jsonb(OLD) -> k THEN
                cols := cols || k;
            END IF;
        END LOOP;
        IF cols = '{}' THEN
            RETURN NEW;
        END IF;
        INSERT INTO audit_log (username, table_name, row_pk, action, old_row, new_row, changed_cols, org_id)
        VALUES (actor, TG_TABLE_NAME, NEW.id, 'UPDATE', to_jsonb(OLD), to_jsonb(NEW), cols, org);
    ELSE
        INSERT INTO audit_log (username, table_name, row_pk, action, new_row, org_id)
        VALUES (actor, TG_TABLE_NAME, NEW.id, 'INSERT', to_jsonb(NEW), org);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 11. Bump schema version
-- ============================================================================

INSERT INTO schema_version (version) VALUES (5);

COMMIT;
