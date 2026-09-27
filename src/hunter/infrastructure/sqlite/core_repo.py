"""Users, settings and audit tables. (H-02)"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime

from hunter.application.ports import AuditEvent
from hunter.domain.modes import RunMode
from hunter.domain.roles import Role
from hunter.domain.users import User
from hunter.infrastructure.sqlite.db import dt_from_db, to_db


class CoreRepo:
    conn: sqlite3.Connection

    def count_users(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def add_user(self, user: User, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO users (id, name, role, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.name, user.role.value, int(user.active), to_db(at), to_db(at)),
        )

    def user_by_name(self, name: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        return _user(row) if row else None

    def user_by_id(self, user_id: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        return [_user(r) for r in self.conn.execute("SELECT * FROM users ORDER BY name")]

    def get_mode(self) -> RunMode:
        row = self.conn.execute("SELECT value FROM settings WHERE key = 'run_mode'").fetchone()
        return RunMode(row["value"])

    def set_mode(self, mode: RunMode, at: datetime) -> None:
        self.conn.execute(
            "UPDATE settings SET value = ?, version = version + 1, updated_at = ? "
            "WHERE key = 'run_mode'",
            (mode.value, to_db(at)),
        )

    def add_audit(self, event: AuditEvent) -> None:
        self.conn.execute(
            "INSERT INTO audit_events (entity_type, entity_id, action, actor_id, previous_version, "
            "new_version, reason, correlation_id, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.entity_type,
                event.entity_id,
                event.action,
                event.actor_id,
                event.previous_version,
                event.new_version,
                event.reason,
                event.correlation_id,
                to_db(event.at),
            ),
        )

    def audit_for(self, entity_id: str) -> Iterator[AuditEvent]:
        rows = self.conn.execute(
            "SELECT * FROM audit_events WHERE entity_id = ? ORDER BY id", (entity_id,)
        )
        for row in rows:
            yield AuditEvent(
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                action=row["action"],
                actor_id=row["actor_id"],
                at=dt_from_db(row["at"]),
                correlation_id=row["correlation_id"],
                previous_version=row["previous_version"],
                new_version=row["new_version"],
                reason=row["reason"],
            )


def _user(row: sqlite3.Row) -> User:
    return User(id=row["id"], name=row["name"], role=Role(row["role"]), active=bool(row["active"]))
