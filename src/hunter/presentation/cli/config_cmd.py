"""`hunter config` — show the effective configuration without secrets. (H-01)"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("config", help="проверить конфигурацию (без секретов)")
    parser.set_defaults(handler=handle)


def handle(app, args: argparse.Namespace) -> int:
    settings = app.settings
    print("Конфигурация в порядке.")
    print(f"  пользователь (HUNTER_ACTOR): {settings.actor}")
    print(f"  база данных:                 {settings.db_path}")
    print(f"  деловой часовой пояс:        UTC{settings.business_tz.tzname(None)[3:] or '+00:00'}")
    return 0
