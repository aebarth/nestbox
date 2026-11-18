-- 007_org_storage.sql — per-organization storage locations (#15)
--
-- Each organization gets its own subtree under the install-level bind
-- mounts: /inbox/<slug>, /sources/<slug>, /backups/<slug>. The effective
-- paths are stored in org_settings (storage_inbox_root, storage_sources_root,
-- storage_backup_path) so Admin → Storage can display and change them.
--
-- For existing organizations this migration seeds the slug-based defaults
-- and repoints each org's built-in Scan Inbox source from the shared /inbox
-- to its own /inbox/<slug> subtree. Unfiled PDFs left in the old shared
-- folder are NOT moved — move them manually (or re-scan them from a
-- temporary source) after upgrading.
--
-- Idempotent: safe to run on a fresh install too (no orgs exist yet, so all
-- statements are no-ops).
--
-- NOTE: Do NOT include BEGIN/COMMIT or INSERT INTO schema_version here.
-- The migration runner (database.py::apply_migrations) manages the
-- transaction and records the version itself.

INSERT INTO org_settings (org_id, key, value)
SELECT o.id, 'storage_inbox_root', to_jsonb('/inbox/' || o.slug)
FROM organizations o
ON CONFLICT (org_id, key) DO NOTHING;

INSERT INTO org_settings (org_id, key, value)
SELECT o.id, 'storage_sources_root', to_jsonb('/sources/' || o.slug)
FROM organizations o
ON CONFLICT (org_id, key) DO NOTHING;

INSERT INTO org_settings (org_id, key, value)
SELECT o.id, 'storage_backup_path', to_jsonb('/backups/' || o.slug)
FROM organizations o
ON CONFLICT (org_id, key) DO NOTHING;

UPDATE sources s
SET config = jsonb_set(s.config, '{root}', to_jsonb('/inbox/' || o.slug))
FROM organizations o
WHERE s.org_id = o.id
  AND s.config ->> 'builtin_inbox' = 'true'
  AND s.config ->> 'root' IS DISTINCT FROM ('/inbox/' || o.slug);
