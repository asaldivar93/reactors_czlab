CREATE DATABASE bioreactor_db;
\c bioreactor_db
-- Table: experiment
CREATE TABLE experiment (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    date TIMESTAMP(3) NOT NULL,
    reactors TEXT NOT NULL,
    volume FLOAT
);

-- Table: analog
CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    model TEXT NOT NULL,
    calibration TEXT,
    units TEXT NOT NULL
    value FLOAT NOT NULL,
);
