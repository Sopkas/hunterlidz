"""Alembic environment: migrations run on the connection handed over by db.upgrade(). (S1-07)

There is no alembic.ini: `hunter migrate` takes the database from Hunter's own configuration,
so the address and its password live in one place (.env, never committed).
"""

from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection, transaction_per_migration=True)
with context.begin_transaction():
    context.run_migrations()
