"""Shared test setup.

Network guard: any DNS lookup or connection beyond loopback fails the test and is recorded,
so a swallowed error inside the code under test still fails the run. libpq (psycopg) opens
its sockets in C, past the Python socket module, so PostgreSQL hosts are checked at the
driver's connect instead (ADR-0003).

Storage tests take the database from HUNTER_TEST_DATABASE_URL; without it they are skipped,
unless HUNTER_TEST_DATABASE_REQUIRED is set (CI on Linux), where a missing database fails.
"""

from __future__ import annotations

import os
import socket

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

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
        hosts = ",".join(str(params.get(key) or "") for key in ("host", "hostaddr")).split(",")
        # No host or a socket directory ("/run/postgresql") stays on this machine.
        remote = [h for h in hosts if h and not h.startswith("/") and h not in _LOOPBACK]
        if remote:
            attempts.append(f"postgres {remote}")
            raise OSError(f"network access in tests is forbidden: postgres {remote}")
        return real_pg_connect(conninfo, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    # SQLAlchemy calls psycopg.connect, a reference taken at import, so both are replaced.
    monkeypatch.setattr(psycopg, "connect", guarded_pg_connect)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(_drop_cls(guarded_pg_connect)))
    yield attempts
    assert not attempts, f"test tried to reach the network: {attempts}"


def _drop_cls(function):
    return lambda cls, *args, **kwargs: function(*args, **kwargs)


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("HUNTER_TEST_DATABASE_URL", "").strip()
    if url:
        return url
    if os.environ.get("HUNTER_TEST_DATABASE_REQUIRED"):
        pytest.fail("HUNTER_TEST_DATABASE_URL is not set, but this run requires the database")
    pytest.skip("HUNTER_TEST_DATABASE_URL is not set: start the database (README, «База»)")
