-- Bring an existing bioreactor_db up to the GUI schema.
-- Nothing has ever written to experiments, so it is redefined rather
-- than migrated.
\c bioreactor_db

ALTER TABLE data ADD COLUMN IF NOT EXISTS experiment_name TEXT;

CREATE INDEX IF NOT EXISTS data_series_idx
    ON data (reactor, name, channel, date);
CREATE INDEX IF NOT EXISTS data_experiment_idx ON data (experiment_name);

DROP TABLE IF EXISTS experiments;
CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    reactors TEXT[] NOT NULL,
    start_date TIMESTAMP(3),
    end_date TIMESTAMP(3)
);
