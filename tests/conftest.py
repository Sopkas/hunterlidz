"""Shared test setup.

Network guard: any DNS lookup or connection beyond loopback fails the test and is recorded,
so a swallowed error inside the code under test still fails the run.
"""

from __future__ import annotations

import socket

import pytest

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    attempts: list[str] = []
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            attempts.append(f"connect {host}")
            raise OSError(f"network access in tests is forbidden: {host}")
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        attempts.append(f"dns {host}")
        raise OSError(f"DNS lookup in tests is forbidden: {host}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield attempts
    assert not attempts, f"test tried to reach the network: {attempts}"
