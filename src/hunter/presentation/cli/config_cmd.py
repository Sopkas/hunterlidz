"""`hunter config` — show the effective configuration without secrets. (S1-03)"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("config", help="проверить конфигурацию (без секретов)")
    parser.set_defaults(handler=handle)


def handle(app, args: argparse.Namespace) -> int:
    settings = app.settings
    offset = settings.business_tz.tzname(None)[3:] or "+00:00"
    print("Конфигурация в порядке.")
    print(f"  база данных:          {app.database_display}")
    print(f"  деловой часовой пояс: UTC{offset}")
    print(f"  валюта по умолчанию:  {settings.default_currency}")
    return 0
