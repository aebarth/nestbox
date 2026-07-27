-- Append-only audit trail.
-- The API sets `SET LOCAL app.username = '<actor>'` per transaction; anything
-- else (loader, worker, psql) is recorded as 'system'.
-- Enforcement is trigger-based so it holds even for the table owner:
--   * every INSERT/UPDATE on audited tables is copied to audit_log
--   * DELETE on audited tables is rejected outright
--   * UPDATE/DELETE on audit_log itself is rejected

CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger AS $$
DECLARE
    actor TEXT := COALESCE(NULLIF(current_setting('app.username', TRUE), ''), 'system');
    cols  TEXT[] := '{}';
    k     TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        FOR k IN SELECT jsonb_object_keys(to_jsonb(NEW)) LOOP
            IF to_jsonb(NEW) -> k IS DISTINCT FROM to_jsonb(OLD) -> k THEN
                cols := cols || k;
            END IF;
        END LOOP;
        IF cols = '{}' THEN
            RETURN NEW;  -- no-op update, don't clutter the log
        END IF;
        INSERT INTO audit_log (username, table_name, row_pk, action, old_row, new_row, changed_cols)
        VALUES (actor, TG_TABLE_NAME, NEW.id, 'UPDATE', to_jsonb(OLD), to_jsonb(NEW), cols);
    ELSE
        INSERT INTO audit_log (username, table_name, row_pk, action, new_row)
        VALUES (actor, TG_TABLE_NAME, NEW.id, 'INSERT', to_jsonb(NEW));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% on % is not permitted: Nestbox tables are append-only (retire or supersede instead)',
        TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['users','sources','projects','record_types','boxes',
                             'records','alerts','duplicate_groups','folder_rules']
    LOOP
        EXECUTE format(
            'CREATE TRIGGER %I_audit AFTER INSERT OR UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION audit_row_change()', t, t);
        EXECUTE format(
            'CREATE TRIGGER %I_no_delete BEFORE DELETE ON %I
             FOR EACH ROW EXECUTE FUNCTION forbid_change()', t, t);
    END LOOP;
END $$;

CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- updated_at maintenance
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['users','sources','projects','record_types','boxes',
                             'records','alerts','duplicate_groups','folder_rules']
    LOOP
        EXECUTE format(
            'CREATE TRIGGER %I_touch BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION touch_updated_at()', t, t);
    END LOOP;
END $$;
