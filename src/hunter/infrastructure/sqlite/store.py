"""SqliteStore: implements application.ports.Store over one connection. (H-02)

Each task adds its repository mixin here, so a table has exactly one owner module.
"""

from __future__ import annotations

from pathlib import Path

from hunter.infrastructure.sqlite.core_repo import CoreRepo
from hunter.infrastructure.sqlite.db import Transactions, connect, migrate
from hunter.infrastructure.sqlite.leads_repo import LeadsRepo
from hunter.infrastructure.sqlite.sources_repo import SourcesRepo


class SqliteStore(CoreRepo, LeadsRepo, SourcesRepo):
    def __init__(self, path: Path | str) -> None:
        self.conn = connect(path)
        self.applied_migrations = migrate(self.conn)
        self._tx = Transactions(self.conn)

    def transaction(self):
        return self._tx.transaction()

    def close(self) -> None:
        self.conn.close()
