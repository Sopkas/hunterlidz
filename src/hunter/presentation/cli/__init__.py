"""Command-line operator interface. (H-01)

Each command group lives in its own module with a register(subparsers) function.
Handlers receive the runtime App and parsed args and return an exit code.
"""

from __future__ import annotations

import argparse

from hunter.presentation.cli import (
    admin_cmd,
    config_cmd,
    leads_cmd,
    research_cmd,
    scoring_cmd,
    sources_cmd,
)

COMMAND_MODULES = (config_cmd, admin_cmd, leads_cmd, sources_cmd, research_cmd, scoring_cmd)


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
