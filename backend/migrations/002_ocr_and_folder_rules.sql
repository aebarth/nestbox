-- 002: OCR text extraction, full-text content search, content-based
-- duplicate detection, and folder-based auto-categorization rules.

ALTER TABLE records
    ADD COLUMN IF NOT EXISTS ocr_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (ocr_status IN ('pending', 'done', 'failed', 'skipped')),
    ADD COLUMN IF NOT EXISTS ocr_method TEXT,
    ADD COLUMN IF NOT EXISTS ocr_text TEXT,
    ADD COLUMN IF NOT EXISTS ocr_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS text_fingerprint TEXT;

ALTER TABLE records ADD COLUMN IF NOT EXISTS ocr_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', left(coalesce(ocr_text, ''), 200000))) STORED;

CREATE INDEX IF NOT EXISTS records_ocr_tsv_gin ON records USING gin (ocr_tsv);
CREATE INDEX IF NOT EXISTS records_fp_trgm ON records USING gin (text_fingerprint gin_trgm_ops);

ALTER TABLE duplicate_groups DROP CONSTRAINT IF EXISTS duplicate_groups_match_kind_check;
ALTER TABLE duplicate_groups ADD CONSTRAINT duplicate_groups_match_kind_check
    CHECK (match_kind IN ('hash', 'name_size', 'content'));

CREATE TABLE IF NOT EXISTS folder_rules (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id      BIGINT NOT NULL REFERENCES sources(id),
    folder_prefix  TEXT NOT NULL DEFAULT '',
    record_type_id BIGINT REFERENCES record_types(id),
    project_id     BIGINT REFERENCES projects(id),
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, folder_prefix)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'folder_rules_audit') THEN
        CREATE TRIGGER folder_rules_audit AFTER INSERT OR UPDATE ON folder_rules
            FOR EACH ROW EXECUTE FUNCTION audit_row_change();
        CREATE TRIGGER folder_rules_no_delete BEFORE DELETE ON folder_rules
            FOR EACH ROW EXECUTE FUNCTION forbid_change();
        CREATE TRIGGER folder_rules_touch BEFORE UPDATE ON folder_rules
            FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
    END IF;
END $$;
