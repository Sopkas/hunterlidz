-- H-07: messages, immutable versions and approvals bound to an exact payload.
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads (id),
    kind TEXT NOT NULL CHECK (kind IN ('initial', 'follow_up', 'response')),
    sequence_step INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('draft', 'pending_review', 'approved', 'sent',
        'rejected', 'cancelled')),
    current_version INTEGER NOT NULL,
    skip_reason TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One active message per lead, kind and sequence step.
CREATE UNIQUE INDEX messages_one_active ON messages (lead_id, kind, sequence_step)
    WHERE status IN ('draft', 'pending_review', 'approved');

CREATE TABLE message_versions (
    message_id TEXT NOT NULL REFERENCES messages (id),
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    contact_id TEXT NOT NULL REFERENCES contacts (id),
    channel TEXT NOT NULL,
    normalized_address TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    case_claim_ids TEXT NOT NULL,
    review_reasons TEXT NOT NULL,
    blockers TEXT NOT NULL,
    generation TEXT,
    created_by TEXT NOT NULL REFERENCES users (id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (message_id, version)
);

CREATE TRIGGER message_versions_immutable BEFORE UPDATE ON message_versions
BEGIN SELECT RAISE(ABORT, 'message versions are immutable'); END;

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages (id),
    message_version INTEGER NOT NULL,
    payload_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users (id),
    approved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    revoke_reason TEXT,
    FOREIGN KEY (message_id, message_version) REFERENCES message_versions (message_id, version)
);

CREATE UNIQUE INDEX approvals_one_live ON approvals (message_id) WHERE revoked_at IS NULL;

-- Model spend outside research runs (drafts), including failed attempts, for the daily cap.
CREATE TABLE llm_usage (
    id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    lead_id TEXT REFERENCES leads (id),
    model_id TEXT NOT NULL,
    status TEXT NOT NULL,
    llm_calls INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost TEXT NOT NULL,
    currency TEXT NOT NULL,
    at TEXT NOT NULL
);
