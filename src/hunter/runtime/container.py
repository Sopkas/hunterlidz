"""Dependency wiring: builds the application object handed to the presentation layer."""

from __future__ import annotations

from dataclasses import dataclass

from hunter.application import access
from hunter.application.context import Deps
from hunter.domain.users import User
from hunter.infrastructure.sqlite.store import SqliteStore
from hunter.infrastructure.system import SystemClock, UuidGenerator
from hunter.runtime.config import Settings


@dataclass(slots=True)
class App:
    settings: Settings
    store: SqliteStore
    deps: Deps

    def actor(self) -> User:
        """Authenticated user from HUNTER_ACTOR; raises UNAUTHENTICATED if unknown."""
        return access.authenticate(self.deps, self.settings.actor)


def build_app(settings: Settings) -> App:
    store = SqliteStore(settings.db_path)
    ids = UuidGenerator()
    deps = Deps(store=store, clock=SystemClock(), ids=ids, correlation_id=ids.new_id())
    return App(settings=settings, store=store, deps=deps)
