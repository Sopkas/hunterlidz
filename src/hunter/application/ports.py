"""Interfaces of adapters used by the use cases. (S1-04)

Only real boundaries: the operating system (who runs the process) and storage.
"""

from __future__ import annotations

from typing import Protocol

from hunter.domain.users import User


class IdentityProvider(Protocol):
    def current_identity(self) -> str:
        """identity_id of whoever runs the process, as reported by the OS, e.g. 'os:maxim'."""


class UserDirectory(Protocol):
    def user_by_identity(self, identity_id: str) -> User | None: ...
