"""Command-line interface. (S1-03)

Each command group lives in its own module with a register(subparsers) function.
Handlers receive the runtime App and parsed args and return an exit code.
"""

from __future__ import annotations

import argparse

from hunter.presentation.cli import config_cmd, llm_cmd, migrate_cmd, whoami_cmd

COMMAND_MODULES = (config_cmd, migrate_cmd, whoami_cmd, llm_cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hunter",
        description=(
            "Hunter v0 — исследование лидов, scoring и черновики с согласованием человеком."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="команда")
    for module in COMMAND_MODULES:
        module.register(subparsers)
    return parser
