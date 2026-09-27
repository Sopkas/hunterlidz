"""Settings loading and validation. (H-01)

Boundaries: reads environment variables and an optional .env file; no network.
Process environment wins over .env, so CI and shells can override local files.
Invalid or missing required settings raise ConfigError; the entry point turns it into exit code 2
before any network call (doc 13, DoD: configuration is validated at start).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

from hunter.domain.budget import Limits, Pricing


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
    knowledge_dir: Path = Path("documents")
    env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


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
        knowledge_dir=Path(env.get("HUNTER_KNOWLEDGE_DIR", "documents")),
        env=MappingProxyType(env),
    )


def load_from_process(cwd: Path | None = None) -> Settings:
    return load_settings(os.environ, (cwd or Path.cwd()) / ".env")


@dataclass(frozen=True, slots=True)
class LlmSettings:
    provider: str  # gemini | claude
    api_key: str
    model: str
    pricing: Pricing
    limits: Limits


_PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", "HUNTER_GEMINI_MODEL", "gemini-flash-lite-latest"),
    "claude": ("ANTHROPIC_API_KEY", "HUNTER_CLAUDE_MODEL", "claude-haiku-4-5"),
}


def _decimal(env: Mapping[str, str], name: str, *, positive: bool) -> Decimal:
    raw = env.get(name, "").strip()
    if not raw:
        raise ConfigError(f"не задан {name}")
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        raise ConfigError(f"{name}: ожидается число, получено {raw!r}") from None
    if value < 0 or (positive and value == 0):
        raise ConfigError(
            f"{name}: ожидается {'положительное' if positive else 'неотрицательное'} число"
        )
    return value


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        raise ConfigError(f"{name}: ожидается целое число") from None
    if value < 1:
        raise ConfigError(f"{name}: должно быть больше нуля")
    return value


def llm_settings(env: Mapping[str, str]) -> LlmSettings:
    """Doc 12: provider, key, tariff and money caps are mandatory before any paid call.
    A free tier is configured explicitly with prices 0; an unknown tariff is an error."""
    provider = env.get("HUNTER_LLM", "").strip().lower()
    if provider not in _PROVIDERS:
        raise ConfigError("HUNTER_LLM: укажите gemini или claude")
    key_name, model_name, default_model = _PROVIDERS[provider]
    api_key = env.get(key_name, "").strip()
    if not api_key:
        raise ConfigError(f"не задан {key_name} для HUNTER_LLM={provider}")
    currency = env.get("COST_CURRENCY", "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ConfigError("COST_CURRENCY: укажите код валюты учёта, например USD")
    pricing = Pricing(
        input_per_mtok=_decimal(env, "HUNTER_LLM_PRICE_INPUT_PER_MTOK", positive=False),
        output_per_mtok=_decimal(env, "HUNTER_LLM_PRICE_OUTPUT_PER_MTOK", positive=False),
        currency=currency,
    )
    limits = Limits(
        max_llm_calls=_int(env, "RESEARCH_MAX_LLM_CALLS", 3),
        max_input_tokens=_int(env, "RESEARCH_MAX_INPUT_TOKENS", 20_000),
        max_output_tokens=_int(env, "RESEARCH_MAX_OUTPUT_TOKENS", 3_000),
        max_active_seconds=float(_int(env, "RESEARCH_MAX_ACTIVE_SECONDS", 120)),
        run_cost_cap=_decimal(env, "RUN_COST_CAP", positive=True),
        daily_cost_cap=_decimal(env, "DAILY_COST_CAP", positive=True),
    )
    return LlmSettings(
        provider=provider,
        api_key=api_key,
        model=env.get(model_name, "").strip() or default_model,
        pricing=pricing,
        limits=limits,
    )
