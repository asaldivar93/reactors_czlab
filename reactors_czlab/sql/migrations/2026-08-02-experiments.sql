-- Bring a database created from the original Bioreactor.sql up to the
-- schema the GUI's experiment interface needs.
--
--     psql -d bioreactor_db -f 2026-08-02-experiments.sql
--
-- Safe to run twice. The experiments table is rebuilt rather than
-- altered: nothing ever wrote to it (CLAUDE.md records it as dead), so
-- there is no data to preserve, and its original shape cannot represent
-- a running experiment - end_date was NOT NULL.

BEGIN;

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

COMMIT;
