-- H-06: versioned scores (FR-05) and the operator's contact exception for WARM leads.
CREATE TABLE scores (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads (id),
    research_run_id TEXT REFERENCES research_runs (id),
    icp_fit INTEGER NOT NULL CHECK (icp_fit IN (0, 10, 20, 25)),
    signal INTEGER NOT NULL CHECK (signal IN (0, 10, 20, 30)),
    problem_fit INTEGER NOT NULL CHECK (problem_fit IN (0, 10, 15, 20)),
    budget INTEGER NOT NULL CHECK (budget IN (0, 5, 10, 15)),
    contactability INTEGER NOT NULL CHECK (contactability IN (0, 5, 10)),
    total INTEGER NOT NULL,
    rules_version TEXT NOT NULL,
    reasons TEXT NOT NULL,
    contradictions TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (total = icp_fit + signal + problem_fit + budget + contactability)
);

CREATE INDEX scores_lead ON scores (lead_id, created_at);

ALTER TABLE leads ADD COLUMN contact_exception_reason TEXT;
ALTER TABLE leads ADD COLUMN contact_exception_by TEXT REFERENCES users (id);
