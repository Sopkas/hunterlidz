"""Interfaces at real boundaries: clock, identifiers and storage. (H-02)

Other boundaries (LLM, source fetching, knowledge base) are added by the tasks that need them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hunter.domain.modes import RunMode
from hunter.domain.users import User


class Clock(Protocol):
    def now(self) -> datetime:
        """Current moment, timezone-aware UTC."""


class IdGenerator(Protocol):
    def new_id(self) -> str:
        """New unique identifier (UUID string)."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    entity_type: str
    entity_id: str
    action: str
    actor_id: str
    at: datetime
    correlation_id: str
    previous_version: int | None = None
    new_version: int | None = None
    reason: str | None = None


class Store(Protocol):
    def transaction(self) -> AbstractContextManager[None]:
        """Atomic unit of work; nested calls join the outer transaction."""

    # users
    def count_users(self) -> int: ...
    def add_user(self, user: User, at: datetime) -> None: ...
    def user_by_name(self, name: str) -> User | None: ...
    def list_users(self) -> list[User]: ...

    # settings
    def get_mode(self) -> RunMode: ...
    def set_mode(self, mode: RunMode, at: datetime) -> None: ...

    # audit (append-only)
    def add_audit(self, event: AuditEvent) -> None: ...
    def audit_for(self, entity_id: str) -> Iterator[AuditEvent]: ...
