-- H-04: case claims registry (doc 01 "Реестр доказательств", doc 10 case_claims).
CREATE TABLE case_claims (
    id TEXT PRIMARY KEY,
    case_key TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('owner_reported', 'verified', 'planned', 'withdrawn')),
    external_allowed INTEGER NOT NULL,
    source_ref TEXT,
    approved_by TEXT,
    claim_version INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (case_key, claim_version)
);

CREATE UNIQUE INDEX case_claims_one_active ON case_claims (case_key) WHERE active = 1;
