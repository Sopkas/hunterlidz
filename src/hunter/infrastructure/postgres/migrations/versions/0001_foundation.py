"""Foundation: users, settings, append-only audit, campaigns, companies, contacts, sources,
evidence, leads. (S1-07, H-02 + H-03 of the source branch, doc 10 "Сущности")

Value lists in CHECK are written out, not imported from the domain: a migration is frozen
history. tests/test_migrations.py fails when the domain enums and these lists drift apart.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = """
-- Doc 10: storage metadata is kept by the database (domain contract, deviation 6).
CREATE FUNCTION hunter_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

CREATE FUNCTION hunter_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE TABLE users (
    id uuid PRIMARY KEY,
    -- Same form as User.identity_id; PostgreSQL regexps cap {m,n} at 255, hence the length.
    identity_id text NOT NULL UNIQUE CHECK (
        identity_id ~ '^(os|telegram):\\S+$'
        AND length(substring(identity_id FROM position(':' IN identity_id) + 1)) <= 256
    ),
    telegram_user_id bigint UNIQUE,
    name text NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('admin', 'operator', 'observer', 'worker')),
    active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE settings (
    key text PRIMARY KEY,
    value text NOT NULL,
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (key <> 'run_mode' OR value IN ('shadow', 'manual', 'paused'))
);

-- Doc 12: a new installation starts in shadow mode.
INSERT INTO settings (key, value) VALUES ('run_mode', 'shadow');

CREATE TABLE audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    entity_type text NOT NULL CHECK (entity_type <> ''),
    entity_id text NOT NULL CHECK (entity_id <> ''),
    actor_id text NOT NULL CHECK (actor_id <> ''),
    action text NOT NULL CHECK (action <> ''),
    previous_version integer,
    new_version integer,
    reason text,
    correlation_id text NOT NULL CHECK (correlation_id <> ''),
    at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_events_entity ON audit_events (entity_type, entity_id, seq);

CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION hunter_append_only();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION hunter_append_only();

CREATE TABLE campaigns (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE,
    started_at timestamptz,
    ended_at timestamptz,
    icp_version text NOT NULL,
    scoring_version text,
    message_variant text,
    status text NOT NULL DEFAULT 'active',
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE companies (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> ''),
    -- Deviation: normalize_company_name() of S1-09, to find possible duplicates by name (T-02).
    normalized_name text NOT NULL DEFAULT '',
    normalized_domain text CHECK (
        normalized_domain = lower(normalized_domain)
        AND normalized_domain LIKE '%.%'
        AND normalized_domain NOT LIKE 'www.%'
        AND normalized_domain NOT LIKE '%.'
        AND normalized_domain !~ '[^\\x21-\\x7e]|[/:@?#]'
    ),
    industry text,
    size_min integer CHECK (size_min >= 0),
    size_max integer CHECK (size_max >= 0),
    location text,
    merged_into_id uuid REFERENCES companies (id),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (size_min IS NULL OR size_max IS NULL OR size_min <= size_max),
    CHECK (merged_into_id IS NULL OR merged_into_id <> id)
);

-- Doc 10: the domain is unique for an active company; a merged record keeps its history.
CREATE UNIQUE INDEX companies_active_domain ON companies (normalized_domain)
    WHERE normalized_domain IS NOT NULL AND merged_into_id IS NULL;
-- S1-09 docs/dedup.md "Что передаёт хранилище": active companies with the same name.
CREATE INDEX companies_active_name ON companies (normalized_name)
    WHERE merged_into_id IS NULL AND normalized_name <> '';

CREATE TABLE sources (
    id uuid PRIMARY KEY,
    canonical_url text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('website', 'conversation', 'manual')),
    fetch_status text NOT NULL
        CHECK (fetch_status IN ('imported', 'fetched', 'denied', 'unavailable', 'error')),
    fetched_at timestamptz,
    content_hash text,
    snapshot_ref text,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- A record is a snapshot of one fetch (domain contract, deviation 16, review of #16): a
    -- refetch with new content is a new record, so evidence keeps pointing at the text where
    -- its excerpt was found (T-06). Failed attempts have no content_hash and never collide.
    UNIQUE (source_type, canonical_url, content_hash),
    -- Doc 10: a URL does not prove the content was received.
    CHECK ((fetch_status IN ('imported', 'fetched')) = (error_code IS NULL))
);

CREATE TABLE contacts (
    id uuid PRIMARY KEY,
    company_id uuid NOT NULL REFERENCES companies (id),
    channel text NOT NULL CHECK (channel IN ('email', 'telegram', 'phone', 'web', 'other')),
    normalized_address text NOT NULL
        CHECK (normalized_address <> '' AND normalized_address = btrim(normalized_address)),
    name text,
    role text,
    source_id uuid REFERENCES sources (id),
    verified_at timestamptz,
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (channel, normalized_address)
);

CREATE TABLE evidence (
    id uuid PRIMARY KEY,
    company_id uuid NOT NULL REFERENCES companies (id),
    source_id uuid NOT NULL REFERENCES sources (id),
    claim text NOT NULL CHECK (btrim(claim) <> ''),
    excerpt text NOT NULL CHECK (btrim(excerpt) <> ''),
    event_at date,
    date_precision text NOT NULL DEFAULT 'unknown'
        CHECK (date_precision IN ('day', 'month', 'year', 'unknown')),
    observed_at timestamptz NOT NULL,
    verification_status text NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('unverified', 'verified', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((date_precision = 'unknown') = (event_at IS NULL)),
    -- Domain contract, deviation 2: a month or a year is stored as its first day.
    CHECK (date_precision <> 'month' OR extract(day FROM event_at) = 1),
    CHECK (date_precision <> 'year' OR (extract(day FROM event_at) = 1
                                         AND extract(month FROM event_at) = 1))
);

CREATE INDEX evidence_company ON evidence (company_id);

CREATE TABLE leads (
    id uuid PRIMARY KEY,
    company_id uuid NOT NULL REFERENCES companies (id),
    campaign_id uuid NOT NULL REFERENCES campaigns (id),
    icp text CHECK (icp IN ('founders', 'existing_business', 'agencies')),
    product text CHECK (product IN ('mvp_launch', 'business_automation', 'external_product_team')),
    stage text NOT NULL DEFAULT 'NEW' CHECK (stage IN (
        'NEW', 'RESEARCHED', 'APPROVED', 'CONTACTED', 'REPLIED', 'QUALIFIED', 'CALL',
        'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST', 'NURTURE'
    )),
    priority text CHECK (priority IN ('HOT', 'WARM', 'WATCH', 'SKIP')),
    owner_id uuid REFERENCES users (id),
    next_action_at timestamptz,
    loss_reason text,
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- Domain contract S1-05: a loss reason exactly for LOST, and never a blank one.
    CHECK ((stage = 'LOST') = (loss_reason IS NOT NULL AND btrim(loss_reason) <> ''))
);

-- Doc 10: one active opportunity per company and campaign; closed ones stay as history.
CREATE UNIQUE INDEX leads_active_per_campaign ON leads (company_id, campaign_id)
    WHERE stage NOT IN ('WON', 'LOST');

CREATE TRIGGER users_touch BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
CREATE TRIGGER settings_touch BEFORE UPDATE ON settings
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
CREATE TRIGGER campaigns_touch BEFORE UPDATE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
CREATE TRIGGER companies_touch BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
CREATE TRIGGER sources_touch BEFORE UPDATE ON sources
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
CREATE TRIGGER contacts_touch BEFORE UPDATE ON contacts
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
CREATE TRIGGER evidence_touch BEFORE UPDATE ON evidence
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
CREATE TRIGGER leads_touch BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
"""

DOWNGRADE = """
DROP TABLE leads, evidence, contacts, sources, companies, campaigns, audit_events, settings,
    users;
DROP FUNCTION hunter_append_only();
DROP FUNCTION hunter_touch_updated_at();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
