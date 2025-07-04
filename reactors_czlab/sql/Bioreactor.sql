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

-- Table: visiferm
CREATE TABLE visiferm (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    value FLOAT NOT NULL,
    units TEXT NOT NULL
);

-- Table: arcph
CREATE TABLE arcph (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    value FLOAT NOT NULL,
    units TEXT NOT NULL
);

-- Table: analog
CREATE TABLE analog (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    calibration TEXT,
    value FLOAT NOT NULL,
    units TEXT NOT NULL
);

-- Table: actuator
CREATE TABLE pump (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    calibration TEXT,
    value FLOAT NOT NULL,
    units TEXT NOT NULL
);

-- Table: mfc
CREATE TABLE visiferm (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    value FLOAT NOT NULL,
    units TEXT NOT NULL
);

-- Table: light
CREATE TABLE light (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    calibration TEXT,
    value FLOAT NOT NULL,
    units TEXT NOT NULL
);

-- Table: biomass - AS7341
CREATE TABLE biomass (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiment (id) ON DELETE CASCADE,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    calibration TEXT,
    value INTEGER[10] NOT NULL,
    units TEXT NOT NULL
);
