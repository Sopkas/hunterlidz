"""Authentication, permissions, users and run mode. (H-02)

The actor always comes from configuration (HUNTER_ACTOR) resolved against the users table,
never from command arguments, so a client cannot claim another identity (doc 10).
"""

from __future__ import annotations

from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.modes import Operation, RunMode, allows
from hunter.domain.roles import Permission, Role, can
from hunter.domain.users import User


def authenticate(deps: Deps, name: str) -> User:
    user = deps.store.user_by_name(name)
    if user is None or not user.active:
        raise HunterError(
            ErrorCode.UNAUTHENTICATED,
            f"пользователь {name!r} не найден или отключён. Администратор: `hunter users add`.",
        )
    return user


def require(user: User, permission: Permission) -> None:
    if not can(user.role, permission):
        raise HunterError(
            ErrorCode.FORBIDDEN, f"роль {user.role} не даёт права на действие «{permission}»"
        )


def require_mode(deps: Deps, operation: Operation) -> None:
    mode = deps.store.get_mode()
    if not allows(mode, operation):
        raise HunterError(ErrorCode.FORBIDDEN, f"в режиме {mode} действие «{operation}» недоступно")


def bootstrap_admin(deps: Deps, name: str) -> User | None:
    """Create the first administrator; a no-op once any user exists."""
    with deps.store.transaction():
        if deps.store.count_users():
            return None
        user = User(id=deps.ids.new_id(), name=name, role=Role.ADMIN, active=True)
        deps.store.add_user(user, deps.clock.now())
        deps.audit(user, "user", user.id, "bootstrap_admin", new_version=1)
        return user


def add_user(deps: Deps, actor: User, name: str, role: Role) -> User:
    require(actor, Permission.MANAGE_USERS)
    with deps.store.transaction():
        if deps.store.user_by_name(name):
            raise HunterError(ErrorCode.INVALID_INPUT, f"пользователь {name!r} уже есть")
        try:
            user = User(id=deps.ids.new_id(), name=name, role=role, active=True)
        except ValueError as exc:
            raise HunterError(ErrorCode.INVALID_INPUT, str(exc)) from None
        deps.store.add_user(user, deps.clock.now())
        deps.audit(actor, "user", user.id, f"add_user:{role}", new_version=1)
        return user


def set_mode(deps: Deps, actor: User, mode: RunMode, reason: str) -> None:
    require(actor, Permission.SET_MODE)
    if not reason.strip():
        raise HunterError(ErrorCode.INVALID_INPUT, "укажите причину смены режима")
    with deps.store.transaction():
        previous = deps.store.get_mode()
        deps.store.set_mode(mode, deps.clock.now())
        deps.audit(actor, "system", "run_mode", f"mode:{previous}->{mode}", reason=reason)
