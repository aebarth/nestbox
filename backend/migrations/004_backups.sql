-- Phase 2.1 (#9): database lifecycle — track backups for restore / retention.
CREATE TABLE backups (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    size_bytes  BIGINT NOT NULL DEFAULT 0,
    sha256      TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'manual' CHECK (kind IN ('manual', 'scheduled')),
    created_by  BIGINT REFERENCES users(id),
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
