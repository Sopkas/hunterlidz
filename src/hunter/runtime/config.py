"""Settings loading and validation. (S1-03)

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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

DATABASE_SCHEMES = frozenset({"postgresql", "postgresql+psycopg"})
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
LLM_KEY = "HUNTER_LLM_API_KEY"


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
    if not 0 <= int(minutes) < 60 or delta > timedelta(hours=14):
        raise ConfigError(f"HUNTER_BUSINESS_UTC_OFFSET вне диапазона: {raw!r}")
    return timezone(sign * delta)


def parse_database_url(raw: str) -> str:
    """Checks the shape only; the URL may hold a password, so it never appears in messages."""
    shape_error = ConfigError(
        "HUNTER_DATABASE_URL: ожидается postgresql://пользователь:пароль@хост:порт/база"
    )
    parts = urlsplit(raw)
    try:
        parts.port  # non-numeric or out-of-range port raises ValueError
    except ValueError:
        raise shape_error from None
    if "@" in parts.path + parts.query + parts.fragment:
        # An unencoded '/', '?' or '#' in the password moved it out of netloc.
        raise ConfigError(
            "HUNTER_DATABASE_URL: закодируйте спецсимволы пароля (/ → %2F, ? → %3F, # → %23)"
        )
    if parts.scheme not in DATABASE_SCHEMES or not parts.hostname or len(parts.path) < 2:
        raise shape_error
    return raw


def safe_database_url(url: str) -> str:
    """URL for display: credentials removed."""
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


@dataclass(frozen=True, slots=True)
class LlmSettings:
    """The model provider (S2-04, ADR-0002). The key is never printed or logged."""

    base_url: str
    model: str
    api_key: str
    structured: str
    timeout_s: float
    price_input_per_million: Decimal | None
    price_output_per_million: Decimal | None

    @property
    def provider(self) -> str:
        host = urlsplit(self.base_url).hostname or ""
        return "openrouter" if host == "openrouter.ai" else host or "openai-compatible"


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    business_tz: timezone
    default_currency: str = "RUB"
    llm: LlmSettings | None = None  # None: no model is called, research stays off


def _price(env: Mapping[str, str], name: str) -> Decimal | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ConfigError(f"{name}: ожидается число, цена за миллион токенов") from None
    if not value.is_finite() or value < 0:
        raise ConfigError(f"{name}: ожидается неотрицательное число")
    return value


def load_llm_settings(
    env: Mapping[str, str], process_env: Mapping[str, str], dotenv: Mapping[str, str]
) -> LlmSettings | None:
    """Owner's rules of 17.10 for the test period: open-source ":free" models on OpenRouter,
    no Gemini, the key only in the process environment (not in .env, files or logs)."""
    model = env.get("HUNTER_LLM_MODEL", "").strip()
    if LLM_KEY in dotenv:
        raise ConfigError(
            f"{LLM_KEY} не хранится в .env: задайте его переменной окружения процесса"
        )
    if not model:
        return None
    if "gemini" in model.lower():
        raise ConfigError("HUNTER_LLM_MODEL: модели Gemini не используем (ADR-0002)")
    base_url = env.get("HUNTER_LLM_BASE_URL", DEFAULT_LLM_BASE_URL).strip().rstrip("/")
    parts = urlsplit(base_url)
    local = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
    if not (parts.scheme == "https" and parts.hostname) and not local:
        raise ConfigError("HUNTER_LLM_BASE_URL: https-адрес или http на 127.0.0.1 / localhost")
    if parts.hostname == "openrouter.ai" and not model.endswith(":free"):
        raise ConfigError(
            "HUNTER_LLM_MODEL: на тестовом периоде только бесплатные модели «…:free» (ADR-0002)"
        )
    api_key = process_env.get(LLM_KEY, "").strip()
    if not api_key and not local:
        raise ConfigError(f"Не задан {LLM_KEY} — ключ провайдера в переменной окружения")
    structured = env.get("HUNTER_LLM_STRUCTURED", "json_schema").strip()
    if structured not in ("json_schema", "json_object"):
        raise ConfigError("HUNTER_LLM_STRUCTURED: json_schema или json_object")
    try:
        timeout_s = float(env.get("HUNTER_LLM_TIMEOUT_S", "60"))
    except ValueError:
        raise ConfigError("HUNTER_LLM_TIMEOUT_S: ожидается число секунд") from None
    if not 1 <= timeout_s <= 600:
        raise ConfigError("HUNTER_LLM_TIMEOUT_S: от 1 до 600 секунд")
    return LlmSettings(
        base_url=base_url,
        model=model,
        api_key=api_key,
        structured=structured,
        timeout_s=timeout_s,
        price_input_per_million=_price(env, "HUNTER_LLM_PRICE_INPUT_PER_M"),
        price_output_per_million=_price(env, "HUNTER_LLM_PRICE_OUTPUT_PER_M"),
    )


def load_settings(environ: Mapping[str, str], dotenv_path: Path) -> Settings:
    dotenv = read_dotenv(dotenv_path)
    env = {**dotenv, **{k: v for k, v in environ.items() if v}}
    database_url = env.get("HUNTER_DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError(
            "Не задан HUNTER_DATABASE_URL — адрес базы PostgreSQL. "
            "Скопируйте .env.example в .env и заполните его."
        )
    currency = env.get("HUNTER_DEFAULT_CURRENCY", "RUB").strip().upper()
    # isascii: «руб» is three letters too, and would split money metrics (review A on #9).
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ConfigError(f"HUNTER_DEFAULT_CURRENCY: ожидается код ISO 4217, получено {currency!r}")
    return Settings(
        database_url=parse_database_url(database_url),
        business_tz=parse_utc_offset(env.get("HUNTER_BUSINESS_UTC_OFFSET", "+03:00")),
        default_currency=currency,
        llm=load_llm_settings(env, environ, dotenv),
    )


def load_from_process(cwd: Path | None = None) -> Settings:
    return load_settings(os.environ, (cwd or Path.cwd()) / ".env")
