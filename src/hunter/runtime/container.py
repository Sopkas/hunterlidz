"""Dependency wiring: builds the application object handed to the presentation layer. (S1-03, S1-04)

Adapters are added here by the tasks that introduce them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from psycopg.errors import InsufficientPrivilege, InvalidSchemaName
from sqlalchemy.exc import OperationalError, ProgrammingError

from hunter.application.errors import ErrorCode, HunterError
from hunter.application.ports import IdentityProvider
from hunter.infrastructure.os_identity import OsIdentity
from hunter.infrastructure.postgres import db
from hunter.runtime.config import Settings, safe_database_url


@dataclass(frozen=True, slots=True)
class App:
    settings: Settings
    identity: IdentityProvider = field(default_factory=OsIdentity)

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


def build_app(settings: Settings) -> App:
    return App(settings=settings)
