"""Process entry point. (S1-03)

Exit codes: 0 success, 1 operation refused (HunterError), 2 configuration error.
Configuration is validated before the application is built, so a missing setting
never reaches the network.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from hunter.application.errors import HunterError
from hunter.presentation.cli import build_parser
from hunter.runtime.config import ConfigError, load_from_process
from hunter.runtime.container import build_app

EXIT_REFUSED = 1
EXIT_CONFIG = 2


def _configure_stdio() -> None:
    # Windows consoles and PowerShell pipes: force UTF-8 and drop the BOM PowerShell prepends.
    for stream, encoding in (
        (sys.stdout, "utf-8"),
        (sys.stderr, "utf-8"),
        (sys.stdin, "utf-8-sig"),
    ):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on test capture streams
        if reconfigure:
            reconfigure(encoding=encoding)


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        settings = load_from_process()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        app = build_app(settings)
        return args.handler(app, args) or 0
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except HunterError as exc:
        print(f"Отказано — {exc}", file=sys.stderr)
        return EXIT_REFUSED
