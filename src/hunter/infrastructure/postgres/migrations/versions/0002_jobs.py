"""Job queue with leases. (S2-06, T-18, doc 10 "jobs", doc 09 "Ошибки и повторные попытки")

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (btrim(kind) <> ''),
    entity_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_token uuid,
    lease_until timestamptz,
    checkpoint jsonb,
    run_after timestamptz NOT NULL DEFAULT now(),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- A lease exactly while running: a finished or queued job has no owner to write for it.
    CHECK ((status = 'running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);

-- One live job per entity and kind: a second enqueue of the same work is not a new job.
CREATE UNIQUE INDEX jobs_one_live ON jobs (kind, entity_id) WHERE status IN ('queued', 'running');
-- The claim scans queued jobs that are due and running ones whose lease ran out.
CREATE INDEX jobs_claimable ON jobs (kind, run_after) WHERE status IN ('queued', 'running');

CREATE TRIGGER jobs_touch BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION hunter_touch_updated_at();
"""

DOWNGRADE = """
DROP TABLE jobs;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
