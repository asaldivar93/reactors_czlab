BEGIN;

CREATE TABLE reactor_recording_state (
    reactor TEXT PRIMARY KEY,
    recording BOOLEAN NOT NULL,
    updated_at TIMESTAMP(3) NOT NULL
);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('0002', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

COMMIT;
