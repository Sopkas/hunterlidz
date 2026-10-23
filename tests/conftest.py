"""Shared test setup.

Network guard: any DNS lookup or connection beyond loopback fails the test and is recorded,
so a swallowed error inside the code under test still fails the run. libpq (psycopg) opens
its sockets in C, past the Python socket module, so PostgreSQL hosts are checked at the
driver's connect instead (ADR-0003).

Storage tests take the database from HUNTER_TEST_DATABASE_URL; without it they are skipped,
unless HUNTER_TEST_DATABASE_REQUIRED is set (CI on Linux), where a missing database fails.
Each run migrates its own throwaway schema; each test runs in a transaction rolled back after
it (fixtures pg_engine and db).
"""

from __future__ import annotations

import os
import socket
import uuid
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import text

from hunter.infrastructure.postgres.db import create_db_engine, upgrade

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    attempts: list[str] = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    real_pg_connect = psycopg.Connection.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            attempts.append(f"connect {host}")
            raise OSError(f"network access in tests is forbidden: {host}")
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host in _LOOPBACK:
            return real_getaddrinfo(host, *args, **kwargs)
        attempts.append(f"dns {host}")
        raise OSError(f"DNS lookup in tests is forbidden: {host}")

    def guarded_pg_connect(conninfo="", **kwargs):
        params = conninfo_to_dict(conninfo, **kwargs)
        if params.get("service") or os.environ.get("PGSERVICE"):
            attempts.append("postgres service")
            raise OSError("network access in tests is forbidden: postgres service file")
        # Without a host in conninfo libpq takes PGHOST / PGHOSTADDR from the environment.
        hosts = ",".join(
            str(params.get(key) or os.environ.get(env) or "")
            for key, env in (("host", "PGHOST"), ("hostaddr", "PGHOSTADDR"))
        ).split(",")
        # No host or a socket directory ("/run/postgresql") stays on this machine.
        remote = [h for h in hosts if h and not h.startswith("/") and h not in _LOOPBACK]
        if remote:
            attempts.append(f"postgres {remote}")
            raise OSError(f"network access in tests is forbidden: postgres {remote}")
        return real_pg_connect(conninfo, **kwargs)

    # A developer's own libpq settings must not steer test connections (review A on #10).
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    # SQLAlchemy calls psycopg.connect, a reference taken at import, so both are replaced.
    monkeypatch.setattr(psycopg, "connect", guarded_pg_connect)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(_drop_cls(guarded_pg_connect)))
    yield attempts
    assert not attempts, f"test tried to reach the network: {attempts}"


def _drop_cls(function):
    return lambda cls, *args, **kwargs: function(*args, **kwargs)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("HUNTER_TEST_DATABASE_URL", "").strip()
    if not url:
        if os.environ.get("HUNTER_TEST_DATABASE_REQUIRED"):
            pytest.fail("HUNTER_TEST_DATABASE_URL is not set, but this run requires the database")
        pytest.skip("HUNTER_TEST_DATABASE_URL is not set: start the database (README, «База»)")
    # Session fixtures connect before a test's block_network is in place: check the host here.
    if urlsplit(url).hostname not in _LOOPBACK:
        pytest.fail("HUNTER_TEST_DATABASE_URL must point to this machine (localhost)")
    return url


@pytest.fixture(scope="session")
def pg_engine(database_url):
    """A fresh schema per test run with all migrations applied; dropped at the end, so tests
    never touch the tables of the developer's own database (ADR-0003, point 8)."""
    schema = f"hunter_test_{uuid.uuid4().hex[:12]}"
    admin = create_db_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_db_engine(database_url, connect_args={"options": f"-c search_path={schema}"})
    try:
        upgrade(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


@pytest.fixture
def db(pg_engine):
    """A connection inside a transaction that is rolled back after the test (ADR-0003, p. 8).
    A statement expected to fail goes into `with db.begin_nested():`, a savepoint."""
    with pg_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
