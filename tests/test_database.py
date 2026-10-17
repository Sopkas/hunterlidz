"""PostgreSQL access from tests (S1-06, ADR-0003): the driver works, the guard holds."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def test_bundled_libpq_is_loaded():
    # Runs on every CI job, Windows included, where there is no database service.
    assert psycopg.pq.__impl__ == "binary"
    assert psycopg.pq.version() >= 170000


def test_guard_stops_libpq_before_a_remote_host(block_network):
    # libpq connects in C, so without the driver-level check this would leave the machine.
    for connect in (psycopg.connect, psycopg.Connection.connect):
        with pytest.raises(OSError, match="forbidden"):
            connect(host="10.255.255.1", dbname="hunter", connect_timeout=1)
        with pytest.raises(OSError, match="forbidden"):
            connect("postgresql://hunter@10.255.255.1/hunter")
    assert len(block_network) == 4
    block_network.clear()  # the attempts were the point of this test


def test_postgres_answers_through_sqlalchemy(database_url):
    # HUNTER_*_DATABASE_URL is plain postgresql://; SQLAlchemy would pick psycopg2 for it.
    engine = create_engine(make_url(database_url).set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("select 1")).scalar_one() == 1
            assert connection.execute(text("show server_encoding")).scalar_one() == "UTF8"
    finally:
        engine.dispose()
