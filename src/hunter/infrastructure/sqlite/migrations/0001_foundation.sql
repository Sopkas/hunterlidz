-- H-02: users, system settings, append-only audit.
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'observer', 'worker')),
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

INSERT INTO settings (key, value, updated_at) VALUES ('run_mode', 'shadow', '1970-01-01T00:00:00+00:00');

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    previous_version INTEGER,
    new_version INTEGER,
    reason TEXT,
    correlation_id TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE INDEX audit_events_entity ON audit_events (entity_id, id);

CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
