CREATE DATABASE bioreactor_db;
\c bioreactor_db

-- Table: analog
CREATE TABLE data (
    id SERIAL PRIMARY KEY,
    node_id TEXT NOT NULL,
    date TIMESTAMP(3) NOT NULL,
    reactor TEXT NOT NULL,
    name TEXT NOT NULL,
    channel TEXT NOT NULL,
    value FLOAT NOT NULL
);
