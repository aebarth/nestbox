-- Nestbox schema. Fresh-install canonical DDL; upgrades ship as
-- backend/migrations/NNN_*.sql applied against schema_version.

CREATE TABLE schema_version (
    version     INT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_version (version) VALUES (1), (2);

CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sources (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL CHECK (kind IN ('local', 'gdrive', 'graph_drive')),
    config          JSONB NOT NULL DEFAULT '{}',
    credentials_enc BYTEA,
    is_monitored    BOOLEAN NOT NULL DEFAULT TRUE,
    scan_cron       TEXT,
    last_scan_at    TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE record_types (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    retention_years INT,            -- NULL = permanent
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE boxes (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    box_number        TEXT NOT NULL UNIQUE,
    physical_location TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'sealed', 'destroyed')),
    sealed_at         TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE duplicate_groups (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_kind   TEXT NOT NULL CHECK (match_kind IN ('hash', 'name_size', 'content')),
    is_dismissed BOOLEAN NOT NULL DEFAULT FALSE,
    dismissed_by BIGINT REFERENCES users(id),
    dismissed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE records (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    uuid               UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    source_id          BIGINT NOT NULL REFERENCES sources(id),
    external_id        TEXT,                    -- Drive fileId / Graph itemId
    path               TEXT NOT NULL,           -- POSIX, relative to source root
    name               TEXT NOT NULL,
    size_bytes         BIGINT NOT NULL DEFAULT 0,
    file_mtime         TIMESTAMPTZ,
    hash_algo          TEXT CHECK (hash_algo IN ('sha256', 'md5', 'quickxor')),
    hash_value         TEXT,
    hashed_at          TIMESTAMPTZ,
    title              TEXT NOT NULL DEFAULT '',
    project_id         BIGINT REFERENCES projects(id),
    record_type_id     BIGINT REFERENCES record_types(id),
    lot_number         TEXT NOT NULL DEFAULT '',
    part_number        TEXT NOT NULL DEFAULT '',
    tags               TEXT[] NOT NULL DEFAULT '{}',
    notes              TEXT NOT NULL DEFAULT '',
    box_id             BIGINT REFERENCES boxes(id),
    filed_at           TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('inbox', 'active', 'missing', 'retired', 'superseded')),
    superseded_by_id   BIGINT REFERENCES records(id),
    retention_until    DATE,
    retired_at         TIMESTAMPTZ,
    retired_by         BIGINT REFERENCES users(id),
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    duplicate_group_id BIGINT REFERENCES duplicate_groups(id),
    ocr_status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (ocr_status IN ('pending', 'done', 'failed', 'skipped')),
    ocr_method         TEXT,
    ocr_text           TEXT,
    ocr_at             TIMESTAMPTZ,
    text_fingerprint   TEXT,
    ocr_tsv            tsvector GENERATED ALWAYS AS
                       (to_tsvector('english', left(coalesce(ocr_text, ''), 200000))) STORED,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX records_source_path_live_uq
    ON records (source_id, path) WHERE status NOT IN ('retired', 'superseded');
CREATE UNIQUE INDEX records_source_external_uq
    ON records (source_id, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX records_hash_idx ON records (hash_algo, hash_value);
CREATE INDEX records_box_idx ON records (box_id);
CREATE INDEX records_status_idx ON records (status);
CREATE INDEX records_dupe_idx ON records (duplicate_group_id) WHERE duplicate_group_id IS NOT NULL;
CREATE INDEX records_tags_gin ON records USING gin (tags);
CREATE INDEX records_name_trgm ON records USING gin (name gin_trgm_ops);
CREATE INDEX records_title_trgm ON records USING gin (title gin_trgm_ops);
CREATE INDEX records_ocr_tsv_gin ON records USING gin (ocr_tsv);
CREATE INDEX records_fp_trgm ON records USING gin (text_fingerprint gin_trgm_ops);

CREATE TABLE folder_rules (
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

CREATE TABLE scan_runs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   BIGINT REFERENCES sources(id),   -- NULL = all monitored sources
    kind        TEXT NOT NULL DEFAULT 'scan' CHECK (kind IN ('scan', 'integrity')),
    trigger     TEXT NOT NULL DEFAULT 'manual' CHECK (trigger IN ('manual', 'scheduled')),
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'error')),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    stats       JSONB NOT NULL DEFAULT '{}',
    error_text  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX scan_runs_status_idx ON scan_runs (status) WHERE status IN ('queued', 'running');

CREATE TABLE file_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id   BIGINT NOT NULL REFERENCES records(id),
    scan_run_id BIGINT REFERENCES scan_runs(id),
    event_type  TEXT NOT NULL CHECK (event_type IN
                ('discovered','modified','renamed','moved','missing','reappeared',
                 'hash_mismatch','filed','categorized','retired','superseded')),
    old_value   JSONB,
    new_value   JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX file_events_record_idx ON file_events (record_id, occurred_at DESC);

CREATE TABLE alerts (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_event_id BIGINT REFERENCES file_events(id),
    record_id     BIGINT NOT NULL REFERENCES records(id),
    alert_type    TEXT NOT NULL,
    severity      TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'resolved')),
    resolution    TEXT CHECK (resolution IN ('accepted', 'restored', 'retired', 'false_positive')),
    resolved_by   BIGINT REFERENCES users(id),
    resolved_at   TIMESTAMPTZ,
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX alerts_open_idx ON alerts (status, severity) WHERE status <> 'resolved';
CREATE INDEX alerts_record_idx ON alerts (record_id);

CREATE TABLE audit_log (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    username     TEXT NOT NULL DEFAULT 'system',
    table_name   TEXT NOT NULL,
    row_pk       BIGINT,
    action       TEXT NOT NULL CHECK (action IN ('INSERT', 'UPDATE')),
    old_row      JSONB,
    new_row      JSONB,
    changed_cols TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_log_table_idx ON audit_log (table_name, row_pk, at DESC);
