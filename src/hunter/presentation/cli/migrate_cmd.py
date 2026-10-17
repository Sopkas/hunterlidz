"""`hunter migrate` — bring the database schema up to date. (S1-07)

Safe to run again: applied migrations are skipped (doc 12, deployment step 3).
"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("migrate", help="применить миграции базы данных")
    parser.set_defaults(handler=handle)


def handle(app, args: argparse.Namespace) -> int:
    revision = app.migrate()
    print(f"Схема базы актуальна: ревизия {revision}, {app.database_display}")
    return 0
