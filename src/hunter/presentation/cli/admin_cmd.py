"""`hunter init`, `hunter users`, `hunter mode`, `hunter history`. (H-02)"""

from __future__ import annotations

import argparse

from hunter.application import access
from hunter.domain.modes import RunMode
from hunter.domain.roles import Permission, Role


def register(subparsers: argparse._SubParsersAction) -> None:
    init = subparsers.add_parser("init", help="создать базу и первого администратора")
    init.set_defaults(handler=handle_init)

    users = subparsers.add_parser("users", help="пользователи и роли")
    users_sub = users.add_subparsers(dest="users_command", required=True, metavar="действие")
    users_sub.add_parser("list", help="список пользователей").set_defaults(handler=handle_list)
    add = users_sub.add_parser("add", help="добавить пользователя (только admin)")
    add.add_argument("name")
    add.add_argument("role", choices=[r.value for r in Role])
    add.set_defaults(handler=handle_add)

    mode = subparsers.add_parser("mode", help="режим работы: показать или сменить (admin)")
    mode.add_argument("new_mode", nargs="?", choices=[m.value for m in RunMode])
    mode.add_argument("--reason", default="", help="причина смены режима (обязательна)")
    mode.set_defaults(handler=handle_mode)

    history = subparsers.add_parser("history", help="история изменений объекта по его ID")
    history.add_argument("entity_id")
    history.set_defaults(handler=handle_history)


def handle_init(app, args) -> int:
    user = access.bootstrap_admin(app.deps, app.settings.actor)
    print(f"База: {app.settings.db_path}. Миграции: {app.store.applied_migrations or 'актуальны'}.")
    if user:
        print(f"Создан первый администратор: {user.name}.")
    else:
        print("Пользователи уже есть — администратор не создавался.")
    return 0


def handle_list(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    for user in app.store.list_users():
        state = "" if user.active else " (отключён)"
        print(f"  {user.name:<24} {user.role}{state}")
    return 0


def handle_add(app, args) -> int:
    user = access.add_user(app.deps, app.actor(), args.name, Role(args.role))
    print(f"Добавлен пользователь {user.name} с ролью {user.role}.")
    return 0


def handle_mode(app, args) -> int:
    actor = app.actor()
    if not args.new_mode:
        print(f"Текущий режим: {app.store.get_mode()}")
        return 0
    access.set_mode(app.deps, actor, RunMode(args.new_mode), args.reason)
    print(f"Режим переключён на {args.new_mode}.")
    return 0


def handle_history(app, args) -> int:
    access.require(app.actor(), Permission.READ)
    names = {u.id: u.name for u in app.store.list_users()}
    events = list(app.store.audit_for(args.entity_id))
    if not events:
        print("Событий нет.")
    for e in events:
        versions = f" v{e.previous_version}→v{e.new_version}" if e.new_version else ""
        reason = f" — {e.reason}" if e.reason else ""
        who = names.get(e.actor_id, e.actor_id)
        print(f"  {e.at:%Y-%m-%d %H:%M} UTC  {who:<16} {e.action}{versions}{reason}")
    return 0
