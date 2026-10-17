"""Dependency wiring: builds the application object handed to the presentation layer. (S1-03, S1-04)

Adapters are added here by the tasks that introduce them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hunter.application.ports import IdentityProvider
from hunter.infrastructure.os_identity import OsIdentity
from hunter.runtime.config import Settings, safe_database_url


@dataclass(frozen=True, slots=True)
class App:
    settings: Settings
    identity: IdentityProvider = field(default_factory=OsIdentity)

    @property
    def database_display(self) -> str:
        """Database address without credentials, safe to print."""
        return safe_database_url(self.settings.database_url)


def build_app(settings: Settings) -> App:
    return App(settings=settings)
