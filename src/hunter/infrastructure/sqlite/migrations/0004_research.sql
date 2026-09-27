-- H-05: job queue with leases and research runs with usage, cost and results.
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_until TEXT,
    checkpoint TEXT,
    run_after TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One active job per entity and kind.
CREATE UNIQUE INDEX jobs_one_active ON jobs (kind, entity_id) WHERE status IN ('queued', 'running');

CREATE TABLE research_runs (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads (id),
    job_id TEXT NOT NULL REFERENCES jobs (id),
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'needs_review',
        'insufficient_evidence', 'budget_exceeded', 'failed')),
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost TEXT NOT NULL DEFAULT '0',
    currency TEXT NOT NULL,
    error_code TEXT,
    checkpoint TEXT,
    result TEXT,
    problems TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX research_runs_lead ON research_runs (lead_id, started_at);
CREATE UNIQUE INDEX research_runs_one_per_job ON research_runs (job_id)
    WHERE status != 'running';
