-- Finding 2: give scan_runs a claim lease so (a) two workers can never execute
-- the same run and (b) a run left 'running' by a crashed worker is recovered.
ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;

-- Speeds up the claim (status='queued') and the reaper (status='running').
CREATE INDEX IF NOT EXISTS ix_scan_runs_status ON scan_runs (status, id);
