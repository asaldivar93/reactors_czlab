-- Add the experiment model and establish schema versioning on an existing
-- database. The experiments table was dead before this migration and its old
-- shape could not represent a running experiment, so there is no data to
-- preserve when rebuilding it.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL
);

ALTER TABLE data ADD COLUMN IF NOT EXISTS experiment_name TEXT;

CREATE INDEX IF NOT EXISTS data_series_idx
    ON data (reactor, name, channel, date);
CREATE INDEX IF NOT EXISTS data_experiment_idx
    ON data (experiment_name, date);

DROP TABLE IF EXISTS experiments;

CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    reactors TEXT[] NOT NULL,
    start_date TIMESTAMP(3),
    end_date TIMESTAMP(3)
);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('0001', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

COMMIT;
