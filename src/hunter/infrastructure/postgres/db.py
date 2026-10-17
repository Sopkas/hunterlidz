"""PostgreSQL engine and migrations. (S1-07, ADR-0003)

HUNTER_DATABASE_URL is a plain postgresql:// address; SQLAlchemy 2.0 would pick psycopg2 for
it, so the driver is set explicitly (ADR-0003, point 6). Access is synchronous (point 5).
"""

from __future__ import annotations

from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

CONNECT_TIMEOUT_S = "10"


def create_db_engine(database_url: str, **kwargs) -> Engine:
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    if "connect_timeout" not in url.query:
        # libpq waits forever by default; an address may set its own value.
        url = url.update_query_dict({"connect_timeout": CONNECT_TIMEOUT_S})
    return create_engine(url, **kwargs)


def _alembic_config(connection) -> Config:
    config = Config()
    location = resources.files("hunter.infrastructure.postgres") / "migrations"
    config.set_main_option("script_location", str(location))
    config.attributes["connection"] = connection
    return config


def upgrade(engine: Engine, revision: str = "head") -> str | None:
    """Apply pending migrations; a second run changes nothing. Returns the current revision."""
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), revision)
    return current_revision(engine)


def downgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        command.downgrade(_alembic_config(connection), revision)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()
