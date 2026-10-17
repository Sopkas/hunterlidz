"""Who performs an action and whether the role allows it. (S1-04)

The actor is the Hunter user whose identity_id matches the operating-system account that runs
the process (IdentityProvider). Environment variables, .env and command arguments are not
consulted, so setting HUNTER_ACTOR, USERNAME or USER to an administrator's name changes nothing.

Limitation (local CLI): the trust boundary is the OS account. Whoever can run commands under
another person's OS account, or write to the database directly, acts as that person. The
Telegram panel (S3-05) authenticates by Telegram user ID instead.
"""

from __future__ import annotations

from hunter.application.errors import ErrorCode, HunterError
from hunter.application.ports import IdentityProvider, UserDirectory
from hunter.domain.roles import Permission, can
from hunter.domain.users import User


def authenticate(identity: IdentityProvider, users: UserDirectory) -> User:
    identity_id = identity.current_identity()
    user = users.user_by_identity(identity_id)
    if user is None or not user.active:
        raise HunterError(
            ErrorCode.UNAUTHENTICATED,
            f"учётная запись {identity_id!r} не связана с активным пользователем Hunter. "
            "Администратор: `hunter users add`.",
        )
    return user


def require(user: User, permission: Permission) -> None:
    if not can(user.role, permission):
        raise HunterError(
            ErrorCode.FORBIDDEN, f"роль {user.role} не даёт права на действие «{permission}»"
        )
