-- 008_org_backups.sql — org-scoped backups (#15, PR B)
--
-- Adds an optional organization scope to the install-level `backups` table.
-- Full-DB pg_dump rows keep org_id NULL; org exports (kind='org', zip files
-- written to the org's own backup path) carry their organization. The
-- filename for org backups is the absolute container path of the zip.
--
-- Idempotent; no-op on a fresh install (empty table).
--
-- NOTE: Do NOT include BEGIN/COMMIT or INSERT INTO schema_version here.
-- The migration runner (database.py::apply_migrations) manages the
-- transaction and records the version itself.

ALTER TABLE backups ADD COLUMN IF NOT EXISTS org_id BIGINT REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS idx_backups_org_id ON backups (org_id);

-- The original kind check (004 / db-init) only allowed install-level kinds;
-- org-scoped exports use kind='org'. Drop and re-add so both fresh installs
-- (db-init DDL) and upgraded installs (legacy-v4) end up with the wider set.
ALTER TABLE backups DROP CONSTRAINT IF EXISTS backups_kind_check;
ALTER TABLE backups ADD CONSTRAINT backups_kind_check CHECK (kind IN ('manual', 'scheduled', 'org'));
