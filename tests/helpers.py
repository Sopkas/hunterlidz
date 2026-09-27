"""Test doubles injected through constructors (no monkeypatching of global modules)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hunter.application.context import Deps
from hunter.domain.roles import Role
from hunter.domain.users import User
from hunter.infrastructure.sqlite.store import SqliteStore

T0 = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, at: datetime = T0) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def advance(self, **kwargs) -> None:
        self.at += timedelta(**kwargs)


class SeqIds:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> str:
        self.n += 1
        return f"id-{self.n:04d}"


def make_deps(store: SqliteStore | None = None, clock: FixedClock | None = None) -> Deps:
    return Deps(
        store=store or SqliteStore(":memory:"),
        clock=clock or FixedClock(),
        ids=SeqIds(),
        correlation_id="corr-test",
    )


def add_user(deps: Deps, name: str, role: Role) -> User:
    user = User(id=deps.ids.new_id(), name=name, role=role, active=True)
    deps.store.add_user(user, deps.clock.now())
    return user
