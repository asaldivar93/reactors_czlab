CREATE DATABASE bioreactor_db;
\c bioreactor_db

CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL
);

-- One row per channel reading. reactor/name/channel are the three parts
-- of the OPC browse name <reactor>:<name>:<channel>, split by
-- OpcClient.match_tree.
CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    node_id TEXT NOT NULL,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    name TEXT NOT NULL,
    channel TEXT NOT NULL,
    value FLOAT NOT NULL,
    -- Nullable: recording runs with or without an experiment, and rows
    -- taken outside one still belong in the table.
    experiment_name TEXT
);

-- The plots filter (reactor, name, channel) over a date range, which
-- was a sequential scan of the whole table on every frame.
CREATE INDEX data_series_idx ON data (reactor, name, channel, date);
CREATE INDEX data_experiment_idx ON data (experiment_name, date);

-- start_date and end_date are both nullable, unlike the original
-- definition: an experiment that has been created but not started has
-- neither, and one that is running has no end date. name is UNIQUE
-- because it is what the data rows are tagged with, so two experiments
-- sharing a name would be indistinguishable afterwards.
CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    reactors TEXT[] NOT NULL,
    start_date TIMESTAMP(3),
    end_date TIMESTAMP(3)
);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('0001', CURRENT_TIMESTAMP);
