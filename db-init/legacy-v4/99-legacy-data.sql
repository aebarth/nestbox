-- LEGACY v4 SNAPSHOT (upgrade rehearsal only — see README.md).
--
-- Simulated production data on top of the v4 seed: these rows represent an
-- install that has been in use. Every one of them must survive
-- 005_multi_org.sql under the auto-created Default org (slug 'default');
-- backend/tests/test_upgrade_backfill.py asserts exactly that.

INSERT INTO projects (code, name) VALUES
    ('PRJ-1', 'Legacy Project One'),
    ('PRJ-2', 'Legacy Project Two');

INSERT INTO boxes (box_number, physical_location) VALUES ('B-001', 'Cabinet 3, Shelf B');

-- Records live in the seed's Scan Inbox source (id = 1). one.pdf/two.pdf share
-- content (one duplicate group); three.pdf is unique.
INSERT INTO records (source_id, path, name, size_bytes, hash_algo, hash_value, title, project_id, record_type_id, status) VALUES
    (1, 'legacy/one.pdf',   'one.pdf',   100, 'sha256', repeat('aa', 32), 'Legacy One',   1, 1, 'active'),
    (1, 'legacy/two.pdf',   'two.pdf',   100, 'sha256', repeat('aa', 32), 'Legacy Two',   1, 1, 'active'),
    (1, 'legacy/three.pdf', 'three.pdf', 200, 'sha256', repeat('bb', 32), 'Legacy Three', 2, 2, 'active');

INSERT INTO duplicate_groups (match_kind) VALUES ('hash');
UPDATE records SET duplicate_group_id = 1 WHERE name IN ('one.pdf', 'two.pdf');

INSERT INTO folder_rules (source_id, folder_prefix, record_type_id) VALUES (1, 'legacy/', 1);

INSERT INTO scan_runs (source_id, kind, trigger, status, started_at, finished_at, stats)
VALUES (1, 'scan', 'manual', 'done', now() - interval '1 day', now() - interval '1 day',
        '{"seen": 3, "new": 3}');

INSERT INTO file_events (record_id, scan_run_id, event_type) VALUES (1, 1, 'discovered');
INSERT INTO alerts (file_event_id, record_id, alert_type, severity) VALUES (1, 1, 'discovered', 'info');
