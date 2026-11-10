-- 05-fix-rls.sql — NULL-safe RLS org-context (fixes the v5 first-run crash)
--
-- 02-schema.sql installs the org RLS policies using
--       current_setting('app.org_id')::bigint
-- That expression raises "unrecognized configuration parameter app.org_id"
-- when the GUC is not yet set (first-run setup, before any org context exists)
-- and "invalid input syntax for type bigint" when it is an empty string. The
-- audit trigger runs on every INSERT/UPDATE, and its own INSERT INTO audit_log
-- trips this policy's WITH CHECK — so essentially every write made before an
-- org context was established crashed the whole request.
--
-- This file runs AFTER 02-schema.sql (db-init applies *.sql in sorted order)
-- and swaps the fragile expression for current_org_id(), a STABLE helper that
-- returns NULL for an unset/empty/non-numeric GUC. Policies then fail closed
-- (no rows visible, no cross-org leakage) instead of erroring. Idempotent.

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

-- The RLS policy is evaluated as the connected role (nestbox_app), so it must
-- be able to call the helper.
GRANT EXECUTE ON FUNCTION current_org_id() TO nestbox_app;
GRANT EXECUTE ON FUNCTION current_org_id() TO nestbox_admin;

-- Rebuild the standard data-table policies (created as org_isolation by 02).
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

-- Rebuild the audit_log policy (created as audit_org_scope by 02). Install-
-- level events (org_id IS NULL) stay visible to every org.
DROP POLICY IF EXISTS audit_org_scope ON audit_log;
CREATE POLICY audit_org_scope ON audit_log
    USING (org_id IS NULL OR org_id = current_org_id())
    WITH CHECK (org_id IS NULL OR org_id = current_org_id());
