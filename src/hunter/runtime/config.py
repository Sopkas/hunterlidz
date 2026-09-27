"""Settings loading and validation. (H-01)

Boundaries: reads environment variables and an optional .env file; no network.
Process environment wins over .env, so CI and shells can override local files.
Invalid or missing required settings raise ConfigError; the entry point turns it into exit code 2
before any network call (doc 13, DoD: configuration is validated at start).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path


class ConfigError(Exception):
    """Configuration is missing or invalid; message is safe to show to the user."""


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=value lines; comments and blank values are skipped."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.strip().partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if sep and name and not name.startswith("#") and value:
            values[name] = value
    return values


def parse_utc_offset(raw: str) -> timezone:
    """'+03:00' -> fixed offset. A fixed offset avoids a tzdata dependency on Windows."""
    sign = -1 if raw.startswith("-") else 1
    try:
        hours, minutes = raw.lstrip("+-").split(":")
        delta = timedelta(hours=int(hours), minutes=int(minutes))
    except ValueError:
        raise ConfigError(f"HUNTER_BUSINESS_UTC_OFFSET: ожидается формат +03:00, получено {raw!r}")
    if delta > timedelta(hours=14):
        raise ConfigError(f"HUNTER_BUSINESS_UTC_OFFSET вне диапазона: {raw!r}")
    return timezone(sign * delta)


@dataclass(frozen=True, slots=True)
class Settings:
    actor: str
    db_path: Path
    business_tz: timezone
    default_currency: str = "RUB"


def load_settings(environ: Mapping[str, str], dotenv_path: Path) -> Settings:
    env = {**read_dotenv(dotenv_path), **{k: v for k, v in environ.items() if v}}
    actor = env.get("HUNTER_ACTOR", "").strip()
    if not actor:
        raise ConfigError(
            "Не задан HUNTER_ACTOR — имя пользователя Hunter, от которого выполняются действия. "
            "Скопируйте .env.example в .env и заполните его."
        )
    currency = env.get("HUNTER_DEFAULT_CURRENCY", "RUB").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ConfigError(f"HUNTER_DEFAULT_CURRENCY: ожидается код ISO 4217, получено {currency!r}")
    return Settings(
        actor=actor,
        db_path=Path(env.get("HUNTER_DB", "hunter.db")),
        business_tz=parse_utc_offset(env.get("HUNTER_BUSINESS_UTC_OFFSET", "+03:00")),
        default_currency=currency,
    )


def load_from_process(cwd: Path | None = None) -> Settings:
    return load_settings(os.environ, (cwd or Path.cwd()) / ".env")
