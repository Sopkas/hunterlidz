"""Dependency wiring: builds the application object handed to the presentation layer. (H-01)"""

from __future__ import annotations

from dataclasses import dataclass

from hunter.runtime.config import Settings


@dataclass(slots=True)
class App:
    settings: Settings


def build_app(settings: Settings) -> App:
    return App(settings=settings)
