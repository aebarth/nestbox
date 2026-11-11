-- v5: No global seed data.
-- Per-org starter defaults (record types, Scan Inbox source) are created
-- by the application at org-creation time (setup flow or admin creates org).
-- See backend/services/org_defaults.py (PR 2).
--
-- System-level settings that persist across orgs:
INSERT INTO settings (key, value) VALUES
    ('backup_cron',        '"0 3 * * *"'),
    ('label_default_format', '"avery5160"');
