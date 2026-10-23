"""Dependency wiring: builds the application object handed to the presentation layer. (S1-03, S1-04)

Adapters are added here by the tasks that introduce them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from psycopg.errors import InsufficientPrivilege, InvalidSchemaName
from sqlalchemy.exc import OperationalError, ProgrammingError

from hunter.application.errors import ErrorCode, HunterError
from hunter.application.llm import LlmClient, Tariff
from hunter.application.llm_privacy import MaskingClient
from hunter.application.ports import IdentityProvider
from hunter.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from hunter.infrastructure.os_identity import OsIdentity
from hunter.infrastructure.postgres import db
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

    def migrate(self) -> str | None:
        """Apply pending migrations (S1-07). The driver's message is not shown: it may name
        the user, and the operator only needs to know which database did not answer."""
        engine = db.create_db_engine(self.settings.database_url)
        try:
            return db.upgrade(engine)
        except OperationalError:
            raise HunterError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"база данных {self.database_display} не отвечает или отказала во входе. "
                "Запущена ли она и верны ли логин и пароль? README, раздел «База».",
            ) from None
        except ProgrammingError as error:
            # Only rights and the schema are the operator's to fix; any other error is a bug in
            # a migration and keeps its traceback (review A on #14).
            if not isinstance(error.orig, (InsufficientPrivilege, InvalidSchemaName)):
                raise
            raise HunterError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"база данных {self.database_display} отказала в правах: пользователю нужно "
                "право создавать таблицы в своей схеме. README, раздел «База».",
            ) from None
        finally:
            engine.dispose()


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
