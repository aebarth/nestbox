-- Starter lookup values; admins can edit or deactivate these in the UI.
INSERT INTO record_types (code, name, retention_years) VALUES
    ('DHR',  'Device History Record', NULL),
    ('DMR',  'Device Master Record',  NULL),
    ('TRN',  'Training Record',       5),
    ('CAL',  'Calibration Record',    5),
    ('SUP',  'Supplier Record',       7),
    ('CAPA', 'CAPA Record',           7),
    ('GEN',  'General Document',      NULL);

INSERT INTO settings (key, value) VALUES
    ('org_name',             '"My Organization"'),
    ('schedule_cron',        '"0 2 * * *"'),
    ('integrity_cron',       '"0 3 * * 0"'),
    ('label_default_format', '"avery5160"');

-- The scan inbox is a built-in local source.
INSERT INTO sources (name, kind, config, is_monitored) VALUES
    ('Scan Inbox', 'local', '{"root": "/inbox", "builtin_inbox": true}', TRUE);
