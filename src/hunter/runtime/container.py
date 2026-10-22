"""Dependency wiring: builds the application object handed to the presentation layer. (S1-03, S1-04)

Adapters are added here by the tasks that introduce them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from hunter.application.llm import LlmClient, Tariff
from hunter.application.llm_privacy import MaskingClient
from hunter.application.ports import IdentityProvider
from hunter.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from hunter.infrastructure.os_identity import OsIdentity
from hunter.runtime.config import LlmSettings, Settings, safe_database_url


@dataclass(frozen=True, slots=True)
class App:
    settings: Settings
    identity: IdentityProvider = field(default_factory=OsIdentity)
    llm: LlmClient | None = None

    @property
    def database_display(self) -> str:
        """Database address without credentials, safe to print."""
        return safe_database_url(self.settings.database_url)


def llm_tariff(llm: LlmSettings, currency: str) -> Tariff | None:
    """A ":free" model costs 0; otherwise the configured prices, or None when unknown (the
    budget then refuses a paid call, doc 09)."""
    if llm.model.endswith(":free"):
        return Tariff(Decimal(0), Decimal(0), currency)
    if llm.price_input_per_million is None or llm.price_output_per_million is None:
        return None
    return Tariff(llm.price_input_per_million, llm.price_output_per_million, currency)


def build_llm(settings: Settings) -> LlmClient | None:
    """Every call goes through MaskingClient: contacts never reach the model (ADR-0002)."""
    llm = settings.llm
    if llm is None:
        return None
    client = OpenAICompatibleClient(
        base_url=llm.base_url,
        model=llm.model,
        api_key=llm.api_key,
        tariff=llm_tariff(llm, settings.default_currency),
        timeout_s=llm.timeout_s,
        structured=llm.structured,
        provider=llm.provider,
    )
    return MaskingClient(client)


def build_app(settings: Settings) -> App:
    return App(settings=settings, llm=build_llm(settings))
