"""`hunter whoami` — the OS account Hunter acts for. (S1-04)

Environment variables do not change the answer; that is the manual check for T-10.
"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "whoami", help="учётная запись ОС, от имени которой действует Hunter"
    )
    parser.set_defaults(handler=handle)


def handle(app, args: argparse.Namespace) -> int:
    print(f"Учётная запись: {app.identity.current_identity()}")
    return 0
