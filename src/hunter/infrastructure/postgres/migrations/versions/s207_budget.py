"""Durable research budgets, stacked after the jobs migration (S2-07)."""

from alembic import op

revision = "s207_budget"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE budget_days (
        day date PRIMARY KEY,
        cap numeric NOT NULL CHECK (cap >= 0),
        charged numeric NOT NULL DEFAULT 0 CHECK (charged >= 0),
        provider_floor numeric NOT NULL DEFAULT 0 CHECK (provider_floor >= 0)
    );
    CREATE TABLE research_budgets (
        id uuid PRIMARY KEY,
        day date NOT NULL REFERENCES budget_days(day),
        state jsonb NOT NULL,
        stopped_reason text,
        checkpoint jsonb NOT NULL DEFAULT '{}'
    );
    """)


def downgrade():
    op.execute("DROP TABLE research_budgets; DROP TABLE budget_days;")
