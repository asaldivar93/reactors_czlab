CREATE DATABASE bioreactor_db;
\c bioreactor_db

CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    node_id TEXT NOT NULL,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    name TEXT NOT NULL,
    channel TEXT NOT NULL,
    value FLOAT NOT NULL,
    -- NULL when the row was recorded outside any experiment.
    experiment_name TEXT
);

-- The plot query filters on exactly these columns and orders by date.
CREATE INDEX data_series_idx ON data (reactor, name, channel, date);
CREATE INDEX data_experiment_idx ON data (experiment_name);

CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    reactors TEXT[] NOT NULL,
    start_date TIMESTAMP(3),
    -- NULL while the experiment is running.
    end_date TIMESTAMP(3)
);
