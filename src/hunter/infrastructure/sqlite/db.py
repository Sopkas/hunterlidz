"""Connection, migrations and transactions. (H-02)

Migrations are numbered SQL files applied in order and recorded in schema_migrations;
applying them twice is a no-op, so an existing database is upgraded in place.
Transactions use BEGIN IMMEDIATE: the write lock is taken up front, which serialises
concurrent writers instead of failing one of them at commit time.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path

MIGRATIONS_PACKAGE = "hunter.infrastructure.sqlite.migrations"


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations; returns the names applied now."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)"
    )
    done = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
    files = sorted(
        f for f in resources.files(MIGRATIONS_PACKAGE).iterdir() if f.name.endswith(".sql")
    )
    applied = []
    for file in files:
        if file.name in done:
            continue
        script = file.read_text(encoding="utf-8")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split(script):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)", (file.name, to_db(datetime.now(UTC)))
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(file.name)
    return applied


def _split(script: str) -> list[str]:
    # executescript() would commit implicitly; statements are split to stay inside one transaction.
    # complete_statement understands trigger bodies (BEGIN ...; END;); splitting on ';' does not.
    statements, buffer = [], ""
    for line in script.splitlines(keepends=True):
        if not buffer and line.strip().startswith("--"):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise ValueError("migration ends with an incomplete SQL statement")
    return statements


class Transactions:
    """Re-entrant transaction helper: nested blocks join the outermost one."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")
        finally:
            self._depth = 0


def to_db(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be stored; use timezone-aware UTC")
        return value.astimezone(UTC).isoformat()
    return value.isoformat()


def dt_from_db(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def date_from_db(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
