-- 006_fix_rls_policies.sql — NULL-safe RLS org-context (fixes v5 first-run crash)
--
-- 005_multi_org.sql installed the org RLS policies using
--       current_setting('app.org_id')::bigint
-- which raises "unrecognized configuration parameter app.org_id" when the GUC
-- is not yet set (first-run setup, before any org context exists) and
-- "invalid input syntax for type bigint" when it is an empty string. Because
-- the audit trigger fires on every INSERT/UPDATE and its own INSERT INTO
-- audit_log trips this policy's WITH CHECK, nearly every write made before an
-- org context was established crashed the request.
--
-- This migration swaps the fragile expression for current_org_id(), a STABLE
-- helper that returns NULL for an unset/empty/non-numeric GUC. Policies then
-- fail closed (no rows visible, no cross-org leakage) instead of erroring.
-- Idempotent: safe to run on a fresh install too (db-init already applies the
-- same fix via 05-fix-rls.sql).

BEGIN;

CREATE OR REPLACE FUNCTION current_org_id() RETURNS BIGINT AS $$
DECLARE v TEXT;
BEGIN
    BEGIN
        v := current_setting('app.org_id', TRUE);
    EXCEPTION WHEN OTHERS THEN
        RETURN NULL;
    END;
    IF v IS NULL OR btrim(v) = '' THEN
        RETURN NULL;
    END IF;
    BEGIN
        RETURN v::bigint;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN NULL;
    END;
END;
$$ LANGUAGE plpgsql STABLE;

GRANT EXECUTE ON FUNCTION current_org_id() TO nestbox_app;
GRANT EXECUTE ON FUNCTION current_org_id() TO nestbox_admin;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'org_settings','sources','projects','record_types','boxes',
        'duplicate_groups','records','folder_rules','scan_runs',
        'file_events','alerts'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY org_isolation ON %I
             USING (org_id = current_org_id())
             WITH CHECK (org_id = current_org_id())', t);
    END LOOP;
END $$;

DROP POLICY IF EXISTS audit_org_scope ON audit_log;
CREATE POLICY audit_org_scope ON audit_log
    USING (org_id IS NULL OR org_id = current_org_id())
    WITH CHECK (org_id IS NULL OR org_id = current_org_id());

INSERT INTO schema_version (version) VALUES (6);

COMMIT;
