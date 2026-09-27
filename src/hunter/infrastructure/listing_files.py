"""Read marketplace exports (CSV or JSON) into rows with canonical field names. (H-03)

Doc "Как мы работаем", uploaded files: type and size are checked before reading, and
nothing is evaluated from the file (no formulas, no code). Column names differ between
platforms, so each canonical field accepts a list of aliases.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from hunter.application.errors import ErrorCode, HunterError

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2_000

ALIASES: dict[str, tuple[str, ...]] = {
    "ext_id": ("id", "ext_id", "listing_id", "ad_id"),
    "title": ("title", "name", "заголовок"),
    "body": ("description", "body", "text", "desc", "описание"),
    "company": ("company", "company_name", "employer", "author", "компания"),
    "website": ("website", "site", "company_url", "сайт"),
    "url": ("url", "link", "source_url", "ссылка"),
    "budget": ("budget", "price", "бюджет"),
    "posted_at": ("posted_at", "date", "published_at", "created_at", "дата"),
    "contact": ("contact", "email", "telegram", "phone", "контакт"),
    "contact_name": ("contact_name", "contact_person", "контактное_лицо"),
    "platform": ("platform", "source", "площадка"),
}


def read_listing_rows(path: Path) -> list[dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".json"):
        raise HunterError(ErrorCode.INVALID_INPUT, "поддерживаются только файлы .csv и .json")
    if not path.is_file():
        raise HunterError(ErrorCode.NOT_FOUND, f"файл не найден: {path.name}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise HunterError(ErrorCode.INVALID_INPUT, "файл больше 5 МБ")
    try:
        rows = _read_csv(path) if suffix == ".csv" else _read_json(path)
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
        raise HunterError(ErrorCode.INVALID_INPUT, f"файл не читается: {type(exc).__name__}")
    if len(rows) > MAX_ROWS:
        raise HunterError(ErrorCode.INVALID_INPUT, f"больше {MAX_ROWS} строк в одном импорте")
    return [canonical(row) for row in rows]


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):  # {"items": [...]} and similar wrappers
        data = next((v for v in data.values() if isinstance(v, list)), [])
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise HunterError(ErrorCode.INVALID_INPUT, "JSON должен быть списком объектов")
    return data


def canonical(row: dict) -> dict[str, object]:
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    result: dict[str, object] = {}
    for field, names in ALIASES.items():
        for name in names:
            value = lowered.get(name)
            if value not in (None, ""):
                result[field] = value
                break
    return result
