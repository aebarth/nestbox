-- Finding 2: give scan_runs a claim lease so (a) two workers can never execute
-- the same run and (b) a run left 'running' by a crashed worker is recovered.
--
-- Fresh installs already carry these columns + index via db-init/02-schema.sql
-- (v3); this migration upgrades existing installs from v2. Both use IF NOT
-- EXISTS so it is safe to run where the objects already exist.
ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;

-- Claim (status='queued' ORDER BY id) and reaper (status='running') both filter
-- on status; the composite (status, id) index serves the ordered claim directly.
CREATE INDEX IF NOT EXISTS ix_scan_runs_status ON scan_runs (status, id);
