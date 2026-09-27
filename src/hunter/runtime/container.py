"""Dependency wiring: builds the application object handed to the presentation layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from hunter.application import access
from hunter.application.context import Deps
from hunter.application.research import ResearchConfig
from hunter.domain.users import User
from hunter.infrastructure.fetcher import SafeHttpFetcher
from hunter.infrastructure.knowledge import FileKnowledgeBase
from hunter.infrastructure.sqlite.store import SqliteStore
from hunter.infrastructure.system import SystemClock, UuidGenerator
from hunter.runtime.config import Settings, llm_settings


@dataclass(slots=True)
class App:
    settings: Settings
    store: SqliteStore
    deps: Deps
    fetcher: SafeHttpFetcher
    knowledge: FileKnowledgeBase

    def actor(self) -> User:
        """Authenticated user from HUNTER_ACTOR; raises UNAUTHENTICATED if unknown."""
        return access.authenticate(self.deps, self.settings.actor)

    def llm(self):
        """LLM client from settings; ConfigError (exit 2) before any network call."""
        cfg = llm_settings(self.settings.env)
        if cfg.provider == "gemini":
            from hunter.infrastructure.llm.gemini import GeminiClient

            return GeminiClient(cfg.api_key, cfg.model)
        from hunter.infrastructure.llm.claude import ClaudeClient

        return ClaudeClient(cfg.api_key, cfg.model)

    def research_config(self) -> ResearchConfig:
        cfg = llm_settings(self.settings.env)
        return ResearchConfig(
            limits=cfg.limits, pricing=cfg.pricing, business_tz=self.settings.business_tz
        )

    def business_today(self) -> date:
        """Calendar date in the business time zone (not the UTC date)."""
        return self.deps.clock.now().astimezone(self.settings.business_tz).date()


def build_app(settings: Settings) -> App:
    store = SqliteStore(settings.db_path)
    ids = UuidGenerator()
    deps = Deps(store=store, clock=SystemClock(), ids=ids, correlation_id=ids.new_id())
    return App(
        settings=settings,
        store=store,
        deps=deps,
        fetcher=SafeHttpFetcher(),
        knowledge=FileKnowledgeBase(settings.knowledge_dir),
    )
