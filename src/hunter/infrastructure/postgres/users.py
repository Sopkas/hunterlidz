"""Users in PostgreSQL: the UserDirectory port of S1-04. (S1-07)

Writing users (`hunter users add`) comes with S1-08; the CLI asks this directory who the OS
account belongs to before every operation that needs a role.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from hunter.domain.roles import Role
from hunter.domain.users import User


class PostgresUserDirectory:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def user_by_identity(self, identity_id: str) -> User | None:
        row = self._connection.execute(
            text("SELECT id, identity_id, name, role, active FROM users WHERE identity_id = :i"),
            {"i": identity_id},
        ).one_or_none()
        if row is None:
            return None
        return User(
            id=str(row.id),
            identity_id=row.identity_id,
            name=row.name,
            role=Role(row.role),
            active=row.active,
        )
