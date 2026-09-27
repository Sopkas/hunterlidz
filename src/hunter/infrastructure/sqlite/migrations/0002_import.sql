-- H-03: campaigns, companies, contacts, sources, evidence, leads, listings and import results.
CREATE TABLE campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    icp_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE companies (
    id TEXT PRIMARY KEY,
    name TEXT,
    normalized_domain TEXT,
    industry TEXT,
    location TEXT,
    merged_into_id TEXT REFERENCES companies (id),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Domain is unique among active (not merged) companies; companies without a domain are never
-- merged automatically, even with an equal name (doc 03, T-02).
CREATE UNIQUE INDEX companies_active_domain ON companies (normalized_domain)
    WHERE normalized_domain IS NOT NULL AND merged_into_id IS NULL;

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies (id),
    source_type TEXT NOT NULL CHECK (source_type IN ('listing', 'website', 'conversation', 'manual')),
    canonical_url TEXT NOT NULL,
    fetch_status TEXT NOT NULL,
    fetched_at TEXT,
    content_hash TEXT,
    snapshot TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (source_type, canonical_url)
);

CREATE TABLE contacts (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies (id),
    name TEXT,
    role TEXT,
    channel TEXT NOT NULL,
    normalized_address TEXT NOT NULL,
    company_wide INTEGER NOT NULL DEFAULT 0,
    source_id TEXT REFERENCES sources (id),
    verified_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (channel, normalized_address)
);

CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies (id),
    source_id TEXT NOT NULL REFERENCES sources (id),
    field TEXT NOT NULL,
    claim TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    event_date TEXT,
    date_precision TEXT NOT NULL DEFAULT 'unknown'
        CHECK (date_precision IN ('day', 'month', 'year', 'unknown')),
    observed_at TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'source_quoted'
);

CREATE INDEX evidence_company ON evidence (company_id);

CREATE TABLE leads (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies (id),
    campaign_id TEXT NOT NULL REFERENCES campaigns (id),
    icp TEXT NOT NULL DEFAULT 'unknown',
    product TEXT,
    stage TEXT NOT NULL DEFAULT 'NEW',
    priority TEXT,
    score_total INTEGER,
    prescore_total INTEGER,
    prescore TEXT,
    prescore_version TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    owner_id TEXT REFERENCES users (id),
    next_action_at TEXT,
    loss_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One active opportunity per company and campaign; closed ones stay as history (doc 10).
CREATE UNIQUE INDEX leads_active_per_campaign ON leads (company_id, campaign_id)
    WHERE stage NOT IN ('WON', 'LOST');

CREATE TABLE listings (
    id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    text_hash TEXT NOT NULL,
    platform TEXT NOT NULL,
    ext_id TEXT,
    company_id TEXT REFERENCES companies (id),
    source_id TEXT REFERENCES sources (id),
    lead_id TEXT REFERENCES leads (id),
    screening_status TEXT NOT NULL CHECK (screening_status IN ('accepted', 'rejected')),
    reject_reason TEXT,
    screening_version TEXT NOT NULL,
    raw TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE INDEX listings_text_hash ON listings (text_hash);

CREATE TABLE imports (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns (id),
    file_name TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users (id),
    created_at TEXT NOT NULL
);

CREATE TABLE import_rows (
    import_id TEXT NOT NULL REFERENCES imports (id),
    row_no INTEGER NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('created', 'matched', 'rejected')),
    reason TEXT,
    listing_id TEXT REFERENCES listings (id),
    lead_id TEXT REFERENCES leads (id),
    PRIMARY KEY (import_id, row_no)
);
