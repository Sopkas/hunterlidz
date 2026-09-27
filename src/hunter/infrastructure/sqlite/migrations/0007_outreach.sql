-- H-09: idempotency, manual send attempts, chain tasks, inbound replies, contact bans.
CREATE TABLE idempotency_keys (
    key TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE send_attempts (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages (id),
    message_version INTEGER NOT NULL,
    approval_id TEXT NOT NULL REFERENCES approvals (id),
    idempotency_key TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('manual')),
    status TEXT NOT NULL CHECK (status IN ('sent')),
    sent_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL REFERENCES users (id),
    recorded_at TEXT NOT NULL
);

-- A message is sent at most once.
CREATE UNIQUE INDEX send_attempts_one_per_message ON send_attempts (message_id);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads (id),
    type TEXT NOT NULL,
    sequence_step INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'done', 'cancelled')),
    note TEXT,
    cancel_reason TEXT,
    owner_id TEXT REFERENCES users (id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One open task per lead, type and step (doc 10).
CREATE UNIQUE INDEX tasks_one_open ON tasks (lead_id, type, sequence_step) WHERE status = 'open';
CREATE INDEX tasks_due ON tasks (status, due_at);

CREATE TABLE inbound_messages (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads (id),
    contact_id TEXT REFERENCES contacts (id),
    channel TEXT NOT NULL,
    external_event_id TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,
    proposal TEXT,
    reply_class TEXT,
    action TEXT,
    reviewed_by TEXT REFERENCES users (id),
    reviewed_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (channel, external_event_id)
);

CREATE TABLE suppressions (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,  -- rows created by one ban decision are lifted together
    scope_type TEXT NOT NULL CHECK (scope_type IN ('contact', 'company')),
    scope_id TEXT NOT NULL,
    match_domain TEXT,
    match_address TEXT,
    reason TEXT NOT NULL,
    source_event_id TEXT,
    created_by TEXT NOT NULL REFERENCES users (id),
    created_at TEXT NOT NULL,
    lifted_at TEXT,
    lifted_by TEXT REFERENCES users (id),
    lift_reason TEXT
);

CREATE INDEX suppressions_scope ON suppressions (scope_type, scope_id) WHERE lifted_at IS NULL;
