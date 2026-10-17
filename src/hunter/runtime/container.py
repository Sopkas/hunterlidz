"""Dependency wiring: builds the application object handed to the presentation layer. (S1-03)

Adapters (storage, identity) are added here by the tasks that introduce them.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunter.runtime.config import Settings, safe_database_url


@dataclass(frozen=True, slots=True)
class App:
    settings: Settings

    @property
    def database_display(self) -> str:
        """Database address without credentials, safe to print."""
        return safe_database_url(self.settings.database_url)


def build_app(settings: Settings) -> App:
    return App(settings=settings)
