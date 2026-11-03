"""Interfaces of adapters used by the use cases. (S1-04)

Only real boundaries: the operating system (who runs the process) and storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hunter.domain.entities import FetchStatus
from hunter.domain.users import User


class IdentityProvider(Protocol):
    def current_identity(self) -> str:
        """identity_id of whoever runs the process, as reported by the OS, e.g. 'os:maxim'."""


class UserDirectory(Protocol):
    def user_by_identity(self, identity_id: str) -> User | None: ...


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status: FetchStatus
    text: str | None = None
    content_hash: str | None = None
    error_code: str | None = None
    truncated: bool = False


class SourceFetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...
